from datetime import datetime, timezone
from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult, GradingMode, QuestionAnalysis, QuestionStatus, QuestionType, Subject,
)
from qingzi_learning.knowledge.updater import KnowledgeUpdater
from qingzi_learning.storage.repository import KnowledgeRepository


@pytest.fixture
def repo(tmp_path: Path):
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    value = KnowledgeRepository(config)
    yield value
    value.close()


def _approve(repo: KnowledgeRepository, *, subject="数学") -> str:
    run = repo.create_exam_run("QZ-MATH-RETEST", subject, {}, {})
    run = repo.save_exam_generation(
        run.exam_id, {"title": "复测"}, {"approved": True},
        [{
            "question_id": "Q01", "question_type": "application", "points": 100,
            "knowledge_points": ["分数应用"], "blueprint_category": "primary",
            "prompt": "新题", "answer": "答案", "explanation": "解析", "rubric": "评分",
        }],
    )
    repo.approve_exam(run.exam_id, {"student": "模拟试卷/test.html"}, expected_revision=run.revision)
    return run.exam_id


def _analysis(exam_id, question_id, *, subject=Subject.MATH, status=QuestionStatus.CORRECT):
    return AnalysisResult(
        document_id="retest-doc", subject=subject, subject_confidence=.99,
        document_type="模拟卷回拍", grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(),
        questions=(QuestionAnalysis(
            question_id="1", question_type=QuestionType.APPLICATION, page=1,
            prompt_summary="分数应用复测", student_answer="答案", reference_answer="答案",
            status=status, decision_source="model", knowledge_points=("分数应用",),
            error_categories=(), confidence=.99, reason="复测题",
            source_exam_question_id=question_id,
        ),), summary="复测", source_exam_id=exam_id,
    )


def _store_document(repo, analysis):
    repo.create_document(analysis.document_id, analysis.subject.value, analysis.document_type,
                         ((1, "C:/spool/retest.jpg", "a" * 64),))


def test_approved_exam_retest_links_once_and_updates_mastery(repo) -> None:
    exam_id = _approve(repo)
    analysis = _analysis(exam_id, "Q01")
    _store_document(repo, analysis)
    updater = KnowledgeUpdater(repo, now=lambda: datetime(2026, 9, 18, tzinfo=timezone.utc))

    updater.apply(analysis)
    updater.apply(analysis)

    attempts = repo.exam_attempts(exam_id)
    assert len(attempts) == 1
    assert attempts[0].exam_question_id == "Q01"
    assert repo.get_knowledge_stats("数学", "分数应用").correct_count == 1


@pytest.mark.parametrize(
    "exam_id, question_id, subject",
    [
        ("QZ-FAKE", "Q01", Subject.MATH),
        ("QZ-MATH-RETEST", "Q99", Subject.MATH),
        ("QZ-MATH-RETEST", "Q01", Subject.ENGLISH),
        (None, "Q01", Subject.MATH),
    ],
)
def test_unknown_partial_or_cross_subject_ids_force_review_without_attempt(
    repo, exam_id, question_id, subject
) -> None:
    approved = _approve(repo)
    if exam_id == "QZ-MATH-RETEST":
        exam_id = approved
    analysis = _analysis(exam_id, question_id, subject=subject)
    _store_document(repo, analysis)

    KnowledgeUpdater(repo).apply(analysis)

    stored = repo.get_document(analysis.document_id)
    assert stored["questions"][0]["status"] == "needs_review"
    assert repo.exam_attempts(approved) == ()
    assert repo.get_knowledge_stats(subject.value, "分数应用").exposure_count == 0
