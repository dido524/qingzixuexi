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


@pytest.fixture
def updater(repo: KnowledgeRepository) -> KnowledgeUpdater:
    return KnowledgeUpdater(repo, now=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))


def test_split_updater_delegates_one_batch_without_partial_writes(updater, monkeypatch):
    """A per-child loop or swallowed transaction failure would violate the batch boundary."""
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    calls = []
    parent, children = object(), (object(), object())

    def fail_batch(received_parent, received_children, *, now):
        calls.append((received_parent, received_children, now))
        raise RuntimeError("batch rollback")

    monkeypatch.setattr(updater.repo, "apply_split_batch", fail_batch, raising=False)
    with pytest.raises(RuntimeError, match="batch rollback"):
        updater.apply_split_batch(parent, children, now=now)
    assert calls == [(parent, children, now)]


def question(
    question_id: str,
    status: QuestionStatus,
    source: str,
    point: str,
    *,
    confidence: float = 0.96,
    categories: tuple[str, ...] = (),
) -> QuestionAnalysis:
    return QuestionAnalysis(
        question_id=question_id,
        question_type=QuestionType.OBJECTIVE,
        page=1,
        prompt_summary=point,
        student_answer="answer",
        reference_answer="reference",
        status=status,
        decision_source=source,
        knowledge_points=(point,),
        error_categories=categories,
        confidence=confidence,
        reason="fixture",
    )


def analysis(
    document_id: str,
    subject: Subject,
    questions: tuple[QuestionAnalysis, ...],
) -> AnalysisResult:
    return AnalysisResult(
        document_id=document_id,
        subject=subject,
        subject_confidence=0.96,
        document_type="试卷",
        grading_mode=GradingMode.MIXED,
        teacher_mark_evidence=(),
        questions=questions,
        summary="fixture",
    )


def apply(updater: KnowledgeUpdater, result: AnalysisResult) -> None:
    """Stage captured-page evidence because analysis facts must remain traceable."""
    if updater.repo.get_document(result.document_id) is None:
        updater.repo.create_document(
            document_id=result.document_id,
            subject=result.subject.value,
            document_type=result.document_type,
            pages=tuple(
                (page, f"C:/spool/{result.document_id}/page_{page:03d}.jpg", f"{page:064x}")
                for page in sorted({question.page for question in result.questions})
            ),
        )
    updater.apply(result)


def test_needs_review_is_linked_but_not_counted(updater: KnowledgeUpdater) -> None:
    """Counting unresolved work as an attempt would make mastery look more certain than it is."""
    apply(
        updater,
        analysis(
            "doc-review",
            Subject.MATH,
            (question("1", QuestionStatus.NEEDS_REVIEW, "model", "分数除法"),),
        )
    )

    stats = updater.repo.get_knowledge_stats("数学", "分数除法")

    assert stats.exposure_count == 0
    assert stats.needs_review == 1
    assert updater.repo.count_review_items() == 1


def test_teacher_and_model_decisions_have_separate_counters(updater: KnowledgeUpdater) -> None:
    """Collapsing sources would hide whether a weakness came from the teacher or model."""
    apply(
        updater,
        analysis(
            "doc-mixed",
            Subject.ENGLISH,
            (
                question("1", QuestionStatus.INCORRECT, "teacher", "一般现在时", categories=("时态",)),
                question("2", QuestionStatus.INCORRECT, "model", "一般现在时", categories=("第三人称单数",)),
            ),
        )
    )

    stats = updater.repo.get_knowledge_stats("英语", "一般现在时")

    assert stats.exposure_count == 2
    assert stats.incorrect_count == 2
    assert stats.teacher_incorrect_count == 1
    assert stats.model_incorrect_count == 1
    assert stats.common_error_categories == ("时态", "第三人称单数")


def test_reapplying_document_does_not_double_mastery_counts(updater: KnowledgeUpdater) -> None:
    """A retry that increments instead of recomputing would inflate every count."""
    result = analysis(
        "doc-idempotent",
        Subject.ENGLISH,
        (question("1", QuestionStatus.CORRECT, "teacher", "一般现在时"),),
    )
    apply(updater, result)
    first = updater.repo.get_knowledge_stats("英语", "一般现在时")

    apply(updater, result)
    second = updater.repo.get_knowledge_stats("英语", "一般现在时")

    assert second == first


def test_recompute_reports_trend_and_priority_from_persisted_history(
    updater: KnowledgeUpdater,
) -> None:
    """A wrong chronology would fail to surface a recent, repeated, unretested error."""
    older = analysis(
        "doc-older",
        Subject.CHINESE,
        (question("1", QuestionStatus.INCORRECT, "teacher", "概括主要内容", categories=("要点遗漏",)),),
    )
    newer = analysis(
        "doc-newer",
        Subject.CHINESE,
        (question("1", QuestionStatus.INCORRECT, "teacher", "概括主要内容", categories=("要点遗漏",)),),
    )
    apply(updater, older)
    updater.repo.connection.execute(
        "UPDATE documents SET created_at = ? WHERE document_id = ?",
        ("2026-09-01 00:00:00", older.document_id),
    )
    apply(updater, newer)
    updater.repo.connection.execute(
        "UPDATE documents SET created_at = ? WHERE document_id = ?",
        ("2026-09-14 00:00:00", newer.document_id),
    )

    updater.recompute("语文", ("概括主要内容",))

    stats = updater.repo.get_knowledge_stats("语文", "概括主要内容")
    assert stats.trend == "declining"
    assert stats.review_priority == 85


def test_consecutive_correct_retests_lower_priority(updater: KnowledgeUpdater) -> None:
    """Ignoring successful retests would keep an already-mastered point unnecessarily urgent."""
    apply(
        updater,
        analysis(
            "doc-error",
            Subject.MATH,
            (question("1", QuestionStatus.INCORRECT, "teacher", "小数乘法"),),
        )
    )
    apply(
        updater,
        analysis(
            "doc-correct-1",
            Subject.MATH,
            (question("1", QuestionStatus.CORRECT, "teacher", "小数乘法"),),
        )
    )
    apply(
        updater,
        analysis(
            "doc-correct-2",
            Subject.MATH,
            (question("1", QuestionStatus.CORRECT, "teacher", "小数乘法"),),
        )
    )

    stats = updater.repo.get_knowledge_stats("数学", "小数乘法")
    assert stats.trend == "improving"
    assert stats.review_priority == 10


def test_question_renumbering_cannot_change_a_single_attempts_trend(
    updater: KnowledgeUpdater,
) -> None:
    """Question ordering must not turn one worksheet into two chronological retests."""
    apply(
        updater,
        analysis(
            "doc-order-a",
            Subject.MATH,
            (
                question("1", QuestionStatus.INCORRECT, "teacher", "顺序不变A"),
                question("2", QuestionStatus.CORRECT, "teacher", "顺序不变A"),
            ),
        ),
    )
    apply(
        updater,
        analysis(
            "doc-order-b",
            Subject.MATH,
            (
                question("1", QuestionStatus.CORRECT, "teacher", "顺序不变B"),
                question("2", QuestionStatus.INCORRECT, "teacher", "顺序不变B"),
            ),
        ),
    )

    first = updater.repo.get_knowledge_stats("数学", "顺序不变A")
    second = updater.repo.get_knowledge_stats("数学", "顺序不变B")

    assert (first.trend, first.review_priority) == ("declining", 55)
    assert (second.trend, second.review_priority) == ("declining", 55)


def test_multiple_correct_questions_on_one_worksheet_are_one_retest(
    updater: KnowledgeUpdater,
) -> None:
    """Two correct answers on an original worksheet count as one successful later attempt."""
    apply(
        updater,
        analysis(
            "doc-prior-error",
            Subject.MATH,
            (question("1", QuestionStatus.INCORRECT, "teacher", "分数应用"),),
        ),
    )
    apply(
        updater,
        analysis(
            "doc-original-worksheet",
            Subject.MATH,
            (
                question("1", QuestionStatus.CORRECT, "teacher", "分数应用"),
                question("2", QuestionStatus.CORRECT, "teacher", "分数应用"),
            ),
        ),
    )

    stats = updater.repo.get_knowledge_stats("数学", "分数应用")

    assert stats.trend == "improving"
    assert stats.review_priority == 15


def test_restaging_recomputes_removed_knowledge_point(updater: KnowledgeUpdater) -> None:
    """Cascade deletion during a retake must not leave the old point's weakness behind."""
    original = analysis(
        "doc-restage-point",
        Subject.MATH,
        (question("1", QuestionStatus.INCORRECT, "teacher", "旧知识点"),),
    )
    apply(updater, original)
    updater.repo.create_document(
        document_id=original.document_id,
        subject="数学",
        document_type=original.document_type,
        pages=((1, "C:/spool/restaged/page_001.jpg", "a" * 64),),
    )
    updater.apply(
        analysis(
            original.document_id,
            Subject.MATH,
            (question("1", QuestionStatus.CORRECT, "teacher", "新知识点"),),
        )
    )

    assert updater.repo.get_knowledge_stats("数学", "旧知识点").exposure_count == 0
    assert updater.repo.get_knowledge_stats("数学", "新知识点").exposure_count == 1


def test_restaging_changed_subject_recomputes_old_subject(updater: KnowledgeUpdater) -> None:
    """Moving a recaptured document to another subject must clear its old subject aggregate."""
    original = analysis(
        "doc-restage-subject",
        Subject.MATH,
        (question("1", QuestionStatus.INCORRECT, "teacher", "旧科目知识点"),),
    )
    apply(updater, original)
    updater.repo.create_document(
        document_id=original.document_id,
        subject="英语",
        document_type=original.document_type,
        pages=((1, "C:/spool/restaged-subject/page_001.jpg", "b" * 64),),
    )
    updater.apply(
        analysis(
            original.document_id,
            Subject.ENGLISH,
            (question("1", QuestionStatus.CORRECT, "teacher", "新科目知识点"),),
        )
    )

    assert updater.repo.get_knowledge_stats("数学", "旧科目知识点").exposure_count == 0
    assert updater.repo.get_knowledge_stats("英语", "新科目知识点").exposure_count == 1
