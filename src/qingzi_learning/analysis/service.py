"""Validate model judgments and leave recoverable, subject-neutral job state."""

from __future__ import annotations

from dataclasses import dataclass, replace
import json
from pathlib import Path
from typing import Literal, Protocol

from jsonschema import ValidationError

from qingzi_learning.analysis.codex_cli import AnalysisError, validate_result
from qingzi_learning.domain import AnalysisResult, CapturedDocument, QuestionStatus, QuestionType
from qingzi_learning.export.safe_write import atomic_write


class Analyzer(Protocol):
    def analyze(self, document: CapturedDocument) -> AnalysisResult: ...


@dataclass(frozen=True)
class AnalysisOutcome:
    state: Literal["pending", "analyzed", "needs_subject_confirmation"]
    analysis: AnalysisResult | None = None
    error_code: str | None = None


class AnalysisService:
    def __init__(self, analyzer: Analyzer) -> None:
        self.analyzer = analyzer

    def analyze_or_queue(self, document: CapturedDocument) -> AnalysisOutcome:
        """Queue before launch, then validate/protect results without archiving them."""
        state_path = document.session_dir / "analysis_state.json"
        _write_state(state_path, document.document_id, AnalysisOutcome("pending"))
        try:
            result = self.analyzer.analyze(document)
            validate_result(result, document)
        except AnalysisError as exc:
            # Only application-owned codes enter durable state, even for injected analyzers.
            allowed = {"cli_unavailable", "timeout", "cli_failed", "invalid_response",
                       "invalid_document", "unsafe_cli_path",
                       "deepseek_api_key_missing", "deepseek_auth_failed",
                       "deepseek_rate_limited", "deepseek_unavailable",
                       "invalid_deepseek_response"}
            outcome = AnalysisOutcome("pending", error_code=exc.code if exc.code in allowed else "analysis_failed")
        except (ValueError, TypeError, ValidationError):
            outcome = AnalysisOutcome("pending", error_code="invalid_response")
        else:
            subjective = {QuestionType.READING_OPEN, QuestionType.COMPOSITION, QuestionType.SHORT_ANSWER}
            questions = tuple(
                replace(question, status=QuestionStatus.NEEDS_REVIEW)
                if question.question_type in subjective
                and question.decision_source == "model" and question.confidence < 0.90
                else question
                for question in result.questions
            )
            protected = replace(result, questions=questions)
            state = "needs_subject_confirmation" if protected.subject_confidence < 0.85 else "analyzed"
            outcome = AnalysisOutcome(state, analysis=protected)
        _write_state(state_path, document.document_id, outcome)
        return outcome


def _write_state(path: Path, document_id: str, outcome: AnalysisOutcome) -> None:
    """Atomic sidecar excludes raw model output, process logs, and subject guesses."""
    payload = {"version": 1, "document_id": document_id,
               "state": outcome.state, "error_code": outcome.error_code}
    atomic_write(path, json.dumps(payload, ensure_ascii=False, allow_nan=False), path.parent)
