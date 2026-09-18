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


REQUEST = {
    "subject": "数学",
    "scope": "分数",
    "duration_minutes": 40,
    "difficulty": "适中",
    "question_count": 1,
    "include_composition": False,
    "include_reading": False,
}
BLUEPRINT = {
    "allocation": {"primary": 1, "related": 0, "stable": 0},
    "targets": [{"knowledge_point": "分数应用", "category": "primary"}],
}
GENERATION = {"title": "分数专项练习", "total_points": 100}
VERIFICATION = {"status": "passed", "issues": []}
QUESTION = {
    "question_id": "Q01",
    "question_type": "application",
    "points": 100,
    "knowledge_points": ["分数应用"],
    "blueprint_category": "primary",
    "prompt": "一桶水用去一部分后还剩多少？",
    "answer": "二分之一桶",
    "explanation": "用整体减去已用部分。",
    "rubric": "列式正确并写出结果。",
}


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


def test_exam_cannot_be_approved_before_validated_questions(repo: KnowledgeRepository) -> None:
    run = repo.create_exam_run("QZ-MATH-1", "数学", REQUEST, BLUEPRINT)

    assert run.status == "draft" and run.revision == 0
    with pytest.raises(ValueError, match="尚未通过校验"):
        repo.approve_exam(run.exam_id, {"student": "student.html"}, expected_revision=0)


def test_validated_exam_uses_optimistic_parent_approval(repo: KnowledgeRepository) -> None:
    run = repo.create_exam_run("QZ-MATH-2", "数学", REQUEST, BLUEPRINT)
    ready = repo.save_exam_generation(
        run.exam_id, GENERATION, VERIFICATION, (QUESTION,)
    )

    assert ready.status == "needs_parent_approval"
    assert ready.revision == 1
    questions = repo.exam_questions(run.exam_id)
    assert len(questions) == 1
    assert questions[0].question_id == "Q01"
    assert questions[0].knowledge_points == ("分数应用",)
    with pytest.raises(ValueError, match="已变化"):
        repo.approve_exam(run.exam_id, {"student": "student.html"}, expected_revision=0)

    approved = repo.approve_exam(
        run.exam_id,
        {"student": "模拟试卷/QZ-MATH-2/学生试卷.html"},
        expected_revision=ready.revision,
    )
    assert approved.status == "approved"
    assert approved.revision == 2
    assert approved.approved_at is not None
    with pytest.raises(ValueError, match="已批准"):
        repo.save_exam_generation(run.exam_id, GENERATION, VERIFICATION, (QUESTION,))


def test_exam_attempt_is_idempotent(repo: KnowledgeRepository) -> None:
    run = repo.create_exam_run("QZ-MATH-3", "数学", REQUEST, BLUEPRINT)
    ready = repo.save_exam_generation(run.exam_id, GENERATION, VERIFICATION, (QUESTION,))
    repo.approve_exam(
        run.exam_id, {"student": "student.html"}, expected_revision=ready.revision
    )
    _save_answer_document(repo, "answer-doc")

    first = repo.record_exam_attempt(
        run.exam_id, "Q01", "answer-doc", "1", "incorrect"
    )
    second = repo.record_exam_attempt(
        run.exam_id, "Q01", "answer-doc", "1", "incorrect"
    )

    assert first is True and second is False
    attempts = repo.exam_attempts(run.exam_id)
    assert len(attempts) == 1
    assert attempts[0].status == "incorrect"


def test_failed_and_generating_exams_do_not_appear_as_approved(repo: KnowledgeRepository) -> None:
    first = repo.create_exam_run("QZ-MATH-4", "数学", REQUEST, BLUEPRINT)
    repo.fail_exam(first.exam_id, "generation_failed")
    repo.create_exam_run("QZ-MATH-5", "数学", REQUEST, BLUEPRINT)

    assert [run.status for run in repo.list_exam_runs()] == ["draft", "failed"]
    assert repo.list_exam_runs(status="approved") == ()


def _save_answer_document(repo: KnowledgeRepository, document_id: str) -> None:
    analysis = AnalysisResult(
        document_id=document_id,
        subject=Subject.MATH,
        subject_confidence=0.99,
        document_type="模拟卷复测",
        grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=(
            QuestionAnalysis(
                question_id="1",
                question_type=QuestionType.APPLICATION,
                page=1,
                prompt_summary="分数应用复测",
                student_answer="三分之一",
                reference_answer="二分之一",
                status=QuestionStatus.INCORRECT,
                decision_source="model",
                knowledge_points=("分数应用",),
                error_categories=("审题",),
                confidence=0.99,
                reason="答案不完整",
            ),
        ),
        summary="复测",
    )
    repo.create_document(
        document_id, "数学", "模拟卷复测",
        ((1, str(repo.config.knowledge_root / "answer.jpg"), "a" * 64),),
    )
    KnowledgeUpdater(
        repo, now=lambda: datetime(2026, 9, 18, tzinfo=timezone.utc)
    ).apply(analysis)
