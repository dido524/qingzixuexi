"""Shared, dependency-light models for homework capture and analysis."""

from dataclasses import dataclass
from enum import Enum
import json
from importlib.resources import files
from pathlib import Path
from typing import Any, Mapping

from jsonschema import validate


class Subject(str, Enum):
    CHINESE = "语文"
    MATH = "数学"
    ENGLISH = "英语"


class GradingMode(str, Enum):
    TEACHER_MARKED = "teacher_marked"
    AUTO_GRADE = "auto_grade"
    MIXED = "mixed"


class QuestionStatus(str, Enum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PARTIAL = "partial"
    NEEDS_REVIEW = "needs_review"


class QuestionType(str, Enum):
    OBJECTIVE = "objective"
    FILL_BLANK = "fill_blank"
    CALCULATION = "calculation"
    APPLICATION = "application"
    READING_OPEN = "reading_open"
    COMPOSITION = "composition"
    TRANSLATION = "translation"
    SHORT_ANSWER = "short_answer"
    OTHER = "other"


@dataclass(frozen=True)
class CapturedPage:
    page_number: int
    path: Path
    sha256: str


@dataclass(frozen=True)
class CapturedDocument:
    document_id: str
    pages: tuple[CapturedPage, ...]
    document_type: str = "作业"

    @property
    def session_dir(self) -> Path:
        if not self.pages:
            raise ValueError("资料没有已拍摄页面")
        parents = {page.path.resolve().parent for page in self.pages}
        if len(parents) != 1:
            raise ValueError("已拍摄页面必须位于同一会话目录")
        return parents.pop()


@dataclass(frozen=True)
class QuestionAnalysis:
    question_id: str
    question_type: QuestionType
    page: int
    prompt_summary: str
    student_answer: str
    reference_answer: str
    status: QuestionStatus
    decision_source: str
    knowledge_points: tuple[str, ...]
    error_categories: tuple[str, ...]
    confidence: float
    reason: str

    @property
    def counts_toward_mastery(self) -> bool:
        return self.status != QuestionStatus.NEEDS_REVIEW and self.confidence >= 0.80


@dataclass(frozen=True)
class PageSubjectAssignment:
    page: int
    suggested_subject: Subject
    confidence: float
    needs_confirmation: bool
    reason: str


@dataclass(frozen=True)
class AnalysisResult:
    document_id: str
    subject: Subject
    subject_confidence: float
    document_type: str
    grading_mode: GradingMode
    teacher_mark_evidence: tuple[str, ...]
    questions: tuple[QuestionAnalysis, ...]
    summary: str
    page_subjects: tuple[PageSubjectAssignment, ...] = ()


def validate_analysis_payload(payload: Mapping[str, Any], *, allow_legacy: bool = False) -> None:
    """Validate a Codex analysis response against the packaged strict schema."""
    schema_text = (
        files("qingzi_learning.schema") / "analysis-result.schema.json"
    ).read_text(encoding="utf-8")
    schema = json.loads(schema_text)
    if allow_legacy and "page_subjects" not in payload:
        schema["required"].remove("page_subjects")
    validate(instance=dict(payload), schema=schema)
