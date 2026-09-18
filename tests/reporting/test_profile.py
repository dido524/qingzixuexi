from datetime import datetime, timezone
from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult,
    GradingMode,
    QuestionAnalysis,
    QuestionStatus,
    QuestionType,
    Subject,
)
from qingzi_learning.knowledge.updater import KnowledgeUpdater
from qingzi_learning.storage.repository import KnowledgeRepository


UTC = timezone.utc


@pytest.fixture
def repo(tmp_path: Path) -> KnowledgeRepository:
    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "app-data",
    )
    repository = KnowledgeRepository(config)
    yield repository
    repository.close()


@pytest.mark.parametrize(
    ("question_count", "study_day_count", "expected"),
    [
        (0, 0, "no_data"),
        (9, 3, "initial"),
        (10, 1, "initial"),
        (10, 2, "forming"),
        (29, 8, "forming"),
        (30, 2, "forming"),
        (30, 3, "stable"),
    ],
)
def test_evidence_level_is_conservative(
    question_count: int, study_day_count: int, expected: str
) -> None:
    from qingzi_learning.reporting.profile import evidence_level

    assert evidence_level(question_count, study_day_count) == expected


def test_profile_excludes_pending_and_low_confidence_model_judgments(
    repo: KnowledgeRepository,
) -> None:
    from qingzi_learning.reporting.profile import LearningProfileBuilder

    _save_question(repo, "valid", "2026-09-16 10:00:00", QuestionStatus.INCORRECT, 0.96)
    _save_question(repo, "pending", "2026-09-17 10:00:00", QuestionStatus.NEEDS_REVIEW, 0.99)
    _save_question(repo, "uncertain", "2026-09-18 10:00:00", QuestionStatus.INCORRECT, 0.79)

    profile = LearningProfileBuilder(repo).build(
        None, cutoff_at=datetime(2026, 9, 18, 23, 0, tzinfo=UTC)
    )

    math = profile["subjects"]["数学"]
    point = math["knowledge_points"]["分数应用"]
    assert math["question_count"] == 1
    assert math["study_day_count"] == 1
    assert math["evidence_level"] == "initial"
    assert point["exposure_count"] == 1
    assert point["incorrect_count"] == 1
    assert point["error_categories"] == [{"name": "审题", "count": 1}]
    assert profile["summary"]["pending_count"] == 1
    assert profile["summary"]["excluded_low_confidence_count"] == 1


def test_profile_compares_current_facts_with_previous_snapshot(
    repo: KnowledgeRepository,
) -> None:
    from qingzi_learning.reporting.profile import LearningProfileBuilder

    _save_question(repo, "first", "2026-09-16 10:00:00", QuestionStatus.INCORRECT, 0.96)
    builder = LearningProfileBuilder(repo)
    first = builder.build(None, cutoff_at=datetime(2026, 9, 16, 23, 0, tzinfo=UTC))

    _save_question(repo, "retest", "2026-09-18 10:00:00", QuestionStatus.CORRECT, 0.96)
    second = builder.build(first, cutoff_at=datetime(2026, 9, 18, 23, 0, tzinfo=UTC))

    point = second["subjects"]["数学"]["knowledge_points"]["分数应用"]
    assert point["correct_count"] == 1
    assert point["incorrect_count"] == 1
    assert point["delta"] == {
        "exposure_count": 1,
        "correct_count": 1,
        "incorrect_count": 0,
        "partial_count": 0,
    }
    assert point["change"] == "improved"
    assert second["delta"]["question_count"] == 1
    assert second["delta"]["new_document_count"] == 1


def test_profile_keeps_all_subjects_and_marks_new_points(repo: KnowledgeRepository) -> None:
    from qingzi_learning.reporting.profile import LearningProfileBuilder

    _save_question(repo, "math", "2026-09-18 10:00:00", QuestionStatus.PARTIAL, 0.96)

    profile = LearningProfileBuilder(repo).build(
        None, cutoff_at=datetime(2026, 9, 18, 23, 0, tzinfo=UTC)
    )

    assert tuple(profile["subjects"]) == ("语文", "数学", "英语")
    assert profile["subjects"]["语文"]["evidence_level"] == "no_data"
    assert profile["subjects"]["英语"]["knowledge_points"] == {}
    assert profile["subjects"]["数学"]["knowledge_points"]["分数应用"]["change"] == "new"


def _save_question(
    repo: KnowledgeRepository,
    document_id: str,
    created_at: str,
    status: QuestionStatus,
    confidence: float,
) -> None:
    analysis = AnalysisResult(
        document_id=document_id,
        subject=Subject.MATH,
        subject_confidence=0.98,
        document_type="练习",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=(
            QuestionAnalysis(
                question_id="1",
                question_type=QuestionType.APPLICATION,
                page=1,
                prompt_summary="分数应用题",
                student_answer="1/3",
                reference_answer="1/2",
                status=status,
                decision_source="model",
                knowledge_points=("分数应用",),
                error_categories=("审题",),
                confidence=confidence,
                reason="测试证据",
            ),
        ),
        summary="测试资料",
    )
    repo.create_document(
        document_id=document_id,
        subject="数学",
        document_type="练习",
        pages=((1, str(repo.config.knowledge_root / f"{document_id}.jpg"), "a" * 64),),
    )
    repo.connection.execute(
        "UPDATE documents SET created_at=?, updated_at=? WHERE document_id=?",
        (created_at, created_at, document_id),
    )
    repo.connection.commit()
    KnowledgeUpdater(
        repo, now=lambda: datetime.fromisoformat(created_at).replace(tzinfo=UTC)
    ).apply(analysis)
