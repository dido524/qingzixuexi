"""SQLite-led workflow with verified copies and replayable publication."""

from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import tempfile
import threading
from uuid import uuid4

from qingzi_learning.analysis.codex_cli import _from_payload
from qingzi_learning.analysis.service import AnalysisService
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig
from qingzi_learning.domain import CapturedDocument, CapturedPage, Subject
from qingzi_learning.export.dashboard import DashboardExporter
from qingzi_learning.export.markdown import MarkdownExporter
from qingzi_learning.export.publication import PublicationCoordinator
from qingzi_learning.export.safe_write import _guard, atomic_write
from qingzi_learning.grading.annotation import ANNOTATION_LAYOUT_VERSION, AnnotationRenderer
from qingzi_learning.knowledge.updater import KnowledgeUpdater
from qingzi_learning.review.service import ReviewService
from qingzi_learning.reporting.service import LearningReportService
from qingzi_learning.reporting.narrative import LocalNarrativeProvider, NarrativeService
from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.exams.service import TargetedExamService
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import KnowledgeRepository, WorkflowJob
from qingzi_learning.workflow.subject_split import (
    PendingPageSubject,
    SubjectSplitPlan,
    build_subject_split_plan,
    split_analysis,
)


@dataclass(frozen=True)
class WorkflowOutcome:
    job_id: str
    state: str
    subject: str | None
    archived_pages: tuple[Path, ...] = ()
    analysis_markdown: Path | None = None
    dashboard_path: Path | None = None
    summary: str = ""
    error_code: str | None = None
    recovery_path: Path | None = None
    pending_page_subjects: tuple[PendingPageSubject, ...] = ()
    child_document_ids: tuple[str, ...] = ()
    page_subject_index: int = 0
    page_subject_total: int = 0
    annotated_pages: tuple[Path, ...] = ()
    grading_gallery_path: Path | None = None
    parent_job_id: str | None = None


class _WorkflowSuperseded(Exception):
    """A newer durable owner replaced the snapshot this worker observed."""


class WorkflowController:
    """Own one worker's repository; callers serialize work through this controller.

    workflow_jobs is authoritative. analysis_state.json is only a repairable mirror.
    A terminal state with export_pending still needs replay before being returned.
    """

    def __init__(self, config: AppConfig, analyzer, repo: KnowledgeRepository,
                 updater=None, markdown=None, dashboard=None, now=None,
                 narrative_provider=None, exam_generator=None, exam_verifier=None) -> None:
        self.config, self.analyzer, self.repo = config, analyzer, repo
        self.service = AnalysisService(analyzer)
        self.paths = KnowledgePaths(config)
        self.updater = updater or KnowledgeUpdater(repo)
        self.markdown = markdown or MarkdownExporter(repo, self.paths)
        self.dashboard = dashboard or DashboardExporter(repo, self.paths)
        self.annotations = AnnotationRenderer(config.knowledge_root)
        self.review = ReviewService(repo, updater=self.updater, markdown=self.markdown, dashboard=self.dashboard)
        narrative_service = (
            NarrativeService(narrative_provider, LocalNarrativeProvider())
            if narrative_provider is not None else None
        )
        self.reports = LearningReportService(repo, narrative_service=narrative_service)
        self.exams = TargetedExamService(
            repo, generator=exam_generator, verifier=exam_verifier
        )
        self.publication = PublicationCoordinator(repo)
        self._now = now or (lambda: datetime.now(timezone.utc))
        self._lock = threading.RLock()

    def generate_learning_report(self):
        with self._lock:
            artifacts = self.reports.generate()
            # Refresh the shared homepage only after the report is durably complete.
            self.dashboard.export()
            return artifacts

    def report_history(self):
        with self._lock:
            return self.reports.history()

    def preview_exam_blueprint(self, request: ExamRequest):
        with self._lock:
            return self.exams.preview_blueprint(request)

    def generate_exam(self, request: ExamRequest):
        with self._lock:
            return self.exams.create_draft(request)

    def approve_exam(self, exam_id: str, expected_revision: int):
        with self._lock:
            artifacts = self.exams.approve(exam_id, expected_revision=expected_revision)
            self.dashboard.export()
            return artifacts

    def exam_history(self):
        with self._lock:
            return self.exams.history()

    def finish_and_analyze(self, session: CaptureSession) -> WorkflowOutcome:
        with self._lock:
            session_dir = Path(session.session_dir).absolute()
            self._spool_guard(session_dir)
            job = self.repo.get_job(session_dir.name)
            if job is not None:
                if job.payload["session_dir"] != str(session_dir):
                    raise ValueError("会话标识冲突")
                return self._resume(job)
            # Recover from durable bytes rather than trusting in-memory capture metadata.
            durable = CaptureSession.recover(session_dir)
            if session.pages != durable.pages or session.current_page_number != durable.current_page_number:
                raise ValueError("拍摄会话已变化，请重新恢复")
            if not session.finished:
                session.finish()
                durable = CaptureSession.recover(session_dir)
            job = self._stage(durable)
            return self._resume(job)

    def confirm_subject(self, job_id: str, subject: str) -> WorkflowOutcome:
        with self._lock:
            if subject not in self.config.subjects:
                raise ValueError("非法科目")
            job = self._require_job(job_id)
            if job.payload.get("confirmation_mode") == "pages":
                raise ValueError("当前任务等待页面科目确认")
            if job.state != "needs_subject_confirmation":
                if job.subject == subject:
                    return self._resume(job)
                raise ValueError("当前任务不等待科目确认")
            payload = dict(job.payload, subject_confirmed=True,
                           confirmation_mode="legacy_batch")
            if payload.get("analysis"):
                payload["analysis"] = dict(payload["analysis"], subject=subject)
            job = self._save(replace(job, subject=subject, state="pending", payload=payload))
            # An unsuccessful analysis is archived as pending without an implicit retry.
            if not payload.get("analysis"):
                return self._archive_failed(job)
            return self._resume(job)

    def confirm_page_subject(self, job_id: str, page_number: int, subject: str) -> WorkflowOutcome:
        with self._lock:
            job = self._require_job(job_id)
            if (job.state != "needs_subject_confirmation"
                    or job.payload.get("confirmation_mode") != "pages"):
                raise ValueError("当前任务不等待页面科目确认")
            parsed = Subject(subject)
            observed = job
            try:
                while True:
                    if (job.state != "needs_subject_confirmation" or job.knowledge_applied
                            or job.payload.get("confirmation_mode") != "pages"
                            or job.payload.get("child_document_ids")
                            or job.payload.get("analysis") != observed.payload.get("analysis")
                            or job.payload.get("pages") != observed.payload.get("pages")):
                        return self._outcome(job)
                    pending = {item.page for item in self._page_split_plan(job).unresolved_pages}
                    if page_number not in pending:
                        if job != observed:
                            return self._outcome(job)
                        raise ValueError("该页面当前不等待确认")
                    # Eligibility and this entire payload are compared under the
                    # repository's BEGIN IMMEDIATE lock. On contention, merge only
                    # a still-pending page into the newly read authoritative payload.
                    overrides = dict(job.payload.get("page_subject_overrides", {}))
                    overrides[str(page_number)] = parsed.value
                    saved = replace(job, payload=dict(job.payload, page_subject_overrides=overrides))
                    if self.repo.save_workflow_job_if_current(job, saved, self._mirror):
                        job = saved
                        break
                    job = self._require_job(job_id)
                plan = self._page_split_plan(saved)
                payload = self._with_split_plan(saved.payload, plan)
                if plan.unresolved_pages:
                    return self._outcome(self._save_if_current(saved, replace(saved, payload=payload)))
                saved = self._save_if_current(saved, replace(saved, state="pending", payload=payload))
                return self._resume(saved)
            except _WorkflowSuperseded:
                return self._outcome(self._require_job(job_id))
            except (OSError, ValueError):
                # A newer completion may also remove the old spool images while
                # this confirmation is verifying them. It still owns the result.
                if not self.repo.workflow_job_matches(job):
                    return self._outcome(self._require_job(job_id))
                raise

    def retry_pending(self, job_id: str) -> WorkflowOutcome:
        with self._lock:
            return self._resume(self._require_job(job_id))

    def cancel(self) -> None:
        """Forward a UI shutdown request to an analyzer that supports cancellation.

        The worker remains alive until its current durable operation returns; this
        hook lets cancellable adapters finish that bounded shutdown sooner without
        permitting a second thread to use this controller or its SQLite connection.
        """
        cancel = getattr(self.analyzer, "cancel", None)
        if callable(cancel):
            cancel()

    def recover_jobs(self) -> list[WorkflowOutcome]:
        """Discover interrupted work, reconcile mirrors, and offer explicit retries.

        No model call or knowledge mutation occurs just because the app starts.
        Unfinished captures remain available to CaptureSession.recover separately.
        """
        with self._lock:
            outcomes: dict[str, WorkflowOutcome] = {}
            root = self.config.spool_root.absolute()
            try:
                self._spool_guard(root)
                directories = sorted(root.iterdir()) if root.exists() else []
            except Exception:
                # Recovery is a batch boundary: a broken item must not hide siblings.
                directories = []
                outcomes[""] = self._recovery_error("", root)
            for directory in directories:
                try:
                    self._spool_guard(directory)
                    if not directory.is_dir():
                        continue
                    if self.repo.get_job(directory.name) is None:
                        capture = CaptureSession.recover(directory)
                        if capture.finished:
                            self._stage(capture)
                except Exception:
                    outcomes[directory.name] = self._recovery_error(directory.name, directory)
            try:
                job_ids = self.repo.list_workflow_job_ids()
            except Exception:
                outcomes[""] = self._recovery_error("", self.config.app_data_root.absolute())
                return list(outcomes.values())
            latest_root_job = None
            for job_id in job_ids:
                try:
                    job = self._require_job(job_id)
                    corrupt_publication = (job.knowledge_applied and job.state in {"completed", "needs_review"}
                                           and not self.publication.current())
                    if job.state in {"spooled", "analyzing"} or job.payload.get("export_pending") or corrupt_publication:
                        candidate = replace(job, state="pending")
                    elif self._is_legacy_confirmation(job):
                        self._analysis_from_payload(job.payload["analysis"])
                        candidate = replace(job, payload=dict(
                            job.payload, confirmation_mode="legacy_batch",
                        ))
                    else:
                        candidate = job
                    if self.repo.save_workflow_job_if_current(job, candidate, self._mirror):
                        job = candidate
                    else:
                        job = self._require_job(job_id)
                    if not job.payload.get("parent_job_id"):
                        latest_root_job = job
                    # A transient discovery-side mirror failure may already be repaired.
                    outcomes.pop(job_id, None)
                    cleanup_pending = (job.payload.get("child_document_ids")
                                       and job.payload.get("publish_state") == "completed"
                                       and not job.payload.get("cleanup_completed"))
                    if job.state not in {"completed", "capturing"} or job.payload.get("cleanup") or cleanup_pending:
                        outcomes[job_id] = self._outcome(job)
                except Exception:
                    latest_root_job = None
                    directory = (root / job_id if re.fullmatch(
                        r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", job_id) else root)
                    outcomes[job_id] = self._recovery_error(job_id, directory)
            if (self.config.review_all_model_questions and latest_root_job is not None
                    and latest_root_job.state == "completed"):
                # Surface only the newest completed capture from the older app.
                # Its model-correct questions were saved before the all-question
                # review policy and would otherwise be invisible after restart.
                try:
                    document_ids = (tuple(latest_root_job.payload.get("child_document_ids", ()))
                                    or (latest_root_job.job_id,))
                    if self.repo.pending_review_ids(document_ids):
                        outcomes.setdefault(latest_root_job.job_id, self._outcome(latest_root_job))
                except Exception:
                    outcomes[latest_root_job.job_id] = self._recovery_error(
                        latest_root_job.job_id, root / latest_root_job.job_id)
            ordered = [outcomes[job_id] for job_id in job_ids if job_id in outcomes]
            ordered.extend(outcome for job_id, outcome in outcomes.items() if job_id not in job_ids)
            return ordered

    @staticmethod
    def _recovery_error(job_id: str, path: Path) -> WorkflowOutcome:
        # Do not manufacture a subject, page manifest, or database record from damage.
        return WorkflowOutcome(job_id, "pending", None, error_code="recovery_failed",
                               summary="恢复失败，原始资料已保留，请检查恢复位置。",
                               recovery_path=path)

    def _stage(self, capture: CaptureSession) -> WorkflowJob:
        directory = capture.session_dir.absolute()
        self._spool_guard(directory)
        if not capture.pages:
            raise ValueError("至少需要一张已拍摄页面")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", directory.name):
            raise ValueError("非法拍摄会话标识")
        assets = []
        for page in capture.pages:
            self._spool_guard(page.path)
            assets.append({"relative": page.path.name, "source": str(page.path),
                           "sha256": page.sha256, "cleanup": True})
        audit = directory / "discarded"
        self._spool_guard(audit)
        if audit.exists():
            for file in sorted(audit.rglob("*")):
                self._spool_guard(file)
                if file.is_file():
                    assets.append({"relative": file.relative_to(directory).as_posix(),
                                   "source": str(file), "sha256": self._digest(file), "cleanup": True})
        metadata = directory / "session.json"
        assets.append({"relative": "session.json", "source": str(metadata),
                       "sha256": self._digest(metadata), "cleanup": False})
        payload = {
            "version": 1, "session_dir": str(directory), "date": self._now().strftime("%Y-%m"),
            "pages": [{"page_number": p.page_number, "path": str(p.path), "sha256": p.sha256}
                      for p in capture.pages],
            "assets": assets, "cleanup": [], "archive_kind": None,
            "analysis": None, "subject_confirmed": False, "export_pending": False,
            "confirmation_mode": None, "page_subject_overrides": {}, "split_plan": None,
            "child_document_ids": [], "publish_state": "not_started",
        }
        return self._save(WorkflowJob(directory.name, "spooled", None, False, None, payload))

    def _resume(self, job: WorkflowJob) -> WorkflowOutcome:
        if job.payload.get("parent_review") or self.repo.review_publication_revision(job.job_id):
            # Parent decisions are authoritative effective facts. Their outbox
            # replays only publication, never the cached model's old judgments.
            if job.payload.get("parent_job_id"):
                try:
                    parent = self._require_job(job.payload["parent_job_id"])
                    children = [self._require_job(c) for c in parent.payload["child_document_ids"]]
                    self._verify_split_manifests(parent)
                    for child in children:
                        self._verify_split_child(child)
                    self._split_cleanup_items(parent, children)
                except (OSError, ValueError, RuntimeError):
                    return self._outcome(replace(job, state="pending", last_error="workflow_failed"))
            self.review.retry_publication(job.job_id)
            return self._outcome(self._require_job(job.job_id))
        publication_job = None
        try:
            if job.payload.get("parent_job_id"):
                parent = self._require_job(job.payload["parent_job_id"])
                result = self._publish_split(parent)
                child = self._require_job(job.job_id)
                # A failed parent validation is also a failed child retry, but
                # must not independently rewrite that child's publication owner.
                if result.error_code:
                    child = replace(child, state="pending", last_error=result.error_code)
                return self._outcome(child)
            if job.payload.get("child_document_ids") and job.knowledge_applied:
                return self._publish_split(job)
            if job.state in {"completed", "needs_review"} and not job.payload.get("export_pending") and not self.publication.current():
                job = self._save_if_current(job, replace(job, state="pending", payload=dict(job.payload, export_pending=True)))
            if job.state == "needs_subject_confirmation":
                if job.payload.get("confirmation_mode") == "pages":
                    plan = self._page_split_plan(job)
                    if not plan.unresolved_pages:
                        job = self._save_if_current(job, replace(
                            job, state="pending",
                            payload=self._with_split_plan(job.payload, plan),
                        ))
                if job.state == "needs_subject_confirmation":
                    if self._is_legacy_confirmation(job):
                        # Only cached pre-page-mapping analyses take the compatibility
                        # path; fresh model responses have already been strictly checked.
                        self._analysis_from_payload(job.payload["analysis"])
                        job = self._save_if_current(job, replace(
                            job, payload=dict(job.payload, confirmation_mode="legacy_batch"),
                        ))
                    self._save_if_current(job, job)
                    return self._outcome(self._require_job(job.job_id))
            if job.state in {"completed", "needs_review"} and not job.payload.get("export_pending"):
                self._save_if_current(job, job)
                return self._outcome(self._require_job(job.job_id))
            if job.state in {"completed", "needs_review"} and job.payload.get("export_pending"):
                # Direct Retry may run before a startup scan. Enter a legal replay
                # state first; cached analysis and applied facts remain authoritative.
                job = self._save_if_current(job, replace(job, state="pending"))
            if job.payload.get("analysis") is None:
                job = self._save_if_current(job, replace(job, state="analyzing", last_error=None))
                document = self._document(job)
                for page in document.pages:
                    self._source_guard(page.path)
                    if self._digest(page.path) != page.sha256:
                        raise ValueError("分析页面哈希不匹配")
                result = self.service.analyze_or_queue(document)
                payload = dict(job.payload)
                payload["analysis"] = (
                    json.loads(json.dumps(asdict(result.analysis), ensure_ascii=False, allow_nan=False))
                    if result.analysis else None
                )
                if result.analysis is None:
                    job = self._save_if_current(job, replace(job, state="pending" if job.subject else
                                            "needs_subject_confirmation", payload=payload,
                                            last_error=result.error_code or "analysis_failed"))
                    return self._archive_failed(job) if job.subject else self._outcome(job)
                if result.analysis.page_subjects:
                    plan = build_subject_split_plan(
                        document, result.analysis, {}, self.config.subject_confidence_threshold,
                    )
                    payload = self._with_split_plan(payload, plan)
                    job = self._save_if_current(job, replace(
                        job, subject=None,
                        state="needs_subject_confirmation" if plan.unresolved_pages else "pending",
                        payload=dict(payload, confirmation_mode="pages"), last_error=None,
                    ))
                    return self._resume(job)
                subject = job.subject
                if not payload["subject_confirmed"]:
                    subject = (result.analysis.subject.value if
                               result.analysis.subject_confidence >= self.config.subject_confidence_threshold
                               else None)
                if subject:
                    payload["analysis"] = dict(payload["analysis"], subject=subject)
                job = self._save_if_current(job, replace(job, subject=subject, payload=payload,
                                        state="analyzing" if subject else "needs_subject_confirmation",
                                        last_error=None))
                if subject is None:
                    return self._outcome(job)
            if job.payload.get("confirmation_mode") == "pages":
                plan = self._page_split_plan(job)
                payload = self._with_split_plan(job.payload, plan)
                if plan.unresolved_pages:
                    saved = self._save_if_current(job, replace(
                        job, subject=None, state="needs_subject_confirmation", payload=payload,
                    ))
                    return self._outcome(saved)
                if len(plan.groups) != 1:
                    saved = self._save_if_current(job, replace(
                        job, subject=None, state="pending", payload=payload,
                    ))
                    return self._apply_split(saved, plan)
                subject = plan.groups[0].subject.value
                payload = dict(
                    payload,
                    analysis=dict(payload["analysis"], subject=subject),
                    confirmation_mode=None,
                )
                job = self._save_if_current(job, replace(
                    job, subject=subject, state="analyzing", payload=payload, last_error=None,
                ))
            job = self._save_if_current(job, replace(job, state="analyzing", last_error=None))
            job = self._archive(job, "raw")
            analysis = self._analysis_from_payload(job.payload["analysis"])
            job = self._ensure_annotations(job, analysis)
            if not job.knowledge_applied:
                # Pages exist at durable archive paths before questions are committed.
                self.repo.save_analysis(analysis)
                self.updater.apply(analysis)
                job = self._require_job(job.job_id)
            terminal = "needs_review" if self.repo.document_review_count(job.job_id) else "completed"
            candidate = replace(job, state=terminal, payload=dict(
                job.payload, export_pending=True, ordinary_publication_id=uuid4().hex))
            job = self._save_if_current(job, candidate)
            publication_job = job
            def render():
                analysis_path = self.markdown.export_document(job.job_id)
                self.markdown.export_subject(job.subject)
                dashboard_path = self.dashboard.export()
                return dict(analysis_markdown=str(analysis_path), dashboard_path=str(dashboard_path))
            def finalize(paths):
                completed = replace(publication_job, payload=dict(publication_job.payload, export_pending=False, **paths))
                self.repo._write_workflow_job(completed)
                self._mirror(completed)
            if not self.publication.publish(publication_job, render, finalize):
                self.repo.save_workflow_job_if_current(publication_job, replace(
                    publication_job, state="pending", last_error="publication_changed"), self._mirror)
            return self._outcome(self._require_job(job.job_id))
        except _WorkflowSuperseded:
            return self._outcome(self._require_job(job.job_id))
        except (OSError, ValueError, RuntimeError):
            # Never store arbitrary exception text (may contain model output or secrets).
            expected = publication_job
            if expected is None:
                expected = self._require_job(job.job_id)
                if expected.payload.get("parent_review") or self.repo.review_publication_revision(job.job_id):
                    return self._outcome(self._require_job(job.job_id))
            failed = replace(expected, state="pending", last_error="workflow_failed")
            self.repo.save_workflow_job_if_current(expected, failed, self._mirror)
            return self._outcome(self._require_job(job.job_id))

    def _archive_failed(self, job: WorkflowJob) -> WorkflowOutcome:
        try:
            job = self._archive(job, "pending")
        except (OSError, ValueError, RuntimeError):
            job = self._save(replace(self._require_job(job.job_id), state="pending",
                                     last_error="archive_failed"))
        return self._outcome(job)

    def _apply_split(self, job: WorkflowJob, plan: SubjectSplitPlan) -> WorkflowOutcome:
        analyses = split_analysis(self._analysis_from_payload(job.payload["analysis"]), plan)
        documents = self._stage_split_files(job, plan)
        children = []
        for document, analysis in zip(documents, analyses):
            payload = dict(
                version=1, parent_job_id=job.job_id, session_dir=job.payload["session_dir"],
                date=job.payload["date"], archive_kind="raw", cleanup=[],
                pages=[dict(page_number=p.page_number, path=str(p.path), sha256=p.sha256)
                       for p in document.pages],
                assets=[dict(relative=p.path.name, source=str(p.path), sha256=p.sha256, cleanup=False)
                        for p in document.pages],
                analysis=json.loads(json.dumps(asdict(analysis), ensure_ascii=False, allow_nan=False)),
                subject_confirmed=True, confirmation_mode=None, export_pending=True,
                publish_state="not_started", child_document_ids=[],
                mirror_path=str(document.session_dir / "analysis_state.json"),
            )
            if any(question.answer_bbox is not None for question in analysis.questions):
                artifacts = self.annotations.render(document, analysis)
                payload.update(
                    annotated_pages=[str(path) for path in artifacts.pages],
                    grading_gallery_path=str(artifacts.gallery),
                    annotation_layout_version=ANNOTATION_LAYOUT_VERSION,
                )
            child = WorkflowJob(document.document_id, "pending", analysis.subject.value, False, None, payload)
            children.append((child, document, analysis))
        if job.payload.get("archive_kind") == "pending":
            # An interrupted failed-archive cleanup may still point spool ->
            # pending. Rebind page cleanup to the verified final child copies;
            # neither source is removed until the split publication succeeds.
            cleanup = self._split_cleanup_items(job, [child for child, _, _ in children])
            job = self._save_if_current(job, replace(job, payload=dict(job.payload, cleanup=cleanup)))
        self.updater.apply_split_batch(job, tuple(children), now=self._now())
        saved = self._require_job(job.job_id)
        self._save_if_current(saved, saved)
        for child, _, _ in children:
            persisted = self._require_job(child.job_id)
            self._save_if_current(persisted, persisted)
        return self._publish_split(saved)

    def _publish_split(self, parent: WorkflowJob) -> WorkflowOutcome:
        """One durable owner publishes every child, then releases verified spool pages."""
        expected = parent
        candidates = []
        owned_parent, owned_children = parent, []
        owned_child_ids, review_revisions = set(), {}
        try:
            children = [self._require_job(c) for c in parent.payload["child_document_ids"]]
            self._verify_split_manifests(parent)
            for child in children:
                self._verify_split_child(child)
            # A parent's retry may repair a review export, but only its review
            # owner may acknowledge that outbox. Ordinary publication never
            # takes over a child once parent review owns its effective facts.
            for child in children:
                if child.payload.get("parent_review") or self.repo.review_publication_revision(child.job_id):
                    if not self.review.retry_publication(child.job_id):
                        return self._outcome(replace(
                            self._require_job(parent.job_id), state="pending", last_error="review_export_failed",
                        ))
            parent = self._require_job(parent.job_id)
            children = [self._require_job(c) for c in parent.payload["child_document_ids"]]
            owned_parent, owned_children = parent, children
            review_revisions = {c.job_id: self.repo.review_publication_revision(c.job_id) for c in children}
            reviewed_ids = {c.job_id for c in children if c.payload.get("parent_review") or review_revisions[c.job_id]}
            if any(c.job_id in reviewed_ids and (c.payload.get("export_pending")
                   or c.state not in {"completed", "needs_review"}) for c in children):
                return self._outcome(replace(parent, state="pending", last_error="review_export_failed"))
            self._verify_split_manifests(parent)
            for child in children:
                self._verify_split_child(child)
            cleanup = self._split_cleanup_items(parent, children)
            if (parent.payload.get("publish_state") == "completed"
                    and parent.state in {"completed", "needs_review"}
                    and not parent.payload.get("export_pending") and self.publication.current()):
                for child in children:
                    self._save_if_current(child, child)
                return self._cleanup_split(parent, cleanup)

            publication_id = uuid4().hex
            # Set reading states before render. Finalization only clears ownership
            # flags/paths, which are deliberately excluded from reading_revision.
            for child in children:
                if child.job_id in reviewed_ids:
                    candidates.append(child)
                    continue
                terminal = ("needs_review" if any(q["status"] == "needs_review"
                            for q in self.repo.get_document(child.job_id)["questions"]) else "completed")
                candidates.append(replace(child, state=terminal, last_error=None, payload=dict(
                    child.payload, export_pending=True, publish_state="pending",
                    ordinary_publication_id=publication_id,
                )))
            terminal = "needs_review" if any(c.state == "needs_review" for c in candidates) else "completed"
            expected = replace(parent, state=terminal, last_error=None, payload=dict(
                parent.payload, export_pending=True, publish_state="pending",
                ordinary_publication_id=publication_id,
                cleanup=parent.payload.get("cleanup") or (
                    [] if parent.payload.get("cleanup_completed") else cleanup),
            ))
            owned_child_ids = {c.job_id for c in children} - reviewed_ids
            with self.repo.connection:
                self.repo.connection.execute("BEGIN IMMEDIATE")
                if not self._split_owner_matches(parent, children, review_revisions):
                    raise _WorkflowSuperseded
                for previous, candidate in zip([parent, *children], [expected, *candidates]):
                    if previous.job_id in reviewed_ids:
                        continue
                    if previous.state != candidate.state:
                        # Use the established pending -> analyzing -> terminal
                        # transitions inside the same atomic candidate transaction.
                        if previous.state != "pending":
                            self.repo._write_workflow_job(replace(previous, state="pending"))
                        self.repo._write_workflow_job(replace(previous, state="analyzing"))
                    self.repo._write_workflow_job(candidate)
                    self._mirror(candidate)
            owned_parent, owned_children = expected, candidates

            def render():
                details = {child.job_id: str(self.markdown.export_document(child.job_id)) for child in candidates}
                for subject in sorted({child.subject for child in candidates}):
                    self.markdown.export_subject(subject)
                return dict(analysis_markdown_by_child=details, dashboard_path=str(self.dashboard.export()))

            def finalize(paths):
                for child in candidates:
                    if child.job_id in reviewed_ids:
                        continue
                    completed = replace(child, payload=dict(
                        child.payload, export_pending=False, publish_state="completed",
                        analysis_markdown=paths["analysis_markdown_by_child"][child.job_id],
                        dashboard_path=paths["dashboard_path"],
                    ))
                    self.repo._write_workflow_job(completed)
                    self._mirror(completed)
                completed = replace(expected, payload=dict(
                    expected.payload, export_pending=False, publish_state="completed", **paths,
                ))
                self.repo._write_workflow_job(completed)
                self._mirror(completed)

            if not self.publication.publish(
                expected, render, finalize,
                ownership_check=lambda: self._split_owner_matches(expected, candidates, review_revisions),
            ):
                self._fail_split_publication(expected, candidates, "publication_changed", owned_child_ids, review_revisions)
                return self._outcome(self._require_job(parent.job_id))
            return self._cleanup_split(self._require_job(parent.job_id), cleanup)
        except _WorkflowSuperseded:
            return self._outcome(self._require_job(parent.job_id))
        except (OSError, ValueError, RuntimeError):
            self._fail_split_publication(owned_parent, owned_children, "workflow_failed", owned_child_ids, review_revisions)
            return self._outcome(self._require_job(parent.job_id))

    def _split_owner_matches(self, parent, children, review_revisions):
        return (all(self.repo.workflow_job_matches(j) for j in [parent, *children])
                and all(self.repo.review_publication_revision(identity) == revision
                        for identity, revision in review_revisions.items()))

    def _fail_split_publication(self, parent, children, error, owned_child_ids, review_revisions):
        with self.repo.connection:
            self.repo.connection.execute("BEGIN IMMEDIATE")
            # A single changed child/review revision supersedes the whole batch.
            # Partial sibling downgrade would invalidate the newer full-library
            # publication even when the parent journal happened to stay equal.
            if not self._split_owner_matches(parent, children, review_revisions):
                return
            for job in [parent, *children]:
                if job.job_id == parent.job_id or job.job_id in owned_child_ids:
                    failed = replace(job, state="pending", last_error=error)
                    self.repo._write_workflow_job(failed)
                    try:
                        self._mirror(failed)
                    except (OSError, ValueError, RuntimeError):
                        pass  # The journal owns recovery even when a mirror is unavailable.

    def _split_cleanup_items(self, parent, children):
        """Bind every removable source to its assigned, audited child destination."""
        sources = {p["page_number"]: p for p in parent.payload["pages"]}
        assets = {a["source"]: a for a in parent.payload["assets"] if a.get("cleanup")}
        source_directory = Path(parent.payload["session_dir"])
        if parent.payload.get("archive_kind") == "pending":
            source_directory = Path(parent.payload["pages"][0]["path"]).parent
            allowed = {self.config.knowledge_root.absolute() / subject / "待处理" / parent.job_id
                       for subject in self.config.subjects}
            if source_directory not in allowed:
                raise ValueError("拆分待处理来源不匹配")
            self._knowledge_guard(source_directory)
        else:
            self._spool_guard(source_directory)
        items = []
        for group, child in zip(parent.payload["split_plan"]["groups"], children):
            if (child.job_id != group["document_id"] or child.subject != group["subject"]
                    or child.payload.get("parent_job_id") != parent.job_id
                    or [p["page_number"] for p in child.payload["pages"]] != group["page_numbers"]):
                raise ValueError("拆分子任务与审计清单不一致")
            year, month = parent.payload["date"].split("-")
            directory = self.config.knowledge_root.absolute() / child.subject / "原始资料" / year / month / child.job_id
            for page in child.payload["pages"]:
                original = sources[page["page_number"]]
                source, destination = Path(original["path"]), Path(page["path"])
                if (source != source_directory / source.name
                        or destination != directory / source.name or page["sha256"] != original["sha256"]):
                    raise ValueError("拆分页面归档映射不一致")
                self._source_guard(source)
                self._knowledge_guard(destination)
                asset = assets.get(str(source))
                if asset and asset["sha256"] == original["sha256"]:
                    items.append(dict(source=str(source), destination=str(destination), sha256=page["sha256"]))
                    if parent.payload.get("archive_kind") == "pending":
                        spool_source = Path(parent.payload["session_dir"]) / source.name
                        self._spool_guard(spool_source)
                        items.append(dict(source=str(spool_source), destination=str(destination), sha256=page["sha256"]))
        return items

    def _cleanup_split(self, parent, verified_items):
        """Deletion is replayable independently of publication and never changes reading facts."""
        try:
            if not parent.payload.get("cleanup_completed") and not parent.payload.get("cleanup"):
                parent = self._save_if_current(parent, replace(parent, payload=dict(parent.payload, cleanup=verified_items)))
            for item in list(parent.payload["cleanup"]):
                if item not in verified_items:
                    raise ValueError("清理记录缺少拆分归档依据")
                self._verify_split_manifests(parent)
                source, destination = Path(item["source"]), Path(item["destination"])
                self._source_guard(source)
                self._knowledge_guard(destination)
                if self._digest(destination) != item["sha256"]:
                    raise ValueError("拆分归档哈希不匹配")
                if source.exists():
                    if self._digest(source) != item["sha256"]:
                        raise ValueError("暂存哈希不匹配")
                    source.unlink()
                remaining = [entry for entry in parent.payload["cleanup"] if entry != item]
                parent = self._save_if_current(parent, replace(parent, payload=dict(parent.payload, cleanup=remaining)))
            parent = self._save_if_current(parent, replace(parent, last_error=None, payload=dict(
                parent.payload, cleanup_completed=True,
            )))
        except (OSError, ValueError, RuntimeError):
            parent = self._save_if_current(parent, replace(parent, last_error="cleanup_failed"))
        return self._outcome(parent)

    def _verify_split_child(self, child: WorkflowJob) -> None:
        for page in self._document(child).pages:
            self._knowledge_guard(page.path)
            if self._digest(page.path) != page.sha256:
                raise ValueError("拆分归档哈希不匹配")

    def _split_manifest(self, parent: WorkflowJob) -> dict:
        """Rebuild audit facts from the durable parent without requiring spool images."""
        document = self._document(parent)
        analysis = self._analysis_from_payload(parent.payload["analysis"])
        plan = build_subject_split_plan(
            document, analysis,
            {int(page): Subject(subject) for page, subject
             in parent.payload.get("page_subject_overrides", {}).items()},
            self.config.subject_confidence_threshold,
        )
        canonical = self._with_split_plan({}, plan)["split_plan"]
        if (plan.unresolved_pages or len(plan.groups) < 2
                or canonical != parent.payload.get("split_plan")):
            raise ValueError("拆分审计计划与父任务不一致")
        split_analysis(analysis, plan)
        return dict(
            version=1, parent_document_id=parent.job_id, groups=canonical["groups"],
            resolved_pages=canonical["resolved_pages"],
            page_subject_overrides=parent.payload.get("page_subject_overrides", {}),
            model_page_subjects=[asdict(item) for item in analysis.page_subjects],
            source_pages=[dict(page=p.page_number, filename=p.path.name, sha256=p.sha256)
                          for p in sorted(document.pages, key=lambda page: page.page_number)],
        )

    def _ensure_split_manifest(self, destination: Path, manifest: dict) -> None:
        """Reuse identical audit bytes; install missing bytes without clobbering collisions."""
        self._knowledge_guard(destination)
        content = json.dumps(manifest, ensure_ascii=False, allow_nan=False)
        digest = sha256(content.encode("utf-8")).hexdigest()
        if destination.exists():
            if self._digest(destination) != digest:
                raise ValueError("拆分审计清单已存在且内容不同")
            return
        # The normal atomic writer replaces files. Write privately, then reuse the
        # verified archive installer so a concurrent destination is checked too.
        with tempfile.TemporaryDirectory(prefix=".split-audit-", dir=destination.parent) as name:
            temporary = Path(name) / destination.name
            self._knowledge_guard(temporary)
            atomic_write(temporary, content, self.config.knowledge_root)
            self._knowledge_guard(destination)
            self._copy_verified(temporary, destination, digest)

    def _verify_split_manifests(self, parent: WorkflowJob) -> None:
        manifest = self._split_manifest(parent)
        if parent.payload.get("child_document_ids") != [g["document_id"] for g in manifest["groups"]]:
            raise ValueError("拆分子文档与父任务不一致")
        year, month = parent.payload["date"].split("-")
        if not re.fullmatch(r"\d{4}", year) or not re.fullmatch(r"0[1-9]|1[0-2]", month):
            raise ValueError("非法归档日期")
        for group in manifest["groups"]:
            directory = (self.config.knowledge_root.absolute() / group["subject"] / "原始资料"
                         / year / month / group["document_id"])
            self._ensure_split_manifest(directory / "split-manifest.json", manifest)

    def _stage_split_files(self, job: WorkflowJob, plan: SubjectSplitPlan) -> tuple[CapturedDocument, ...]:
        """Verify every child archive before committing any knowledge facts; keep sources."""
        # Validate canonical IDs and complete page coverage before using group paths.
        analysis = self._analysis_from_payload(job.payload["analysis"])
        split_analysis(analysis, plan)
        source_pages = {p.page_number: p for p in self._document(job).pages}
        year, month = job.payload["date"].split("-")
        if not re.fullmatch(r"\d{4}", year) or not re.fullmatch(r"0[1-9]|1[0-2]", month):
            raise ValueError("非法归档日期")
        documents = []
        for group in plan.groups:
            lexical = (self.config.knowledge_root.absolute() / group.subject.value
                       / "原始资料" / year / month / group.document_id)
            self._knowledge_guard(lexical)
            target = self.paths.document_directory(group.subject.value, year, month, group.document_id)
            self._knowledge_guard(target)
            pages = []
            for number in group.page_numbers:
                source = source_pages[number]
                destination = target / source.path.name
                self._source_guard(source.path)
                self._knowledge_guard(destination)
                self._copy_verified(source.path, destination, source.sha256)
                pages.append(CapturedPage(number, destination, source.sha256))
            documents.append(CapturedDocument(group.document_id, tuple(pages), analysis.document_type))
        manifest = self._split_manifest(job)
        for document in documents:
            for page in document.pages:
                self._knowledge_guard(page.path)
                if self._digest(page.path) != page.sha256:
                    raise ValueError("拆分归档哈希不匹配")
            self._ensure_split_manifest(document.pages[0].path.parent / "split-manifest.json", manifest)
        return tuple(documents)

    def _archive(self, job: WorkflowJob, kind: str) -> WorkflowJob:
        if job.subject is None:
            raise ValueError("归档前必须确定科目")
        if job.payload["archive_kind"] and job.payload["archive_kind"] != kind:
            # Finish old cleanup while its verified destination still exists. Otherwise
            # a second crash during pending -> raw relocation strands the old journal.
            job = self._archive(job, job.payload["archive_kind"])
        self._knowledge_guard(self.config.knowledge_root.absolute() / job.subject)
        year, month = job.payload["date"].split("-")
        if kind == "raw":
            # Guard the lexical path before KnowledgePaths resolves existing links.
            target = self.config.knowledge_root.absolute() / job.subject / "原始资料" / year / month / job.job_id
        else:
            target = self.config.knowledge_root.absolute() / job.subject / "待处理" / job.job_id
        self._knowledge_guard(target)
        target.mkdir(parents=True, exist_ok=True)
        self._knowledge_guard(target)
        if job.payload["archive_kind"] != kind:
            new_assets, cleanup = [], list(job.payload["cleanup"])
            for asset in job.payload["assets"]:
                relative = Path(asset["relative"])
                if relative.is_absolute() or ".." in relative.parts:
                    raise ValueError("归档路径越界")
                source, destination = Path(asset["source"]), target / relative
                self._source_guard(source)
                self._knowledge_guard(destination)
                self._copy_verified(source, destination, asset["sha256"])
                new_assets.append(dict(asset, source=str(destination)))
                # Only image copies are removed; spool metadata remains for discovery.
                if asset["cleanup"] and source != destination:
                    cleanup.append({"source": str(source), "destination": str(destination),
                                    "sha256": asset["sha256"]})
            pages = [dict(p, path=str(target / Path(p["path"]).name)) for p in job.payload["pages"]]
            payload = dict(job.payload, assets=new_assets, pages=pages, archive_kind=kind, cleanup=cleanup)
            staged = replace(job, payload=payload)
            self.repo.stage_workflow_archive(staged, self._document(staged))
            job = self._require_job(job.job_id)
            self._mirror(job)
        # Verify every destination again before removing any source image.
        for asset in job.payload["assets"]:
            path = Path(asset["source"])
            self._knowledge_guard(path)
            if self._digest(path) != asset["sha256"]:
                raise ValueError("归档哈希不匹配")
        for item in job.payload["cleanup"]:
            source, destination = Path(item["source"]), Path(item["destination"])
            self._source_guard(source)
            self._knowledge_guard(destination)
            if self._digest(destination) != item["sha256"]:
                raise ValueError("归档哈希不匹配")
            if source.exists():
                if self._digest(source) != item["sha256"]:
                    raise ValueError("暂存哈希不匹配")
                source.unlink()
        if job.payload["cleanup"]:
            job = self._save(replace(job, payload=dict(job.payload, cleanup=[])))
        return job

    def _copy_verified(self, source: Path, destination: Path, digest: str) -> None:
        if destination.exists():
            if self._digest(destination) != digest:
                raise ValueError("目标归档文件已存在且哈希不同")
            # Existing verified destination permits replay even after source cleanup.
            return
        if self._digest(source) != digest:
            raise ValueError("暂存哈希不匹配")
        destination.parent.mkdir(parents=True, exist_ok=True)
        self._knowledge_guard(destination)
        descriptor, name = tempfile.mkstemp(prefix=".archive-", suffix=".part", dir=destination.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as output, source.open("rb") as input_file:
                descriptor = -1
                for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            if self._digest(temporary) != digest:
                raise ValueError("归档复制校验失败")
            self._knowledge_guard(destination)
            # Do not silently replace a colliding archive created in the meantime.
            if destination.exists():
                if self._digest(destination) != digest:
                    raise ValueError("归档目标冲突")
            else:
                temporary.rename(destination)
        finally:
            if descriptor != -1:
                os.close(descriptor)
            if temporary.exists():
                self._knowledge_guard(temporary)
                temporary.unlink()

    def _save(self, job: WorkflowJob) -> WorkflowJob:
        self.repo.save_workflow_job(job)
        self._mirror(job)
        return job

    def _save_if_current(self, expected: WorkflowJob, job: WorkflowJob) -> WorkflowJob:
        if not self.repo.save_workflow_job_if_current(expected, job, self._mirror):
            raise _WorkflowSuperseded
        return job

    def _mirror(self, job: WorkflowJob) -> None:
        payload = {"version": 1, "document_id": job.job_id, "state": job.state,
                   "error_code": job.last_error}
        if job.payload.get("parent_job_id"):
            year, month = job.payload["date"].split("-")
            directory = (self.config.knowledge_root.absolute() / job.subject / "原始资料"
                         / year / month / job.job_id)
            destination = Path(job.payload["mirror_path"])
            self._knowledge_guard(directory)
            self._knowledge_guard(destination)
            if destination.absolute() != directory / "analysis_state.json":
                raise ValueError("拆分子文档镜像路径无效")
            atomic_write(destination, json.dumps(payload), self.config.knowledge_root)
            return
        directory = Path(job.payload["session_dir"])
        self._spool_guard(directory)
        atomic_write(directory / "analysis_state.json", json.dumps(payload), self.config.spool_root)
        # A retry can analyze pages already archived in a subject's pending folder.
        if job.payload["archive_kind"]:
            archived = Path(job.payload["pages"][0]["path"]).parent
            self._knowledge_guard(archived)
            atomic_write(archived / "analysis_state.json", json.dumps(payload), self.config.knowledge_root)

    def _require_job(self, job_id: str) -> WorkflowJob:
        job = self.repo.get_job(job_id)
        if job is None:
            raise ValueError("找不到处理任务")
        return job

    @staticmethod
    def _document(job: WorkflowJob) -> CapturedDocument:
        return CapturedDocument(job.job_id, tuple(
            CapturedPage(p["page_number"], Path(p["path"]), p["sha256"])
            for p in job.payload["pages"]),
            (job.payload.get("analysis") or {}).get("document_type", "作业"))

    def _analysis_from_payload(self, payload: dict):
        legacy = ("page_subjects" not in payload or any(
            "answer_bbox" not in question for question in payload.get("questions", ())
        ))
        return _from_payload(payload, allow_legacy=legacy)

    def refresh_review_annotations(self, document_ids: tuple[str, ...]) -> None:
        """Upgrade cached review images once without rerunning the model."""
        for document_id in document_ids:
            try:
                job = self.repo.get_job(document_id)
                if job is None or not isinstance(job.payload.get("analysis"), dict):
                    continue
                self._ensure_annotations(job, self._analysis_from_payload(job.payload["analysis"]))
            except (OSError, ValueError, RuntimeError):
                # An old or damaged preview must not block access to the review.
                continue

    def _ensure_annotations(self, job: WorkflowJob, analysis) -> WorkflowJob:
        """Render or verify deterministic grading copies from archived originals."""
        # Historical cached analyses predate coordinate OCR. Their ordinary
        # publication must remain replayable without trying to decode test or
        # legacy evidence as a new image.
        if not any(question.answer_bbox is not None for question in analysis.questions):
            return job
        cached_pages = tuple(Path(path) for path in job.payload.get("annotated_pages", ()))
        cached_gallery = (Path(job.payload["grading_gallery_path"])
                          if job.payload.get("grading_gallery_path") else None)
        if (job.payload.get("annotation_layout_version") == ANNOTATION_LAYOUT_VERSION
                and cached_pages and all(path.exists() for path in cached_pages)
                and cached_gallery is not None and cached_gallery.exists()):
            return job
        artifacts = self.annotations.render(self._document(job), analysis)
        paths = [str(path) for path in artifacts.pages]
        return self._save_if_current(job, replace(job, payload=dict(
            job.payload, annotated_pages=paths,
            grading_gallery_path=str(artifacts.gallery),
            annotation_layout_version=ANNOTATION_LAYOUT_VERSION,
        )))

    @staticmethod
    def _is_legacy_confirmation(job: WorkflowJob) -> bool:
        analysis = job.payload.get("analysis")
        return (
            job.state == "needs_subject_confirmation"
            and job.payload.get("confirmation_mode") is None
            and isinstance(analysis, dict)
            and "page_subjects" not in analysis
        )

    def _page_split_plan(self, job: WorkflowJob) -> SubjectSplitPlan:
        analysis_payload = job.payload.get("analysis")
        if not isinstance(analysis_payload, dict) or "page_subjects" not in analysis_payload:
            raise ValueError("页面科目确认缺少页面分析")
        document = self._document(job)
        for page in document.pages:
            self._source_guard(page.path)
            if self._digest(page.path) != page.sha256:
                raise ValueError("页面确认图片哈希不匹配")
        overrides = {
            int(page): Subject(subject)
            for page, subject in job.payload.get("page_subject_overrides", {}).items()
        }
        return build_subject_split_plan(
            document, self._analysis_from_payload(analysis_payload), overrides,
            self.config.subject_confidence_threshold,
        )

    @staticmethod
    def _with_split_plan(payload: dict, plan: SubjectSplitPlan) -> dict:
        """Persist only JSON-safe facts; capture payload remains path authority."""
        split_plan = {
            "parent_document_id": plan.parent_document_id,
            "groups": [
                {"subject": group.subject.value, "document_id": group.document_id,
                 "page_numbers": list(group.page_numbers)}
                for group in plan.groups
            ],
            "unresolved_pages": [
                {"page": item.page, "suggested_subject": item.suggested_subject.value,
                 "confidence": item.confidence, "reason": item.reason}
                for item in plan.unresolved_pages
            ],
            "resolved_pages": [
                {"page": item.page, "subject": item.subject.value, "source": item.source,
                 "confidence": item.confidence}
                for item in plan.resolved_pages
            ],
        }
        return dict(payload, split_plan=split_plan)

    def _outcome(self, job: WorkflowJob) -> WorkflowOutcome:
        payload = job.payload
        state = job.state
        if (state in {"completed", "needs_review"} and payload.get("export_pending")
                and (payload.get("parent_job_id") or payload.get("child_document_ids"))):
            state = "pending"
        pending = ()
        confirmation_index = confirmation_total = 0
        if (job.state == "needs_subject_confirmation"
                and payload.get("confirmation_mode") == "pages"):
            # The interaction is deliberately one page at a time. Image locations
            # are rebuilt from validated captured-page facts, never model output.
            plan = self._page_split_plan(job)
            pending = plan.unresolved_pages[:1]
            confirmed = sum(item.source == "human" for item in plan.resolved_pages)
            confirmation_total = confirmed + len(plan.unresolved_pages)
            if pending:
                confirmation_index = confirmed + 1
            else:
                # The final override can survive a crash before its state change.
                # Every consumer, including UI Retry, must resume publication,
                # never reinterpret an empty page queue as legacy batch choice.
                state = "pending"
        return WorkflowOutcome(
            job.job_id, state, job.subject,
            archived_pages=(tuple(Path(p["path"]) for p in payload["pages"])
                            if payload["archive_kind"] and not payload.get("child_document_ids") else ()),
            analysis_markdown=(Path(payload["analysis_markdown"])
                               if payload.get("analysis_markdown") else None),
            dashboard_path=(Path(payload["dashboard_path"])
                            if payload.get("dashboard_path") else None),
            summary=(payload.get("analysis") or {}).get("summary", ""),
            error_code=job.last_error,
            pending_page_subjects=pending,
            child_document_ids=tuple(payload.get("child_document_ids", ())),
            page_subject_index=confirmation_index,
            page_subject_total=confirmation_total,
            annotated_pages=tuple(Path(path) for path in payload.get("annotated_pages", ())),
            grading_gallery_path=(Path(payload["grading_gallery_path"])
                                   if payload.get("grading_gallery_path") else None),
            parent_job_id=payload.get("parent_job_id"),
        )

    def _spool_guard(self, path: Path) -> None:
        _guard(path.absolute(), self.config.spool_root.absolute())

    def _knowledge_guard(self, path: Path) -> None:
        _guard(path.absolute(), self.config.knowledge_root.absolute())

    def _source_guard(self, path: Path) -> None:
        try:
            path.absolute().relative_to(self.config.spool_root.absolute())
        except ValueError:
            self._knowledge_guard(path)
        else:
            self._spool_guard(path)

    @staticmethod
    def _digest(path: Path) -> str:
        digest = sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()
