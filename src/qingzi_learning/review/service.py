"""Parent review application service; called only on the repository owner thread."""
from dataclasses import dataclass, replace
import json
from pathlib import Path

from qingzi_learning.export.dashboard import DashboardExporter
from qingzi_learning.export.markdown import MarkdownExporter
from qingzi_learning.export.publication import PublicationCoordinator
from qingzi_learning.export.safe_write import _guard, atomic_write
from qingzi_learning.knowledge.updater import KnowledgeUpdater, UpdateSummary
from qingzi_learning.storage.repository import KnowledgeRepository


@dataclass(frozen=True)
class ReviewItem:
    document_id: str
    question_id: str
    subject: str
    page: int
    source_path: Path
    prompt_summary: str
    student_answer: str
    reference_answer: str
    reason: str
    confidence: float
    status: str
    decision_source: str
    original_status: str
    original_decision_source: str
    note: str
    version: str


class ReviewService:
    def __init__(self, repo: KnowledgeRepository, *, updater=None, markdown=None, dashboard=None):
        self.repo = repo
        self.updater = updater or KnowledgeUpdater(repo)
        self.markdown = markdown or MarkdownExporter(repo)
        self.dashboard = dashboard or DashboardExporter(repo)
        self.publication = PublicationCoordinator(repo)

    def get_question(self, document_id: str, question_id: str) -> ReviewItem:
        row = self.repo.review_question(document_id, question_id)
        return ReviewItem(*(row[key] for key in ("document_id", "question_id", "subject", "page")),
                          Path(row["source_path"]), *(row[key] for key in (
                              "prompt_summary", "student_answer", "reference_answer", "reason", "confidence", "status",
                              "decision_source", "original_status", "original_decision_source")),
                          row["review_note"] or "", row["version"])

    def list_pending(self) -> tuple[ReviewItem, ...]:
        return tuple(self.get_question(*identity) for identity in self.repo.pending_review_ids())

    def history(self, document_id: str, question_id: str):
        return self.repo.review_history(document_id, question_id)

    def confirm_question(self, document_id: str, question_id: str, final_status: str,
                         corrected_answer: str, note: str, *, expected_version: str | None = None) -> UpdateSummary:
        summary = self.updater.confirm_review(document_id, question_id, final_status, corrected_answer, note,
                                              expected_version=expected_version)
        self.retry_publication(document_id)
        return summary

    def pending_publications(self) -> tuple[str, ...]:
        return self.repo.pending_review_publications()

    def retry_publication(self, document_id: str) -> bool:
        expected = None
        revision = None
        def render():
            document = self.repo.get_document(document_id)
            analysis = self.markdown.export_document(document_id)
            self.markdown.export_subject(document["subject"])
            dashboard = self.dashboard.export()
            return dict(analysis_markdown=str(analysis), dashboard_path=str(dashboard))
        def finalize(paths):
            self.repo._set_review_job_state(document_id, expected.state, False, **paths)
            self.repo.connection.execute("UPDATE review_publications SET pending=0 WHERE document_id=?", (document_id,))
            self._mirror(document_id)
            self._refresh_split_parent(document_id, paths["dashboard_path"])
        try:
            expected = self.repo.prepare_review_publication(document_id, files_current=self.publication.current())
            if expected is None:
                return True
            # The pending outbox revision and attempt UUID were captured in the
            # same transaction as this snapshot. Never adopt a later revision.
            revision = expected.payload["parent_publication_revision"] * 2
            published = self.publication.publish(expected, render, finalize, review_revision=revision)
            if not published:
                self.repo.mark_review_export_failed(document_id, expected=expected,
                                                     review_revision=revision,
                                                     before_commit=lambda: self._mirror(document_id))
            return published
        except (OSError, ValueError, RuntimeError):
            try:
                if expected is not None:
                    self.repo.mark_review_export_failed(document_id, expected=expected,
                                                         review_revision=revision,
                                                         before_commit=lambda: self._mirror(document_id))
            except (OSError, ValueError, RuntimeError):
                pass  # Authoritative SQLite outbox still records exactly what to retry.
            return False

    def _refresh_split_parent(self, document_id: str, dashboard_path: str) -> None:
        """Update orchestration only, inside the child's publication transaction.

        Split parents have no document/processing row, so this aggregate does not
        change any reading fact rendered before PublicationCoordinator finalized.
        """
        child = self.repo.get_job(document_id)
        if child is None or not child.payload.get("parent_job_id"):
            return
        parent = self.repo.get_job(child.payload["parent_job_id"])
        if parent is None or document_id not in parent.payload.get("child_document_ids", ()):
            raise ValueError("拆分父任务与子任务不一致")
        children = [self.repo.get_job(c) for c in parent.payload["child_document_ids"]]
        if any(c is None for c in children):
            raise ValueError("拆分子任务缺失")
        state = ("needs_review" if any(c.state == "needs_review" for c in children)
                 else "completed" if all(c.state == "completed" for c in children) else "pending")
        if state != parent.state:
            if parent.state != "pending":
                self.repo._write_workflow_job(replace(parent, state="pending"))
            if state != "pending":
                self.repo._write_workflow_job(replace(parent, state="analyzing"))
        self.repo._write_workflow_job(replace(parent, state=state, payload=dict(
            parent.payload, dashboard_path=dashboard_path,
        )))
        self._mirror(parent.job_id)

    def _mirror(self, document_id: str) -> None:
        job = self.repo.get_job(document_id)
        if job is None:
            return
        payload = json.dumps(dict(version=1, document_id=document_id, state=job.state, error_code=job.last_error))
        if job.payload.get("parent_job_id"):
            year, month = job.payload["date"].split("-")
            directory = (self.repo.config.knowledge_root.absolute() / job.subject / "原始资料"
                         / year / month / document_id)
            destination = Path(job.payload["mirror_path"])
            _guard(directory, self.repo.config.knowledge_root.absolute())
            _guard(destination, self.repo.config.knowledge_root.absolute())
            if destination.absolute() != directory / "analysis_state.json":
                raise ValueError("拆分子文档镜像路径无效")
            atomic_write(destination, payload, self.repo.config.knowledge_root)
            return
        directory = Path(job.payload["session_dir"])
        atomic_write(directory / "analysis_state.json", payload, self.repo.config.spool_root)
        if job.payload.get("archive_kind"):
            archive = Path(job.payload["pages"][0]["path"]).parent
            atomic_write(archive / "analysis_state.json", payload, self.repo.config.knowledge_root)
