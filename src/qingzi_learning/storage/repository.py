"""Transactional, idempotent SQLite storage for analysis facts."""

from collections import Counter
from collections.abc import Iterable
from dataclasses import asdict, is_dataclass, replace
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
from pathlib import Path
import sqlite3
from typing import Any
from uuid import uuid4

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import AnalysisResult, CapturedDocument, CapturedPage


_PENDING_STATES = ("pending", "spooled", "analyzing", "needs_subject_confirmation", "needs_review")
_WORKFLOW_TRANSITIONS = {
    "capturing": {"spooled", "pending"},
    "spooled": {"analyzing", "pending"},
    "analyzing": {"pending", "needs_subject_confirmation", "needs_review", "completed"},
    "needs_subject_confirmation": {"pending"},
    "pending": {"analyzing", "needs_subject_confirmation"},
    "needs_review": {"pending", "completed"},
    "completed": {"pending"},
}


@dataclass(frozen=True)
class WorkflowJob:
    job_id: str
    state: str
    subject: str | None
    knowledge_applied: bool
    last_error: str | None
    payload: dict[str, Any]


@dataclass(frozen=True)
class KnowledgeStats:
    """A regenerated mastery view for one subject knowledge point."""

    subject: str
    knowledge_point: str
    first_seen_at: str | None
    last_seen_at: str | None
    exposure_count: int
    correct_count: int
    incorrect_count: int
    partial_count: int
    teacher_correct_count: int
    teacher_incorrect_count: int
    model_correct_count: int
    model_incorrect_count: int
    common_error_categories: tuple[str, ...]
    needs_review: int
    review_priority: int
    trend: str

    @property
    def mastery_rate(self) -> float | None:
        """Return a transparent partial-credit rate, or None without confirmed work."""
        if not self.exposure_count:
            return None
        return (self.correct_count + self.partial_count * 0.5) / self.exposure_count


@dataclass(frozen=True)
class ReportRun:
    """One durable report snapshot; completed rows are never rewritten."""

    report_id: str
    status: str
    previous_report_id: str | None
    evidence_cutoff_at: str
    snapshot: dict[str, Any]
    narrative: dict[str, Any]
    output_files: dict[str, str]
    error_code: str | None
    created_at: str
    completed_at: str | None


@dataclass(frozen=True)
class ExamRun:
    exam_id: str
    status: str
    subject: str
    request: dict[str, Any]
    blueprint: dict[str, Any]
    generation: dict[str, Any]
    verification: dict[str, Any]
    output_files: dict[str, str]
    error_code: str | None
    revision: int
    created_at: str
    approved_at: str | None


@dataclass(frozen=True)
class ExamQuestion:
    exam_id: str
    question_id: str
    question_type: str
    points: int
    knowledge_points: tuple[str, ...]
    blueprint_category: str
    prompt: str
    answer: str
    explanation: str
    rubric: str


@dataclass(frozen=True)
class ExamAttempt:
    exam_id: str
    exam_question_id: str
    document_id: str
    document_question_id: str
    status: str
    linked_at: str


class KnowledgeRepository:
    """The durable fact layer; each analysis save replaces one document atomically."""

    def __init__(self, config: AppConfig, database_path: Path | None = None) -> None:
        self.config = config
        database_path = database_path or config.app_data_root / "knowledge.sqlite3"
        database_path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(database_path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.executescript(
            Path(__file__).with_name("schema.sql").read_text(encoding="utf-8")
        )

    def close(self) -> None:
        self.connection.close()

    def create_report_run(
        self,
        report_id: str,
        previous_report_id: str | None,
        evidence_cutoff_at: str,
        snapshot: dict[str, Any],
    ) -> ReportRun:
        if not report_id or not evidence_cutoff_at or not isinstance(snapshot, dict):
            raise ValueError("报告参数不完整")
        if previous_report_id is not None:
            previous = self.get_report_run(previous_report_id)
            if previous is None or previous.status != "completed":
                raise ValueError("上一份报告不存在或尚未完成")
        try:
            with self.connection:
                self.connection.execute(
                    """INSERT INTO report_runs(
                        report_id, status, previous_report_id, evidence_cutoff_at, snapshot_json
                    ) VALUES (?, 'generating', ?, ?, ?)""",
                    (report_id, previous_report_id, evidence_cutoff_at, self._json(snapshot)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("报告编号已存在") from exc
        return self._require_report_run(report_id)

    def complete_report_run(
        self,
        report_id: str,
        narrative: dict[str, Any],
        output_files: dict[str, str],
    ) -> ReportRun:
        if not isinstance(narrative, dict) or not isinstance(output_files, dict):
            raise ValueError("报告输出格式无效")
        current = self._require_report_run(report_id)
        if current.status == "completed":
            raise ValueError("报告已完成，不能再次发布")
        if current.status == "failed":
            raise ValueError("报告已失败，不能发布")
        with self.connection:
            changed = self.connection.execute(
                """UPDATE report_runs
                   SET status='completed', narrative_json=?, output_files_json=?,
                       error_code=NULL, completed_at=CURRENT_TIMESTAMP
                   WHERE report_id=? AND status='generating'""",
                (self._json(narrative), self._json(output_files), report_id),
            ).rowcount
        if changed != 1:
            raise ValueError("报告状态已变化")
        return self._require_report_run(report_id)

    def fail_report_run(self, report_id: str, error_code: str) -> ReportRun:
        if not error_code:
            raise ValueError("报告失败代码不能为空")
        current = self._require_report_run(report_id)
        if current.status == "completed":
            raise ValueError("报告已完成，不能标记失败")
        if current.status == "failed":
            return current
        with self.connection:
            changed = self.connection.execute(
                """UPDATE report_runs
                   SET status='failed', error_code=?, completed_at=CURRENT_TIMESTAMP
                   WHERE report_id=? AND status='generating'""",
                (error_code, report_id),
            ).rowcount
        if changed != 1:
            raise ValueError("报告状态已变化")
        return self._require_report_run(report_id)

    def get_report_run(self, report_id: str) -> ReportRun | None:
        row = self.connection.execute(
            "SELECT * FROM report_runs WHERE report_id=?", (report_id,)
        ).fetchone()
        return None if row is None else self._report_run(row)

    def latest_completed_report(self) -> ReportRun | None:
        row = self.connection.execute(
            """SELECT * FROM report_runs WHERE status='completed'
               ORDER BY completed_at DESC, rowid DESC LIMIT 1"""
        ).fetchone()
        return None if row is None else self._report_run(row)

    def list_report_runs(self) -> tuple[ReportRun, ...]:
        rows = self.connection.execute(
            "SELECT * FROM report_runs ORDER BY created_at DESC, rowid DESC"
        ).fetchall()
        return tuple(self._report_run(row) for row in rows)

    def report_evidence_rows(self, cutoff_at: str) -> dict[str, Any]:
        """Return bounded, read-only facts used by deterministic report snapshots."""
        rows = self.connection.execute(
            """SELECT documents.document_id, documents.subject, documents.created_at,
                      effective_questions.question_id, effective_questions.status,
                      effective_questions.prompt_summary,
                      effective_questions.error_categories_json,
                      links.knowledge_point
               FROM effective_questions
               JOIN documents ON documents.document_id=effective_questions.document_id
               LEFT JOIN question_knowledge_points AS links
                 ON links.document_id=effective_questions.document_id
                AND links.question_id=effective_questions.question_id
               WHERE datetime(documents.created_at) <= datetime(?)
                 AND effective_questions.status != 'needs_review'
                 AND (effective_questions.decision_source='parent'
                      OR effective_questions.confidence >= 0.80)
               ORDER BY documents.created_at, documents.document_id,
                        effective_questions.question_id, links.knowledge_point""",
            (cutoff_at,),
        ).fetchall()
        effective = []
        for row in rows:
            item = dict(row)
            categories = json.loads(item.pop("error_categories_json"))
            if not isinstance(categories, list) or not all(isinstance(value, str) for value in categories):
                raise ValueError("题目错因记录已损坏")
            item["error_categories"] = categories
            effective.append(item)
        pending_count = self.connection.execute(
            """SELECT COUNT(*) FROM effective_questions
               JOIN documents ON documents.document_id=effective_questions.document_id
               WHERE datetime(documents.created_at) <= datetime(?)
                 AND effective_questions.status='needs_review'""",
            (cutoff_at,),
        ).fetchone()[0]
        excluded_low_confidence_count = self.connection.execute(
            """SELECT COUNT(*) FROM effective_questions
               JOIN documents ON documents.document_id=effective_questions.document_id
               WHERE datetime(documents.created_at) <= datetime(?)
                 AND effective_questions.status!='needs_review'
                 AND effective_questions.decision_source!='parent'
                 AND effective_questions.confidence < 0.80""",
            (cutoff_at,),
        ).fetchone()[0]
        return {
            "effective_rows": effective,
            "pending_count": int(pending_count),
            "excluded_low_confidence_count": int(excluded_low_confidence_count),
            "knowledge_stats": self._knowledge_point_rows(),
        }

    def report_retest_rows(self, cutoff_at: str) -> list[dict[str, Any]]:
        """Return approved, linked retest facts available at a report cutoff."""
        rows = self.connection.execute(
            """SELECT attempts.exam_id, attempts.exam_question_id,
                      attempts.document_id, attempts.document_question_id,
                      attempts.status AS current_status, attempts.linked_at,
                      runs.subject, runs.blueprint_json,
                      questions.knowledge_points_json
               FROM exam_attempts AS attempts
               JOIN exam_runs AS runs ON runs.exam_id=attempts.exam_id
               JOIN exam_questions AS questions
                 ON questions.exam_id=attempts.exam_id
                AND questions.question_id=attempts.exam_question_id
               WHERE runs.status='approved'
                 AND datetime(attempts.linked_at) <= datetime(?)
               ORDER BY attempts.linked_at, attempts.exam_id,
                        attempts.exam_question_id, attempts.document_id""",
            (cutoff_at,),
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            blueprint = json.loads(item.pop("blueprint_json"))
            points = json.loads(item.pop("knowledge_points_json"))
            if (not isinstance(blueprint, dict) or not isinstance(points, list)
                    or not all(isinstance(point, str) for point in points)):
                raise ValueError("复测记录已损坏")
            item["blueprint"] = blueprint
            item["knowledge_points"] = points
            result.append(item)
        return result

    def create_exam_run(
        self,
        exam_id: str,
        subject: str,
        request: dict[str, Any],
        blueprint: dict[str, Any],
    ) -> ExamRun:
        self._validate_subject(subject)
        if not exam_id or not isinstance(request, dict) or not isinstance(blueprint, dict):
            raise ValueError("模拟卷参数不完整")
        try:
            with self.connection:
                self.connection.execute(
                    """INSERT INTO exam_runs(
                        exam_id, status, subject, request_json, blueprint_json
                    ) VALUES (?, 'draft', ?, ?, ?)""",
                    (exam_id, subject, self._json(request), self._json(blueprint)),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("模拟卷编号已存在") from exc
        return self._require_exam_run(exam_id)

    def save_exam_generation(
        self,
        exam_id: str,
        generation: dict[str, Any],
        verification: dict[str, Any],
        questions: Iterable[dict[str, Any] | ExamQuestion],
    ) -> ExamRun:
        current = self._require_exam_run(exam_id)
        if current.status == "approved":
            raise ValueError("模拟卷已批准，不能重新生成")
        if current.status != "draft":
            raise ValueError("模拟卷当前状态不能保存生成结果")
        rows = []
        for value in questions:
            item = asdict(value) if is_dataclass(value) else dict(value)
            knowledge_points = item.get("knowledge_points")
            if (not item.get("question_id") or not isinstance(knowledge_points, (list, tuple))
                    or not knowledge_points):
                raise ValueError("模拟题结构不完整")
            rows.append((
                exam_id,
                item["question_id"],
                item["question_type"],
                int(item["points"]),
                self._json(list(knowledge_points)),
                item["blueprint_category"],
                item["prompt"],
                item["answer"],
                item["explanation"],
                item["rubric"],
            ))
        if not rows:
            raise ValueError("模拟卷没有题目")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            self.connection.executemany(
                """INSERT INTO exam_questions(
                    exam_id, question_id, question_type, points,
                    knowledge_points_json, blueprint_category, prompt,
                    answer, explanation, rubric
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                rows,
            )
            changed = self.connection.execute(
                """UPDATE exam_runs SET status='needs_parent_approval',
                       generation_json=?, verification_json=?, error_code=NULL,
                       revision=revision+1
                   WHERE exam_id=? AND status='draft'""",
                (self._json(generation), self._json(verification), exam_id),
            ).rowcount
            if changed != 1:
                raise ValueError("模拟卷状态已变化")
        return self._require_exam_run(exam_id)

    def approve_exam(
        self,
        exam_id: str,
        output_files: dict[str, str],
        *,
        expected_revision: int,
    ) -> ExamRun:
        current = self._require_exam_run(exam_id)
        if current.status == "approved":
            raise ValueError("模拟卷已批准")
        if current.status != "needs_parent_approval":
            raise ValueError("模拟卷尚未通过校验，不能批准")
        if current.revision != expected_revision:
            raise ValueError("模拟卷已变化，请重新预览")
        if not isinstance(output_files, dict) or not output_files:
            raise ValueError("模拟卷输出文件不能为空")
        with self.connection:
            changed = self.connection.execute(
                """UPDATE exam_runs SET status='approved', output_files_json=?,
                       revision=revision+1, approved_at=CURRENT_TIMESTAMP
                   WHERE exam_id=? AND status='needs_parent_approval' AND revision=?""",
                (self._json(output_files), exam_id, expected_revision),
            ).rowcount
        if changed != 1:
            raise ValueError("模拟卷已变化，请重新预览")
        return self._require_exam_run(exam_id)

    def fail_exam(self, exam_id: str, error_code: str) -> ExamRun:
        current = self._require_exam_run(exam_id)
        if current.status == "approved":
            raise ValueError("模拟卷已批准，不能标记失败")
        if current.status == "failed":
            return current
        with self.connection:
            self.connection.execute(
                """UPDATE exam_runs SET status='failed', error_code=?, revision=revision+1
                   WHERE exam_id=? AND status!='approved'""",
                (error_code, exam_id),
            )
        return self._require_exam_run(exam_id)

    def get_exam_run(self, exam_id: str) -> ExamRun | None:
        row = self.connection.execute(
            "SELECT * FROM exam_runs WHERE exam_id=?", (exam_id,)
        ).fetchone()
        return None if row is None else self._exam_run(row)

    def list_exam_runs(self, status: str | None = None) -> tuple[ExamRun, ...]:
        if status is None:
            rows = self.connection.execute(
                "SELECT * FROM exam_runs ORDER BY created_at DESC, rowid DESC"
            ).fetchall()
        else:
            rows = self.connection.execute(
                """SELECT * FROM exam_runs WHERE status=?
                   ORDER BY created_at DESC, rowid DESC""",
                (status,),
            ).fetchall()
        return tuple(self._exam_run(row) for row in rows)

    def exam_questions(self, exam_id: str) -> tuple[ExamQuestion, ...]:
        rows = self.connection.execute(
            """SELECT * FROM exam_questions WHERE exam_id=?
               ORDER BY question_id""",
            (exam_id,),
        ).fetchall()
        result = []
        for row in rows:
            points = json.loads(row["knowledge_points_json"])
            if not isinstance(points, list) or not all(isinstance(point, str) for point in points):
                raise ValueError("模拟题知识点记录已损坏")
            result.append(ExamQuestion(
                exam_id=row["exam_id"],
                question_id=row["question_id"],
                question_type=row["question_type"],
                points=int(row["points"]),
                knowledge_points=tuple(points),
                blueprint_category=row["blueprint_category"],
                prompt=row["prompt"],
                answer=row["answer"],
                explanation=row["explanation"],
                rubric=row["rubric"],
            ))
        return tuple(result)

    def record_exam_attempt(
        self,
        exam_id: str,
        exam_question_id: str,
        document_id: str,
        document_question_id: str,
        status: str,
    ) -> bool:
        if status not in {"correct", "incorrect", "partial"}:
            raise ValueError("非法复测状态")
        run = self._require_exam_run(exam_id)
        if run.status != "approved":
            raise ValueError("模拟卷尚未批准")
        question = self.connection.execute(
            "SELECT 1 FROM exam_questions WHERE exam_id=? AND question_id=?",
            (exam_id, exam_question_id),
        ).fetchone()
        effective = self.connection.execute(
            """SELECT status FROM effective_questions
               WHERE document_id=? AND question_id=?""",
            (document_id, document_question_id),
        ).fetchone()
        if question is None or effective is None or effective["status"] != status:
            raise ValueError("复测题目关联无效")
        with self.connection:
            changed = self.connection.execute(
                """INSERT OR IGNORE INTO exam_attempts(
                    exam_id, exam_question_id, document_id, document_question_id, status
                ) VALUES (?, ?, ?, ?, ?)""",
                (exam_id, exam_question_id, document_id, document_question_id, status),
            ).rowcount
        return changed == 1

    def exam_attempts(self, exam_id: str) -> tuple[ExamAttempt, ...]:
        rows = self.connection.execute(
            """SELECT * FROM exam_attempts WHERE exam_id=?
               ORDER BY linked_at, exam_question_id, document_id, document_question_id""",
            (exam_id,),
        ).fetchall()
        return tuple(ExamAttempt(**dict(row)) for row in rows)

    def sanitize_exam_links(self, analysis: AnalysisResult) -> AnalysisResult:
        """Turn untrusted or incomplete printed IDs into review-only evidence."""
        exam_id = analysis.source_exam_id
        run = self.get_exam_run(exam_id) if exam_id else None
        approved = run is not None and run.status == "approved" and run.subject == analysis.subject.value
        known = {
            question.question_id for question in self.exam_questions(exam_id)
        } if approved else set()
        claimed = [
            question.source_exam_question_id
            for question in analysis.questions
            if question.source_exam_question_id is not None
        ]
        duplicates = {value for value, count in Counter(claimed).items() if count > 1}
        questions = []
        for question in analysis.questions:
            printed_id = question.source_exam_question_id
            ordinary = exam_id is None and printed_id is None
            valid = (
                approved and printed_id is not None and printed_id in known
                and printed_id not in duplicates
            )
            if ordinary or valid:
                questions.append(question)
            else:
                reason = question.reason.rstrip()
                suffix = "复测编号无法确认，需家长复核后才能计入掌握度。"
                questions.append(replace(
                    question,
                    status=type(question.status).NEEDS_REVIEW,
                    reason=f"{reason} {suffix}".strip(),
                ))
        return replace(analysis, questions=tuple(questions))

    def link_exam_attempts(self, analysis: AnalysisResult) -> tuple[ExamAttempt, ...]:
        """Link only locally verified approved IDs; repeated analyses remain idempotent."""
        sanitized = self.sanitize_exam_links(analysis)
        now = datetime.now(timezone.utc)
        linked: list[ExamAttempt] = []
        with self.connection:
            affected = set()
            for original, question in zip(analysis.questions, sanitized.questions):
                if question.status != original.status:
                    self.connection.execute(
                        """UPDATE questions SET status=?, reason=?
                           WHERE document_id=? AND question_id=?""",
                        (question.status.value, question.reason, analysis.document_id, question.question_id),
                    )
                    affected.update(
                        (analysis.subject.value, point) for point in question.knowledge_points
                    )
                printed_id = question.source_exam_question_id
                if (sanitized.source_exam_id is None or printed_id is None
                        or question.status.value == "needs_review"):
                    continue
                effective = self.connection.execute(
                    """SELECT status FROM effective_questions
                       WHERE document_id=? AND question_id=?""",
                    (analysis.document_id, question.question_id),
                ).fetchone()
                if effective is None or effective["status"] not in {"correct", "incorrect", "partial"}:
                    continue
                changed = self.connection.execute(
                    """INSERT OR IGNORE INTO exam_attempts(
                        exam_id, exam_question_id, document_id, document_question_id, status
                    ) VALUES (?, ?, ?, ?, ?)""",
                    (sanitized.source_exam_id, printed_id, analysis.document_id,
                     question.question_id, effective["status"]),
                ).rowcount
                if changed:
                    row = self.connection.execute(
                        """SELECT * FROM exam_attempts WHERE exam_id=? AND exam_question_id=?
                           AND document_id=? AND document_question_id=?""",
                        (sanitized.source_exam_id, printed_id, analysis.document_id, question.question_id),
                    ).fetchone()
                    linked.append(ExamAttempt(**dict(row)))
            for subject, point in sorted(affected):
                self._recompute_knowledge_stat(subject, point, now)
        return tuple(linked)

    def _require_exam_run(self, exam_id: str) -> ExamRun:
        run = self.get_exam_run(exam_id)
        if run is None:
            raise ValueError("模拟卷不存在")
        return run

    @staticmethod
    def _exam_run(row: sqlite3.Row) -> ExamRun:
        values = {
            name: json.loads(row[f"{name}_json"])
            for name in ("request", "blueprint", "generation", "verification", "output_files")
        }
        if not all(isinstance(value, dict) for value in values.values()):
            raise ValueError("模拟卷记录已损坏")
        return ExamRun(
            exam_id=row["exam_id"],
            status=row["status"],
            subject=row["subject"],
            request=values["request"],
            blueprint=values["blueprint"],
            generation=values["generation"],
            verification=values["verification"],
            output_files={str(key): str(value) for key, value in values["output_files"].items()},
            error_code=row["error_code"],
            revision=int(row["revision"]),
            created_at=row["created_at"],
            approved_at=row["approved_at"],
        )

    def _require_report_run(self, report_id: str) -> ReportRun:
        run = self.get_report_run(report_id)
        if run is None:
            raise ValueError("报告不存在")
        return run

    @staticmethod
    def _report_run(row: sqlite3.Row) -> ReportRun:
        snapshot = json.loads(row["snapshot_json"])
        narrative = json.loads(row["narrative_json"])
        output_files = json.loads(row["output_files_json"])
        if not all(isinstance(value, dict) for value in (snapshot, narrative, output_files)):
            raise ValueError("报告记录已损坏")
        return ReportRun(
            report_id=row["report_id"],
            status=row["status"],
            previous_report_id=row["previous_report_id"],
            evidence_cutoff_at=row["evidence_cutoff_at"],
            snapshot=snapshot,
            narrative=narrative,
            output_files={str(key): str(value) for key, value in output_files.items()},
            error_code=row["error_code"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )

    def get_job(self, job_id: str) -> WorkflowJob | None:
        row = self.connection.execute(
            "SELECT * FROM workflow_jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            return None
        return WorkflowJob(row["job_id"], row["state"], row["subject"],
                           bool(row["knowledge_applied"]), row["last_error"],
                           json.loads(row["payload_json"]))

    def list_workflow_jobs(self) -> list[WorkflowJob]:
        return [self.get_job(job_id) for job_id in self.list_workflow_job_ids()]

    def list_workflow_job_ids(self) -> list[str]:
        """List identities without decoding a possibly damaged individual payload."""
        rows = self.connection.execute(
            "SELECT job_id FROM workflow_jobs ORDER BY created_at, rowid"
        ).fetchall()
        return [row["job_id"] for row in rows]

    def save_workflow_job(self, job: WorkflowJob) -> None:
        """Commit the authoritative journal and any subject-bound mirror together."""
        with self.connection:
            self._write_workflow_job(job)

    def save_workflow_job_if_current(self, expected: WorkflowJob, job: WorkflowJob, before_commit=None) -> bool:
        """Compare and write both journals under one lock; stale owners write nothing.

        Publication snapshots carry a unique owner token, preventing an earlier
        exporter from completing a later export that happens to have the same state.
        Mirror writes share the lock so they cannot lag a newer parent's commit.
        """
        if expected.job_id != job.job_id:
            raise ValueError("处理任务标识不一致")
        # Match persisted facts, not tuple/list differences introduced by JSON.
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if not self.workflow_job_matches(expected):
                return False
            self._write_workflow_job(job)
            if before_commit is not None:
                before_commit(job)
        return True

    def workflow_job_matches(self, expected: WorkflowJob) -> bool:
        current = self.get_job(expected.job_id)
        return current is not None and asdict(current) == json.loads(self._json(expected))

    def reading_revision(self) -> str:
        """Fingerprint every fact consumed by the shared reading layer.

        Rendering deliberately holds no read transaction. Rechecking this global
        fingerprint under the publication lock rejects mixed/stale renderings,
        including changes to a different subject and UTC date-dependent summaries.
        Journal timestamps/export flags are not reading facts; processing state is.
        """
        digest = sha256(self._json((self.config.subjects, datetime.now(timezone.utc).date().isoformat())).encode())
        for table in ("documents", "pages", "questions", "question_knowledge_points", "knowledge_stats",
                      "parent_reviews", "review_audit"):
            rows = [dict(row) for row in self.connection.execute(f"SELECT * FROM {table}")]
            encoded = sorted(self._json(row) for row in rows)
            digest.update(self._json((table, encoded)).encode("utf-8"))
        rows = [tuple(row) for row in self.connection.execute(
            """SELECT p.document_id, COALESCE(w.state, p.state) FROM processing_jobs p
            LEFT JOIN workflow_jobs w ON w.job_id=p.document_id ORDER BY p.document_id""")]
        digest.update(self._json(rows).encode("utf-8"))
        return digest.hexdigest()

    def _write_workflow_job(self, job: WorkflowJob) -> None:
        """Participate in the caller's transaction; never commit a nested write."""
        if job.subject is not None:
            self._validate_subject(job.subject)
        previous = self.get_job(job.job_id)
        if job.state == "completed" and self._document_review_count(job.job_id):
            raise ValueError("仍有待确认题目，不能完成任务")
        if job.state not in _WORKFLOW_TRANSITIONS or (
            previous is not None and job.state != previous.state
            and job.state not in _WORKFLOW_TRANSITIONS[previous.state]
        ):
            raise ValueError("非法处理状态转换")
        self.connection.execute(
            """INSERT INTO workflow_jobs
                (job_id, state, subject, knowledge_applied, last_error, payload_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(job_id) DO UPDATE SET state=excluded.state,
                subject=excluded.subject, knowledge_applied=excluded.knowledge_applied,
                last_error=excluded.last_error, payload_json=excluded.payload_json,
                updated_at=CURRENT_TIMESTAMP""",
            (job.job_id, job.state, job.subject, int(job.knowledge_applied),
             job.last_error, self._json(job.payload)),
        )
        self.connection.execute(
            """UPDATE processing_jobs SET state=?, knowledge_applied=?, last_error=?,
                updated_at=CURRENT_TIMESTAMP WHERE document_id=?""",
            (job.state, int(job.knowledge_applied), job.last_error, job.job_id),
        )

    def stage_workflow_archive(self, job: WorkflowJob, document: CapturedDocument) -> None:
        """Commit verified page locations without deleting existing question evidence."""
        self._validate_subject(job.subject)
        with self.connection:
            self.connection.execute(
                """INSERT INTO documents (document_id, subject, document_type, raw_directory)
                    VALUES (?, ?, ?, ?) ON CONFLICT(document_id) DO UPDATE SET
                    subject=excluded.subject, document_type=excluded.document_type,
                    raw_directory=excluded.raw_directory, updated_at=CURRENT_TIMESTAMP""",
                (job.job_id, job.subject, document.document_type, str(document.session_dir)),
            )
            self.connection.executemany(
                """INSERT INTO pages (document_id, page_number, path, sha256) VALUES (?, ?, ?, ?)
                    ON CONFLICT(document_id, page_number) DO UPDATE SET path=excluded.path,
                    sha256=excluded.sha256""",
                [(job.job_id, *self._page_row(page)) for page in document.pages],
            )
            self.connection.execute(
                "INSERT OR IGNORE INTO processing_jobs (document_id, state) VALUES (?, ?)",
                (job.job_id, job.state),
            )
            # save_workflow_job must participate in this transaction, not commit it early.
            self.connection.execute(
                """UPDATE workflow_jobs SET payload_json=?, state=?, subject=?,
                    last_error=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=?""",
                (self._json(job.payload), job.state, job.subject, job.last_error, job.job_id),
            )
            self.connection.execute(
                """UPDATE processing_jobs SET state=?, knowledge_applied=?, last_error=?,
                    updated_at=CURRENT_TIMESTAMP WHERE document_id=?""",
                (job.state, int(job.knowledge_applied), job.last_error, job.job_id),
            )

    def create_document(
        self,
        document_id: str | CapturedDocument,
        subject: str,
        document_type: str | None = None,
        pages: Iterable[CapturedPage | tuple[int, str, str]] = (),
        raw_directory: str | Path | None = None,
        state: str = "pending",
        now: datetime | None = None,
    ) -> None:
        """Create or refresh an archive record and its uniquely recoverable job."""
        if isinstance(document_id, CapturedDocument):
            document = document_id
            document_id = document.document_id
            document_type = document_type or document.document_type
            pages = document.pages
        self._validate_subject(subject)
        if not document_type:
            raise ValueError("资料类型不能为空")
        page_rows = [self._page_row(page) for page in pages]
        recompute_now = now or datetime.now(timezone.utc)
        with self.connection:
            old_pairs = self._document_knowledge_pairs(document_id)
            self.connection.execute(
                """
                INSERT INTO documents (document_id, subject, document_type, raw_directory)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    subject = excluded.subject,
                    document_type = excluded.document_type,
                    raw_directory = excluded.raw_directory,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (document_id, subject, document_type, str(raw_directory) if raw_directory else None),
            )
            self.connection.execute("DELETE FROM pages WHERE document_id = ?", (document_id,))
            self.connection.executemany(
                "INSERT INTO pages (document_id, page_number, path, sha256) VALUES (?, ?, ?, ?)",
                [(document_id, *row) for row in page_rows],
            )
            self.connection.execute(
                """
                INSERT INTO processing_jobs (document_id, state)
                VALUES (?, ?)
                ON CONFLICT(document_id) DO UPDATE SET
                    state = excluded.state,
                    knowledge_applied = 0,
                    last_error = NULL,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (document_id, state),
            )
            for old_subject, old_point in sorted(old_pairs):
                self._recompute_knowledge_stat(old_subject, old_point, recompute_now)

    def save_analysis(self, analysis: AnalysisResult) -> None:
        """Atomically upsert an analysis, replacing stale questions and their links."""
        with self.connection:
            self._save_analysis(analysis)

    def apply_split_batch(
        self, parent: WorkflowJob,
        children: tuple[tuple[WorkflowJob, CapturedDocument, AnalysisResult], ...],
        *, now: datetime,
    ) -> None:
        """Commit every child's facts and the parent's outbox in one locked transaction."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            committed = replace(parent, state="pending", knowledge_applied=True, last_error=None,
                                payload=dict(parent.payload,
                                             child_document_ids=[child.job_id for child, _, _ in children],
                                             publish_state="facts_applied", export_pending=True))
            if not (self.workflow_job_matches(parent) or self.workflow_job_matches(committed)):
                raise ValueError("拆分任务已变化，请重新读取")
            self._validate_split_batch(parent, children)
            archived_parent = self.get_document(parent.job_id)
            if archived_parent is not None:
                self._retire_failed_split_archive(parent, archived_parent)
            current = self.get_job(parent.job_id)
            if current.knowledge_applied:
                # An identical replay must not replace human reviews or reset publication.
                for child, _, _ in children:
                    saved = self.get_job(child.job_id)
                    if (saved is None or not saved.knowledge_applied
                            or saved.payload.get("parent_job_id") != parent.job_id
                            or self.get_document(child.job_id) is None):
                        raise ValueError("拆分任务缺少已提交的子文档")
                return
            affected = set()
            for child, document, analysis in children:
                existing = self.get_job(child.job_id)
                if ((existing and existing.payload.get("parent_job_id") != parent.job_id)
                        or (existing is None and self.get_document(child.job_id) is not None)):
                    raise ValueError("拆分子文档标识冲突")
                affected.update(self._document_knowledge_pairs(child.job_id))
                self.connection.execute(
                    """INSERT INTO documents (document_id, subject, document_type, raw_directory)
                       VALUES (?, ?, ?, ?) ON CONFLICT(document_id) DO UPDATE SET
                       subject=excluded.subject, document_type=excluded.document_type,
                       raw_directory=excluded.raw_directory, updated_at=CURRENT_TIMESTAMP""",
                    (child.job_id, child.subject, document.document_type, str(document.session_dir)),
                )
                self.connection.executemany(
                    """INSERT INTO pages (document_id, page_number, path, sha256) VALUES (?, ?, ?, ?)
                       ON CONFLICT(document_id, page_number) DO UPDATE SET
                       path=excluded.path, sha256=excluded.sha256""",
                    [(child.job_id, *self._page_row(page)) for page in document.pages],
                )
                self._save_analysis(analysis)
                page_numbers = tuple(p.page_number for p in document.pages)
                placeholders = ",".join("?" for _ in page_numbers)
                self.connection.execute(
                    f"DELETE FROM pages WHERE document_id=? AND page_number NOT IN ({placeholders})",
                    (child.job_id, *page_numbers),
                )
                affected.update(self._document_knowledge_pairs(child.job_id))
                self._write_workflow_job(replace(
                    child, state="pending", knowledge_applied=True, last_error=None,
                    payload=dict(child.payload, publish_state="facts_applied", export_pending=True),
                ))
            for subject, point in sorted(affected):
                self._recompute_knowledge_stat(subject, point, now)
            self._write_workflow_job(committed)

    def _retire_failed_split_archive(self, parent, document) -> None:
        """Convert only an empty failed-analysis archive inside the split transaction."""
        processing = self.connection.execute(
            "SELECT state, knowledge_applied FROM processing_jobs WHERE document_id=?",
            (parent.job_id,),
        ).fetchone()
        directory = self.config.knowledge_root.absolute() / document["subject"] / "待处理" / parent.job_id
        pages = sorted(parent.payload["pages"], key=lambda page: page["page_number"])
        has_review = any(self.connection.execute(
            f"SELECT 1 FROM {table} WHERE document_id=? LIMIT 1", (parent.job_id,),
        ).fetchone() for table in ("parent_reviews", "review_audit", "review_publications"))
        if (parent.knowledge_applied or parent.payload.get("archive_kind") != "pending"
                or not parent.payload.get("subject_confirmed")
                or processing is None or processing["knowledge_applied"]
                or processing["state"] != "pending" or document["questions"] or has_review
                or document["grading_mode"] is not None or document["subject_confidence"] is not None
                or document["summary"] or document["teacher_mark_evidence"]
                or document["subject"] not in self.config.subjects
                or document["raw_directory"] != str(directory) or document["pages"] != pages
                or any(Path(page["path"]).parent != directory for page in pages)):
            raise ValueError("拆分父任务不能是知识文档")
        # Cascades remove only its verified page/processing placeholders. The
        # authoritative workflow and source files survive; any child failure
        # rolls this deletion back together with all child facts.
        self.connection.execute("DELETE FROM documents WHERE document_id=?", (parent.job_id,))

    def _validate_split_batch(self, parent, children) -> None:
        # Import lazily: workflow's public module also exposes the controller.
        from qingzi_learning.analysis.codex_cli import _from_payload
        from qingzi_learning.domain import Subject
        from qingzi_learning.workflow.subject_split import build_subject_split_plan, split_analysis

        if parent.subject is not None or parent.state != "pending":
            raise ValueError("拆分父任务状态无效")
        analysis = _from_payload(parent.payload["analysis"])
        document = CapturedDocument(parent.job_id, tuple(
            CapturedPage(p["page_number"], Path(p["path"]), p["sha256"])
            for p in parent.payload["pages"]), analysis.document_type)
        plan = build_subject_split_plan(
            document, analysis,
            {int(page): Subject(subject) for page, subject
             in parent.payload.get("page_subject_overrides", {}).items()},
            self.config.subject_confidence_threshold,
        )
        if (plan.unresolved_pages or len(plan.groups) < 2
                or parent.payload.get("split_plan") != json.loads(self._json(plan))):
            raise ValueError("拆分计划不完整或已变化")
        expected_analyses = split_analysis(analysis, plan)
        if len(children) != len(expected_analyses):
            raise ValueError("拆分子文档不完整")
        source_pages = {p.page_number: p for p in document.pages}
        for (child, captured, result), group, expected in zip(children, plan.groups, expected_analyses):
            if (child.job_id != group.document_id or captured.document_id != child.job_id
                    or child.subject != group.subject.value or result != expected
                    or captured.document_type != result.document_type
                    or child.payload.get("parent_job_id") != parent.job_id
                    or child.payload.get("archive_kind") != "raw"
                    or child.payload.get("analysis") != json.loads(self._json(result))
                    or tuple(p.page_number for p in captured.pages) != group.page_numbers
                    or child.payload.get("pages") != [
                        dict(page_number=p.page_number, path=str(p.path), sha256=p.sha256)
                        for p in captured.pages]):
                raise ValueError("拆分子文档与计划不一致")
            for page in captured.pages:
                original = source_pages[page.page_number]
                if page.sha256 != original.sha256 or page.path.name != original.path.name:
                    raise ValueError("拆分页面与来源不一致")

    def replace_analysis_and_recompute(self, analysis: AnalysisResult, *, now: datetime) -> None:
        """Replace one document and its affected knowledge facts in one transaction."""
        self._validate_subject(analysis.subject.value)
        with self.connection:
            affected = self._document_knowledge_pairs(analysis.document_id)
            self._save_analysis(analysis)
            affected.update(self._document_knowledge_pairs(analysis.document_id))
            for subject, point in sorted(affected):
                self._recompute_knowledge_stat(subject, point, now)
            review_count = self._document_review_count(analysis.document_id)
            workflow = self.get_job(analysis.document_id)
            if workflow is not None:
                self.connection.execute(
                    "UPDATE workflow_jobs SET knowledge_applied=1 WHERE job_id=?",
                    (analysis.document_id,),
                )
            self.connection.execute(
                """
                UPDATE processing_jobs
                SET knowledge_applied = 1,
                    state = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE document_id = ?
                """,
                (workflow.state if workflow else "needs_review" if review_count else "completed",
                 analysis.document_id),
            )

    def recompute_knowledge_stats(
        self, subject: str, knowledge_points: Iterable[str], *, now: datetime
    ) -> None:
        """Regenerate selected read-model rows from persisted facts only."""
        self._validate_subject(subject)
        with self.connection:
            for point in sorted(set(knowledge_points)):
                self._recompute_knowledge_stat(subject, point, now)

    def get_knowledge_stats(self, subject: str, knowledge_point: str) -> KnowledgeStats:
        """Return one point's generated statistics, including a meaningful empty value."""
        self._validate_subject(subject)
        row = self.connection.execute(
            """
            SELECT * FROM knowledge_stats
            WHERE subject = ? AND knowledge_point = ?
            """,
            (subject, knowledge_point),
        ).fetchone()
        if row is None:
            return KnowledgeStats(
                subject, knowledge_point, None, None, 0, 0, 0, 0, 0, 0, 0, 0, (), 0, 0, "no_data"
            )
        values = dict(row)
        values["common_error_categories"] = tuple(
            self._from_json(values.pop("common_error_categories_json"))
        )
        return KnowledgeStats(**values)

    def count_review_items(self) -> int:
        """Count unresolved questions separately from confirmed mastery evidence."""
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM effective_questions WHERE status = 'needs_review'"
        ).fetchone()
        return int(row["count"])

    def document_review_count(self, document_id: str) -> int:
        """Return unresolved effective judgments for one document."""
        return self._document_review_count(document_id)

    def review_question(self, document_id: str, question_id: str) -> dict[str, Any]:
        """Read effective judgment and a version tied to original page evidence."""
        document = self.get_document(document_id)
        question = next((q for q in document["questions"] if q["question_id"] == question_id), None) if document else None
        if question is None:
            raise ValueError("题目不存在")
        page = next(p for p in document["pages"] if p["page_number"] == question["page"])
        original = dict(self.connection.execute(
            "SELECT * FROM questions WHERE document_id=? AND question_id=?", (document_id, question_id)).fetchone())
        original["source_sha256"] = page["sha256"]
        original["source_path"] = page["path"]
        original["subject"] = document["subject"]
        original["knowledge_points"] = question["knowledge_points"]
        original["teacher_mark_evidence"] = document["teacher_mark_evidence"]
        fingerprint = sha256(self._json(original).encode("utf-8")).hexdigest()
        return dict(question, subject=document["subject"], source_path=page["path"],
                    version=f"{fingerprint}:{question['review_revision']}", original=original)

    def pending_review_ids(self, document_ids: tuple[str, ...] | None = None) -> tuple[tuple[str, str], ...]:
        if document_ids is not None and not document_ids:
            return ()
        scoped = document_ids is not None
        where = "status='needs_review'"
        params: tuple[str, ...] = ()
        if scoped:
            where = ("(status='needs_review' OR (original_decision_source='model' "
                     "AND original_status='correct' AND review_revision=0)) "
                     f"AND document_id IN ({','.join('?' for _ in document_ids)})")
            params = document_ids
        return tuple((r[0], r[1]) for r in self.connection.execute(
            f"SELECT document_id, question_id FROM effective_questions WHERE {where} "
            "ORDER BY document_id, page, question_id", params))

    def review_history(self, document_id: str, question_id: str) -> tuple[dict[str, Any], ...]:
        return tuple(dict(row) for row in self.connection.execute(
            "SELECT * FROM review_audit WHERE document_id=? AND question_id=? ORDER BY audit_id", (document_id, question_id)))

    def confirm_review_and_recompute(self, document_id: str, question_id: str,
                                     final_status: str, corrected_answer: str, note: str,
                                     *, expected_version: str | None, now: datetime) -> tuple[str, tuple[str, ...]]:
        """Serialize version checking, audit, effective facts, stats and outbox together."""
        if final_status not in {"correct", "incorrect", "partial"}:
            raise ValueError("最终状态必须是正确、错误或部分正确")
        if not isinstance(corrected_answer, str) or not isinstance(note, str):
            raise ValueError("答案和备注必须是文字")
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            item = self.review_question(document_id, question_id)
            current = self.connection.execute("SELECT * FROM parent_reviews WHERE document_id=? AND question_id=?",
                                              (document_id, question_id)).fetchone()
            fields = (final_status, corrected_answer, note)
            identical = current is not None and fields == (current["final_status"], current["corrected_answer"], current["note"])
            if expected_version is not None and expected_version != item["version"]:
                # Only an exact duplicate of the immediately preceding revision can
                # replay. A page/analysis change can never be mistaken for a double click.
                previous_version = item["version"].rsplit(":", 1)[0] + f":{item['review_revision'] - 1}"
                if not identical or expected_version != previous_version:
                    raise ValueError("题目已变化，请重新打开复核窗口")
            elif expected_version is None and item["status"] != "needs_review" and not identical:
                raise ValueError("请重新打开题目后再修改已确认结果")
            workflow = self.get_job(document_id)
            if workflow and (not workflow.knowledge_applied or workflow.state not in {"needs_review", "completed", "pending"}):
                raise ValueError("资料正在处理，请稍后复核")
            points = tuple(item["knowledge_points"])
            if identical:
                return item["subject"], points
            revision = item["review_revision"] + 1
            confirmed_at = now.isoformat()
            self.connection.execute(
                """INSERT INTO parent_reviews VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(document_id, question_id) DO UPDATE SET final_status=excluded.final_status,
                corrected_answer=excluded.corrected_answer, note=excluded.note,
                revision=excluded.revision, confirmed_at=excluded.confirmed_at""",
                (document_id, question_id, *fields, revision, confirmed_at))
            self.connection.execute(
                """INSERT INTO review_audit(document_id, question_id, revision, original_json,
                before_json, after_json, confirmed_at) VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (document_id, question_id, revision, self._json(item["original"]),
                 self._json(dict(current) if current else {"status": item["status"], "decision_source": item["decision_source"]}),
                 self._json(dict(final_status=final_status, corrected_answer=corrected_answer, note=note, decision_source="parent")), confirmed_at))
            for point in points:
                self._recompute_knowledge_stat(item["subject"], point, now)
            self.connection.execute(
                """INSERT INTO review_publications(document_id) VALUES (?) ON CONFLICT(document_id)
                DO UPDATE SET revision=review_publications.revision+1, pending=1""", (document_id,))
            self._set_review_job_state(document_id, "pending", True)
            self.connection.execute("UPDATE documents SET updated_at=CURRENT_TIMESTAMP WHERE document_id=?", (document_id,))
            return item["subject"], points

    def pending_review_publications(self) -> tuple[str, ...]:
        return tuple(r[0] for r in self.connection.execute(
            "SELECT document_id FROM review_publications WHERE pending=1 ORDER BY document_id"))

    def review_publication_revision(self, document_id: str) -> int:
        """Monotonic UI stamp: pending and published phases each advance it."""
        row = self.connection.execute("SELECT revision, pending FROM review_publications WHERE document_id=?", (document_id,)).fetchone()
        return int(row[0]) * 2 + int(not row[1]) if row else 0

    def _set_review_job_state(self, document_id: str, state: str, export_pending: bool,
                              error: str | None = None, **paths: Any) -> None:
        """Participate in the caller's transaction, keeping both journals identical."""
        job = self.get_job(document_id)
        if job:
            payload = dict(job.payload, export_pending=export_pending, parent_review=True, **paths)
            self.connection.execute(
                "UPDATE workflow_jobs SET state=?, payload_json=?, knowledge_applied=1, last_error=?, updated_at=CURRENT_TIMESTAMP WHERE job_id=?",
                (state, self._json(payload), error, document_id))
        self.connection.execute(
            """INSERT INTO processing_jobs(document_id, state, knowledge_applied, last_error)
            VALUES (?, ?, 1, ?) ON CONFLICT(document_id) DO UPDATE SET state=excluded.state,
            knowledge_applied=1, last_error=excluded.last_error, updated_at=CURRENT_TIMESTAMP""",
            (document_id, state, error))

    def prepare_review_publication(self, document_id: str, *, files_current: bool):
        """Atomically capture exact outbox revision and unique attempt ownership."""
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            row = self.connection.execute("SELECT pending, revision FROM review_publications WHERE document_id=?", (document_id,)).fetchone()
            if row is None:
                return None
            state = "needs_review" if self._document_review_count(document_id) else "completed"
            if not row[0]:
                job = self.get_job(document_id)
                mirror = self.connection.execute(
                    "SELECT state, knowledge_applied, last_error FROM processing_jobs WHERE document_id=?", (document_id,)).fetchone()
                consistent = (job is None or (job.state == state and job.knowledge_applied
                              and job.last_error is None and job.payload.get("parent_review")
                              and not job.payload.get("export_pending")))
                if files_current and consistent and mirror is not None and tuple(mirror) == (state, 1, None):
                    return None
                # An already-published review still owns its journals. Repair a
                # stale historical write-back without changing its outbox revision,
                # re-applying knowledge, or re-analyzing the original worksheet.
            self._set_review_job_state(document_id, state, True,
                                       parent_publication_id=uuid4().hex,
                                       parent_publication_revision=row["revision"])
            self.connection.execute("UPDATE review_publications SET pending=1 WHERE document_id=?", (document_id,))
            return self.get_job(document_id)

    def mark_review_export_failed(self, document_id: str, *, expected=None, review_revision=None, before_commit=None) -> None:
        # An unowned/legacy notification cannot write a current attempt's state.
        if expected is None or review_revision is None or expected.job_id != document_id:
            return
        with self.connection:
            self.connection.execute("BEGIN IMMEDIATE")
            if not self.workflow_job_matches(expected):
                return
            if self.review_publication_revision(document_id) != review_revision:
                return
            row = self.connection.execute("SELECT pending FROM review_publications WHERE document_id=?", (document_id,)).fetchone()
            if row is None or not row[0]:
                return  # A different publisher may already have completed the entry.
            self._set_review_job_state(document_id, "pending", True, "review_export_failed")
            if before_commit is not None:
                try:
                    before_commit()
                except (OSError, ValueError, RuntimeError):
                    pass  # Keep authoritative pending state even when its mirror is unavailable.

    def _save_analysis(self, analysis: AnalysisResult) -> None:
        if self.connection.execute("SELECT 1 FROM parent_reviews WHERE document_id=? LIMIT 1",
                                   (analysis.document_id,)).fetchone():
            raise ValueError("资料已有家长复核，请保留复核结果，勿重新覆盖分析")
        subject = analysis.subject.value
        self._validate_subject(subject)
        self.connection.execute(
            """
            INSERT INTO documents (
                document_id, subject, subject_confidence, document_type, grading_mode,
                teacher_mark_evidence_json, summary
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                subject = excluded.subject,
                subject_confidence = excluded.subject_confidence,
                document_type = excluded.document_type,
                grading_mode = excluded.grading_mode,
                teacher_mark_evidence_json = excluded.teacher_mark_evidence_json,
                summary = excluded.summary,
                updated_at = CURRENT_TIMESTAMP
            """,
            (
                analysis.document_id,
                subject,
                analysis.subject_confidence,
                analysis.document_type,
                analysis.grading_mode.value,
                self._json(analysis.teacher_mark_evidence),
                analysis.summary,
            ),
        )
        self.connection.execute(
            "DELETE FROM question_knowledge_points WHERE document_id = ?",
            (analysis.document_id,),
        )
        self.connection.execute("DELETE FROM questions WHERE document_id = ?", (analysis.document_id,))
        self.connection.executemany(
            """
            INSERT INTO questions (
                document_id, question_id, question_type, page, prompt_summary, student_answer,
                reference_answer, status, decision_source, error_categories_json,
                confidence, reason
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                (
                    analysis.document_id,
                    question.question_id,
                    getattr(question.question_type, "value", question.question_type),
                    question.page,
                    question.prompt_summary,
                    question.student_answer,
                    question.reference_answer,
                    question.status.value,
                    ("model_pending" if question.decision_source == "model" and (
                        self.config.review_all_model_questions or (
                            analysis.grading_mode.value == "auto_grade"
                            and question.status.value in {"incorrect", "partial"}
                            and question.answer_bbox is not None
                        )
                    ) else question.decision_source),
                    self._json(question.error_categories),
                    question.confidence,
                    question.reason,
                )
                for question in analysis.questions
            ],
        )
        self.connection.executemany(
            """
            INSERT INTO question_knowledge_points (document_id, question_id, knowledge_point)
            VALUES (?, ?, ?)
            """,
            [
                (analysis.document_id, question.question_id, point)
                for question in analysis.questions
                for point in dict.fromkeys(question.knowledge_points)
            ],
        )
        self.connection.execute(
            """
            INSERT INTO processing_jobs (document_id, state)
            VALUES (?, COALESCE((SELECT state FROM workflow_jobs WHERE job_id = ?), 'analysis_saved'))
            ON CONFLICT(document_id) DO UPDATE SET
                state = excluded.state,
                updated_at = CURRENT_TIMESTAMP
            """,
            (analysis.document_id, analysis.document_id),
        )

    def _document_knowledge_pairs(self, document_id: str) -> set[tuple[str, str]]:
        return {
            (row["subject"], row["knowledge_point"])
            for row in self.connection.execute(
                """
                SELECT documents.subject, links.knowledge_point
                FROM documents
                JOIN question_knowledge_points AS links
                  ON links.document_id = documents.document_id
                WHERE documents.document_id = ?
                """,
                (document_id,),
            )
        }

    def _document_review_count(self, document_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM effective_questions WHERE document_id = ? AND status = 'needs_review'",
            (document_id,),
        ).fetchone()
        return int(row["count"])

    def _recompute_knowledge_stat(self, subject: str, point: str, now: datetime) -> None:
        rows = list(
            self.connection.execute(
                """
                SELECT documents.document_id, questions.status, questions.decision_source,
                       questions.confidence, questions.error_categories_json, documents.created_at
                FROM effective_questions AS questions
                JOIN documents ON documents.document_id = questions.document_id
                JOIN question_knowledge_points AS links
                  ON links.document_id = questions.document_id
                 AND links.question_id = questions.question_id
                WHERE documents.subject = ? AND links.knowledge_point = ?
                ORDER BY documents.created_at, documents.rowid, questions.page, questions.question_id
                """,
                (subject, point),
            )
        )
        confirmed = [
            row for row in rows
            if row["status"] != "needs_review" and (row["confidence"] >= 0.80 or row["decision_source"] == "parent")
        ]
        errors = Counter()
        for row in confirmed:
            if row["status"] in ("incorrect", "partial"):
                errors.update(self._from_json(row["error_categories_json"]))

        statuses = [row["status"] for row in confirmed]
        attempts = self._attempts(confirmed)
        attempt_statuses = [status for status, _ in attempts]
        incorrect = statuses.count("incorrect")
        partial = statuses.count("partial")
        correct = statuses.count("correct")
        attempt_incorrect = attempt_statuses.count("incorrect")
        attempt_partial = attempt_statuses.count("partial")
        latest = attempt_statuses[-1] if attempt_statuses else None
        consecutive_correct = 0
        for status in reversed(attempt_statuses):
            if status != "correct":
                break
            consecutive_correct += 1
        priority = attempt_incorrect * 20 + attempt_partial * 10
        priority += max(0, attempt_incorrect - 1) * 10
        if latest in ("incorrect", "partial"):
            priority += 20
            latest_at = attempts[-1][1]
            if self._is_recent(latest_at, now):
                priority += 15
        priority = max(0, priority - consecutive_correct * 5)
        trend = self._trend(attempt_statuses)
        values = (
            subject,
            point,
            confirmed[0]["created_at"] if confirmed else None,
            confirmed[-1]["created_at"] if confirmed else None,
            len(confirmed),
            correct,
            incorrect,
            partial,
            sum(row["status"] == "correct" and row["decision_source"] in ("teacher", "mixed") for row in confirmed),
            sum(row["status"] == "incorrect" and row["decision_source"] in ("teacher", "mixed") for row in confirmed),
            sum(row["status"] == "correct" and row["decision_source"] in ("model", "mixed") for row in confirmed),
            sum(row["status"] == "incorrect" and row["decision_source"] in ("model", "mixed") for row in confirmed),
            self._json(tuple(category for category, _ in sorted(errors.items(), key=lambda item: (-item[1], item[0])))),
            sum(row["status"] == "needs_review" for row in rows),
            priority,
            trend,
        )
        self.connection.execute(
            """
            INSERT INTO knowledge_stats (
                subject, knowledge_point, first_seen_at, last_seen_at,
                exposure_count, correct_count, incorrect_count, partial_count,
                teacher_correct_count, teacher_incorrect_count,
                model_correct_count, model_incorrect_count,
                common_error_categories_json, needs_review, review_priority, trend
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(subject, knowledge_point) DO UPDATE SET
                first_seen_at = excluded.first_seen_at,
                last_seen_at = excluded.last_seen_at,
                exposure_count = excluded.exposure_count,
                correct_count = excluded.correct_count,
                incorrect_count = excluded.incorrect_count,
                partial_count = excluded.partial_count,
                teacher_correct_count = excluded.teacher_correct_count,
                teacher_incorrect_count = excluded.teacher_incorrect_count,
                model_correct_count = excluded.model_correct_count,
                model_incorrect_count = excluded.model_incorrect_count,
                common_error_categories_json = excluded.common_error_categories_json,
                needs_review = excluded.needs_review,
                review_priority = excluded.review_priority,
                trend = excluded.trend
            """,
            values,
        )

    @staticmethod
    def _attempts(rows: list[sqlite3.Row]) -> list[tuple[str, str]]:
        """Collapse confirmed questions into chronological document-level attempts."""
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            grouped.setdefault(row["document_id"], []).append(row)
        attempts: list[tuple[str, str]] = []
        for questions in grouped.values():
            statuses = {row["status"] for row in questions}
            if "incorrect" in statuses:
                status = "incorrect"
            elif "partial" in statuses:
                status = "partial"
            else:
                status = "correct"
            attempts.append((status, questions[0]["created_at"]))
        return attempts

    @staticmethod
    def _trend(statuses: list[str]) -> str:
        if not statuses:
            return "no_data"
        if statuses[-1] in ("incorrect", "partial"):
            return "declining"
        if len(statuses) > 1 and any(status in ("incorrect", "partial") for status in statuses[:-1]):
            return "improving"
        return "steady"

    @staticmethod
    def _is_recent(value: str, now: datetime) -> bool:
        observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        return (now.astimezone(timezone.utc) - observed).days <= 30

    def count_questions(self, document_id: str) -> int:
        row = self.connection.execute(
            "SELECT COUNT(*) AS count FROM questions WHERE document_id = ?", (document_id,)
        ).fetchone()
        return int(row["count"])

    def get_document(self, document_id: str) -> dict[str, Any] | None:
        """Return one document with its page and question evidence, or ``None``."""
        row = self.connection.execute(
            "SELECT * FROM documents WHERE document_id = ?", (document_id,)
        ).fetchone()
        if row is None:
            return None
        document = dict(row)
        document["teacher_mark_evidence"] = self._from_json(
            document.pop("teacher_mark_evidence_json")
        )
        document["pages"] = [
            dict(page)
            for page in self.connection.execute(
                "SELECT page_number, path, sha256 FROM pages WHERE document_id = ? ORDER BY page_number",
                (document_id,),
            )
        ]
        questions: list[dict[str, Any]] = []
        for question in self.connection.execute(
            "SELECT * FROM effective_questions WHERE document_id = ? ORDER BY page, question_id", (document_id,)
        ):
            result = dict(question)
            result["error_categories"] = self._from_json(
                result.pop("error_categories_json")
            )
            result["knowledge_points"] = [
                point["knowledge_point"]
                for point in self.connection.execute(
                    """
                    SELECT knowledge_point FROM question_knowledge_points
                    WHERE document_id = ? AND question_id = ? ORDER BY knowledge_point
                    """,
                    (document_id, result["question_id"]),
                )
            ]
            questions.append(result)
        document["questions"] = questions
        return document

    def list_pending(self) -> list[dict[str, Any]]:
        placeholders = ", ".join("?" for _ in _PENDING_STATES)
        return [
            dict(row)
            for row in self.connection.execute(
                f"""
                SELECT jobs.*, documents.subject, documents.document_type
                FROM processing_jobs AS jobs
                JOIN documents ON documents.document_id = jobs.document_id
                WHERE jobs.state IN ({placeholders})
                ORDER BY jobs.created_at, jobs.document_id
                """,
                _PENDING_STATES,
            )
        ]

    def dashboard_snapshot(self) -> dict[str, Any]:
        """Return bounded dashboard previews plus explicitly labelled aggregate facts."""
        now = datetime.now(timezone.utc)
        month_key = now.strftime("%Y-%m")
        summary = self.connection.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM documents) AS document_count,
                (SELECT COUNT(*) FROM questions) AS question_count,
                (SELECT COUNT(*) FROM effective_questions WHERE status IN ('incorrect', 'partial')) AS error_count,
                (SELECT COUNT(*) FROM effective_questions WHERE status = 'needs_review') AS review_count,
                (SELECT COUNT(*) FROM knowledge_stats WHERE review_priority > 0 OR incorrect_count > 0) AS weak_knowledge_point_count,
                (SELECT COUNT(*) FROM documents WHERE substr(created_at, 1, 7) = ?) AS monthly_document_count,
                (SELECT COUNT(*) FROM processing_jobs p LEFT JOIN workflow_jobs w ON w.job_id=p.document_id
                 WHERE COALESCE(w.state, p.state) IN ('pending', 'spooled', 'analyzing', 'needs_subject_confirmation', 'needs_review')) AS pending_count
            """
            , (month_key,)
        ).fetchone()
        subjects = {
            subject: {
                "document_count": 0,
                "question_count": 0,
                "mastery_rate": None,
                "mastery_sample_size": 0,
                "monthly_document_count": 0,
                "recent_trend": self._recent_trend(subject, now),
            }
            for subject in self.config.subjects
        }
        for row in self.connection.execute(
            """
            SELECT documents.subject, COUNT(DISTINCT documents.document_id) AS document_count,
                   COUNT(questions.question_id) AS question_count,
                   (SELECT COUNT(*) FROM documents AS monthly
                    WHERE monthly.subject = documents.subject
                      AND substr(monthly.created_at, 1, 7) = ?) AS monthly_document_count
            FROM documents LEFT JOIN questions ON questions.document_id = documents.document_id
            GROUP BY documents.subject
            """, (month_key,)
        ):
            subjects[row["subject"]] = {
                "document_count": row["document_count"],
                "question_count": row["question_count"],
                "mastery_rate": None,
                "mastery_sample_size": 0,
                "monthly_document_count": row["monthly_document_count"],
                "recent_trend": self._recent_trend(row["subject"], now),
            }
        knowledge_points = self._knowledge_point_rows()
        for subject, data in subjects.items():
            points = [item for item in knowledge_points if item["subject"] == subject]
            sample_size = sum(item["exposure_count"] for item in points)
            data["mastery_sample_size"] = sample_size
            if sample_size:
                data["mastery_rate"] = sum(
                    item["correct_count"] + item["partial_count"] * 0.5 for item in points
                ) / sample_size
        recent_documents = self._document_rows(limit=20)
        error_bank = self._question_rows(statuses=("incorrect", "partial"), limit=100)
        pending_questions = self._question_rows(statuses=("needs_review",), limit=100)
        return {
            "summary": dict(summary),
            "subjects": subjects,
            "knowledge_points": knowledge_points,
            "weak_knowledge_points": [
                point for point in knowledge_points
                if point["review_priority"] > 0 or point["incorrect_count"] > 0
            ][:20],
            "error_bank": error_bank,
            "error_bank_truncated": self._has_more_questions(("incorrect", "partial"), 100),
            "pending_questions": pending_questions,
            "pending_questions_truncated": self._has_more_questions(("needs_review",), 100),
            "recent_documents": recent_documents,
            "recent_documents_truncated": self._has_more_documents(20),
            "period": {"label": "累计至今", "sample_size": int(summary["question_count"])},
            "monthly_period": {"label": now.strftime("%Y年%m月"), "sample_size": int(summary["monthly_document_count"])},
        }

    def export_subject_snapshot(self, subject: str) -> dict[str, Any]:
        """Return the complete, uncapped archive needed for one subject's Markdown views."""
        self._validate_subject(subject)
        snapshot = self.dashboard_snapshot()
        return {
            "subject": snapshot["subjects"][subject],
            "period": snapshot["period"],
            "monthly_period": {
                "label": snapshot["monthly_period"]["label"],
                "sample_size": snapshot["subjects"][subject]["monthly_document_count"],
            },
            "documents": self._document_rows(subject=subject),
            "knowledge_points": self._subject_export_knowledge_points(subject),
            "error_bank": self._question_rows(subject=subject, statuses=("incorrect", "partial")),
            "pending_questions": self._question_rows(subject=subject, statuses=("needs_review",)),
        }

    def knowledge_point_evidence(self, subject: str, knowledge_point: str) -> list[dict[str, Any]]:
        """Return complete page/question evidence for one generated knowledge-point note."""
        self._validate_subject(subject)
        return [
            dict(row)
            for row in self.connection.execute(
                """
                SELECT documents.document_id, questions.question_id, questions.page,
                       questions.prompt_summary, questions.status, questions.decision_source,
                       questions.reason, pages.path AS source_path
                FROM question_knowledge_points AS links
                JOIN effective_questions AS questions ON questions.document_id = links.document_id
                    AND questions.question_id = links.question_id
                JOIN documents ON documents.document_id = questions.document_id
                LEFT JOIN pages ON pages.document_id = questions.document_id
                    AND pages.page_number = questions.page
                WHERE documents.subject = ? AND links.knowledge_point = ?
                ORDER BY documents.created_at, documents.document_id, questions.page, questions.question_id
                """,
                (subject, knowledge_point),
            )
        ]

    def _subject_export_knowledge_points(self, subject: str) -> list[dict[str, Any]]:
        """Include linked points even while a recoverable job has not built mastery stats yet."""
        rows = {item["knowledge_point"]: item for item in self._knowledge_point_rows(subject)}
        for row in self.connection.execute(
            """
            SELECT DISTINCT links.knowledge_point
            FROM question_knowledge_points AS links
            JOIN documents ON documents.document_id = links.document_id
            WHERE documents.subject = ?
            ORDER BY links.knowledge_point
            """,
            (subject,),
        ):
            point = row["knowledge_point"]
            rows.setdefault(
                point,
                {
                    "subject": subject,
                    "knowledge_point": point,
                    "exposure_count": 0,
                    "correct_count": 0,
                    "incorrect_count": 0,
                    "partial_count": 0,
                    "needs_review": 0,
                    "review_priority": 0,
                    "trend": "no_data",
                    "last_seen_at": None,
                    "mastery_rate": None,
                },
            )
        return sorted(
            rows.values(),
            key=lambda item: (-item["review_priority"], item["knowledge_point"]),
        )

    def _knowledge_point_rows(self, subject: str | None = None) -> list[dict[str, Any]]:
        clauses = ["exposure_count > 0 OR needs_review > 0"]
        parameters: list[Any] = []
        if subject is not None:
            self._validate_subject(subject)
            clauses.append("subject = ?")
            parameters.append(subject)
        rows: list[dict[str, Any]] = []
        for row in self.connection.execute(
            f"""
            SELECT subject, knowledge_point, exposure_count, correct_count, incorrect_count,
                   partial_count, needs_review, review_priority, trend, last_seen_at
            FROM knowledge_stats
            WHERE {' AND '.join(f'({clause})' for clause in clauses)}
            ORDER BY review_priority DESC, last_seen_at DESC, subject, knowledge_point
            """,
            parameters,
        ):
            item = dict(row)
            denominator = item["exposure_count"]
            item["mastery_rate"] = (
                (item["correct_count"] + item["partial_count"] * 0.5) / denominator
                if denominator else None
            )
            rows.append(item)
        return rows

    def _document_rows(self, subject: str | None = None, limit: int | None = None) -> list[dict[str, Any]]:
        clauses: list[str] = []
        parameters: list[Any] = []
        if subject is not None:
            self._validate_subject(subject)
            clauses.append("subject = ?")
            parameters.append(subject)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        limit_sql = f" LIMIT {limit}" if limit is not None else ""
        return [
            dict(row)
            for row in self.connection.execute(
                f"""
                SELECT document_id, subject, document_type, summary, created_at, updated_at
                FROM documents {where}
                ORDER BY updated_at DESC, document_id DESC{limit_sql}
                """,
                parameters,
            )
        ]

    def _question_rows(
        self, *, subject: str | None = None, statuses: tuple[str, ...], limit: int | None = None
    ) -> list[dict[str, Any]]:
        clauses = [f"questions.status IN ({', '.join('?' for _ in statuses)})"]
        parameters: list[Any] = list(statuses)
        if subject is not None:
            self._validate_subject(subject)
            clauses.append("documents.subject = ?")
            parameters.append(subject)
        limit_sql = f" LIMIT {limit}" if limit is not None else ""
        return [
            dict(row)
            for row in self.connection.execute(
                f"""
                SELECT documents.document_id, documents.subject, questions.question_id,
                       questions.page, questions.prompt_summary, questions.status,
                       questions.decision_source, questions.reason, questions.confidence,
                       pages.path AS source_path
                FROM effective_questions AS questions
                JOIN documents ON documents.document_id = questions.document_id
                LEFT JOIN pages ON pages.document_id = questions.document_id
                    AND pages.page_number = questions.page
                WHERE {' AND '.join(clauses)}
                ORDER BY documents.updated_at DESC, documents.document_id DESC,
                         questions.page, questions.question_id{limit_sql}
                """,
                parameters,
            )
        ]

    def _has_more_documents(self, limit: int) -> bool:
        row = self.connection.execute("SELECT COUNT(*) AS count FROM documents").fetchone()
        return int(row["count"]) > limit

    def _has_more_questions(self, statuses: tuple[str, ...], limit: int) -> bool:
        placeholders = ", ".join("?" for _ in statuses)
        row = self.connection.execute(
            f"SELECT COUNT(*) AS count FROM effective_questions WHERE status IN ({placeholders})", statuses
        ).fetchone()
        return int(row["count"]) > limit

    def _recent_trend(self, subject: str, now: datetime) -> dict[str, Any]:
        """Compare two 45-day halves only when both contain confirmed evidence."""
        cutoff = now - timedelta(days=90)
        midpoint = now - timedelta(days=45)
        rows = list(
            self.connection.execute(
                """
                SELECT documents.document_id, documents.created_at, questions.status
                FROM effective_questions AS questions JOIN documents ON documents.document_id = questions.document_id
                WHERE documents.subject = ?
                    AND questions.status != 'needs_review'
                    AND (questions.confidence >= 0.80 OR questions.decision_source = 'parent')
                    AND documents.created_at >= ?
                ORDER BY documents.created_at, documents.document_id, questions.page, questions.question_id
                """,
                (subject, cutoff.strftime("%Y-%m-%d %H:%M:%S")),
            )
        )
        document_count = len({row["document_id"] for row in rows})
        sample_size = len(rows)
        result: dict[str, Any] = {
            "period_label": "最近90天",
            "document_sample_size": document_count,
            "question_sample_size": sample_size,
            "status": "insufficient_data",
            "mastery_rate": None,
        }
        if not rows:
            return result
        scores = [1.0 if row["status"] == "correct" else 0.5 if row["status"] == "partial" else 0.0 for row in rows]
        result["mastery_rate"] = sum(scores) / sample_size
        old_scores = [
            1.0 if row["status"] == "correct" else 0.5 if row["status"] == "partial" else 0.0
            for row in rows
            if row["created_at"] < midpoint.strftime("%Y-%m-%d %H:%M:%S")
        ]
        new_scores = [
            1.0 if row["status"] == "correct" else 0.5 if row["status"] == "partial" else 0.0
            for row in rows
            if row["created_at"] >= midpoint.strftime("%Y-%m-%d %H:%M:%S")
        ]
        if document_count < 2 or not old_scores or not new_scores:
            return result
        difference = sum(new_scores) / len(new_scores) - sum(old_scores) / len(old_scores)
        result["status"] = "improving" if difference > 0.05 else "declining" if difference < -0.05 else "steady"
        return result

    def _validate_subject(self, subject: str) -> None:
        if subject not in self.config.subjects:
            raise ValueError("非法科目")

    @staticmethod
    def _page_row(page: CapturedPage | tuple[int, str, str]) -> tuple[int, str, str]:
        if isinstance(page, CapturedPage):
            return page.page_number, str(page.path), page.sha256
        page_number, path, sha256 = page
        return int(page_number), str(path), str(sha256)

    @staticmethod
    def _json(value: Any) -> str:
        if is_dataclass(value):
            value = asdict(value)
        return json.dumps(value, ensure_ascii=False)

    @staticmethod
    def _from_json(value: str) -> list[Any]:
        return json.loads(value)
