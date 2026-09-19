"""Recompute learning statistics from durable question facts."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timezone

from qingzi_learning.domain import AnalysisResult, CapturedDocument
from qingzi_learning.storage.repository import KnowledgeRepository, WorkflowJob


@dataclass(frozen=True)
class UpdateSummary:
    """The affected knowledge points after an idempotent document update."""

    document_id: str
    subject: str
    knowledge_points: tuple[str, ...]


class KnowledgeUpdater:
    """Replace a document's analysis and recompute its affected read model."""

    def __init__(
        self,
        repo: KnowledgeRepository,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repo = repo
        self._now = now or (lambda: datetime.now(timezone.utc))

    def apply(self, analysis: AnalysisResult) -> UpdateSummary:
        analysis = self.repo.sanitize_exam_links(analysis)
        points = tuple(
            dict.fromkeys(
                point
                for question in analysis.questions
                for point in question.knowledge_points
            )
        )
        self.repo.replace_analysis_and_recompute(analysis, now=self._now())
        self.repo.link_exam_attempts(analysis)
        return UpdateSummary(analysis.document_id, analysis.subject.value, points)

    def recompute(self, subject: str, knowledge_points: Iterable[str]) -> None:
        self.repo.recompute_knowledge_stats(subject, tuple(knowledge_points), now=self._now())

    def apply_split_batch(
        self, parent: WorkflowJob,
        children: tuple[tuple[WorkflowJob, CapturedDocument, AnalysisResult], ...],
        *, now: datetime,
    ) -> None:
        self.repo.apply_split_batch(parent, children, now=now)

    def confirm_review(self, document_id: str, question_id: str, final_status: str,
                       corrected_answer: str, note: str, *, expected_version: str | None = None) -> UpdateSummary:
        subject, points = self.repo.confirm_review_and_recompute(
            document_id, question_id, final_status, corrected_answer, note,
            expected_version=expected_version, now=self._now())
        return UpdateSummary(document_id, subject, points)

    def confirm_correct_batch(self, identities: tuple[tuple[str, str, str], ...]) -> tuple[str, ...]:
        return self.repo.confirm_correct_batch(identities, now=self._now())
