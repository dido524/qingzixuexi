from datetime import datetime, timezone
from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult, GradingMode, QuestionAnalysis, QuestionStatus, QuestionType, Subject,
)
from qingzi_learning.knowledge.updater import KnowledgeUpdater
from qingzi_learning.reporting.profile import LearningProfileBuilder
from qingzi_learning.reporting.render import ReportRenderer
from qingzi_learning.reporting.narrative import LocalNarrativeProvider
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import KnowledgeRepository


@pytest.fixture
def history_with_retest(tmp_path: Path):
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    repo = KnowledgeRepository(config)
    updater = KnowledgeUpdater(repo, now=lambda: datetime(2026, 9, 18, tzinfo=timezone.utc))
    original = _analysis("original-doc", QuestionStatus.INCORRECT)
    _store(repo, original)
    updater.apply(original)
    cutoff = datetime(2099, 1, 1, tzinfo=timezone.utc)
    first = LearningProfileBuilder(repo).build(None, cutoff_at=cutoff)

    run = repo.create_exam_run(
        "QZ-MATH-PROGRESS", "数学", {}, {
            "targets": [{
                "knowledge_point": "分数应用", "category": "primary",
                "representative_questions": [{
                    "document_id": "original-doc", "question_id": "1",
                    "prompt_summary": "分数应用原题", "status": "incorrect",
                }],
            }],
        },
    )
    run = repo.save_exam_generation(
        run.exam_id, {"title": "复测"}, {"approved": True},
        [{
            "question_id": "Q01", "question_type": "application", "points": 100,
            "knowledge_points": ["分数应用"], "blueprint_category": "primary",
            "prompt": "新的分数应用题", "answer": "答案", "explanation": "解析", "rubric": "评分",
        }],
    )
    repo.approve_exam(run.exam_id, {"student": "模拟试卷/test.html"}, expected_revision=run.revision)
    retest = _analysis(
        "retest-doc", QuestionStatus.CORRECT,
        source_exam_id=run.exam_id, source_exam_question_id="Q01",
    )
    _store(repo, retest)
    updater.apply(retest)
    second = LearningProfileBuilder(repo).build(first, cutoff_at=cutoff)
    yield repo, first, second
    repo.close()


def test_next_report_describes_retest_change_without_erasing_original_error(history_with_retest):
    _repo, _first, second = history_with_retest

    assert second["retests"][0]["previous_status"] == "incorrect"
    assert second["retests"][0]["current_status"] == "correct"
    assert second["retests"][0]["knowledge_points"] == ["分数应用"]
    assert second["subjects"]["数学"]["knowledge_points"]["分数应用"]["incorrect_count"] >= 1


def test_child_and_parent_reports_show_bounded_retest_progress(history_with_retest, tmp_path):
    repo, _first, second = history_with_retest
    paths = KnowledgePaths(repo.config)
    renderer = ReportRenderer(paths, paths.report_directory(2099, 1, "report-retest"))
    narrative = LocalNarrativeProvider().generate(second)

    child = renderer.render_child(second, narrative)
    parent = renderer.render_parent(second, narrative)

    assert "这次复测已答对，建议再确认一次" in child
    assert "QZ-MATH-PROGRESS" in parent
    assert "Q01" in parent and "retest-doc" in parent


def _analysis(document_id, status, *, source_exam_id=None, source_exam_question_id=None):
    return AnalysisResult(
        document_id=document_id, subject=Subject.MATH, subject_confidence=.99,
        document_type="练习", grading_mode=GradingMode.AUTO_GRADE,
        teacher_mark_evidence=(), questions=(QuestionAnalysis(
            question_id="1", question_type=QuestionType.APPLICATION, page=1,
            prompt_summary="分数应用题", student_answer="学生答案", reference_answer="参考答案",
            status=status, decision_source="model", knowledge_points=("分数应用",),
            error_categories=("审题",) if status == QuestionStatus.INCORRECT else (),
            confidence=.99, reason="测试", source_exam_question_id=source_exam_question_id,
        ),), summary="测试", source_exam_id=source_exam_id,
    )


def _store(repo, analysis):
    repo.create_document(analysis.document_id, "数学", analysis.document_type,
                         ((1, f"C:/spool/{analysis.document_id}.jpg", "a" * 64),))
