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
    ("total", "expected"),
    [
        (5, {"primary": 3, "related": 1, "stable": 1}),
        (10, {"primary": 5, "related": 3, "stable": 2}),
        (11, {"primary": 6, "related": 3, "stable": 2}),
    ],
)
def test_default_allocation_is_deterministic(total: int, expected: dict) -> None:
    from qingzi_learning.exams.blueprint import allocate_question_counts

    assert allocate_question_counts(
        total, {"primary": True, "related": True, "stable": True}
    ) == expected


def test_allocation_reflows_missing_categories_to_primary() -> None:
    from qingzi_learning.exams.blueprint import allocate_question_counts

    assert allocate_question_counts(
        10, {"primary": True, "related": False, "stable": False}
    ) == {"primary": 10, "related": 0, "stable": 0}


def test_blueprint_excludes_pending_low_confidence_and_other_subjects(
    repo: KnowledgeRepository,
) -> None:
    from qingzi_learning.exams.blueprint import BlueprintBuilder, ExamRequest

    _save(repo, "valid", Subject.MATH, "分数应用", QuestionStatus.INCORRECT, 0.96)
    _save(repo, "pending", Subject.MATH, "待确认知识点", QuestionStatus.NEEDS_REVIEW, 0.99)
    _save(repo, "uncertain", Subject.MATH, "低置信度知识点", QuestionStatus.INCORRECT, 0.79)
    _save(repo, "english", Subject.ENGLISH, "一般现在时", QuestionStatus.INCORRECT, 0.99)

    plan = BlueprintBuilder(repo).build(
        ExamRequest("数学", "分数", 40, "适中", 5, False, False)
    )

    assert plan["allocation"] == {"primary": 5, "related": 0, "stable": 0}
    assert len(plan["slots"]) == 5
    assert all(slot["subject"] == "数学" for slot in plan["slots"])
    assert {slot["knowledge_point"] for slot in plan["slots"]} == {"分数应用"}
    assert "证据不足" in plan["allocation_note"]


def test_blueprint_is_stable_and_uses_related_and_mastered_points(
    repo: KnowledgeRepository,
) -> None:
    from qingzi_learning.exams.blueprint import BlueprintBuilder, ExamRequest

    _save(
        repo, "weak", Subject.MATH, "分数应用", QuestionStatus.INCORRECT, 0.98,
        other_point="单位一判断",
    )
    _save(repo, "related", Subject.MATH, "单位一判断", QuestionStatus.PARTIAL, 0.98)
    _save(repo, "stable", Subject.MATH, "分数加法", QuestionStatus.CORRECT, 0.98)

    request = ExamRequest("数学", "", 40, "适中", 10, False, False)
    first = BlueprintBuilder(repo).build(request)
    second = BlueprintBuilder(repo).build(request)

    assert first == second
    assert len(first["slots"]) == 10
    assert sum(first["allocation"].values()) == 10
    assert first["allocation"]["primary"] >= 5
    assert {slot["category"] for slot in first["slots"]} >= {"primary", "stable"}


def test_blueprint_rejects_a_scope_without_confirmed_evidence(repo: KnowledgeRepository) -> None:
    from qingzi_learning.exams.blueprint import BlueprintBuilder, ExamRequest

    _save(repo, "valid", Subject.MATH, "分数应用", QuestionStatus.INCORRECT, 0.96)
    with pytest.raises(ValueError, match="考试范围"):
        BlueprintBuilder(repo).build(
            ExamRequest("数学", "小数除法", 40, "适中", 5, False, False)
        )


def _save(
    repo: KnowledgeRepository,
    document_id: str,
    subject: Subject,
    point: str,
    status: QuestionStatus,
    confidence: float,
    *,
    other_point: str | None = None,
) -> None:
    analysis = AnalysisResult(
        document_id=document_id,
        subject=subject,
        subject_confidence=0.99,
        document_type="练习",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=(
            QuestionAnalysis(
                question_id="1",
                question_type=QuestionType.APPLICATION,
                page=1,
                prompt_summary=f"{point}练习",
                student_answer="学生答案",
                reference_answer="参考答案",
                status=status,
                decision_source="model",
                knowledge_points=tuple(value for value in (point, other_point) if value),
                error_categories=("审题",),
                confidence=confidence,
                reason="测试证据",
            ),
        ),
        summary="测试资料",
    )
    repo.create_document(
        document_id,
        subject.value,
        "练习",
        ((1, str(repo.config.knowledge_root / f"{document_id}.jpg"), "a" * 64),),
    )
    KnowledgeUpdater(
        repo, now=lambda: datetime(2026, 9, 18, tzinfo=timezone.utc)
    ).apply(analysis)
