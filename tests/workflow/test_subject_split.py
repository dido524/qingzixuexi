from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from qingzi_learning.domain import (
    AnalysisResult,
    CapturedDocument,
    CapturedPage,
    GradingMode,
    PageSubjectAssignment,
    QuestionAnalysis,
    QuestionStatus,
    QuestionType,
    Subject,
)
from qingzi_learning.workflow.subject_split import (
    ResolvedPageSubject,
    SubjectPageGroup,
    SubjectSplitPlan,
    build_subject_split_plan,
    split_analysis,
)


@pytest.fixture
def document(tmp_path):
    pages = []
    for page_number in (1, 2, 3):
        image_path = tmp_path / f"page_{page_number:03d}.jpg"
        content = f"page {page_number}".encode()
        image_path.write_bytes(content)
        pages.append(CapturedPage(page_number, image_path, sha256(content).hexdigest()))
    return CapturedDocument("capture-001", tuple(pages), "试卷")


@pytest.fixture
def analysis(document):
    return AnalysisResult(
        document_id=document.document_id,
        subject=Subject.MATH,
        subject_confidence=0.99,
        document_type=document.document_type,
        grading_mode=GradingMode.MIXED,
        teacher_mark_evidence=("page_001: check",),
        questions=(
            _question("math-1", 1),
            _question("math-2", 2),
            _question("english-1", 3),
        ),
        summary="parent summary",
        page_subjects=(
            PageSubjectAssignment(1, Subject.MATH, 0.95, False, "计算题"),
            PageSubjectAssignment(2, Subject.MATH, 0.81, False, "应用题"),
            PageSubjectAssignment(3, Subject.ENGLISH, 0.90, False, "阅读题"),
        ),
    )


def _question(question_id, page):
    return QuestionAnalysis(
        question_id=question_id,
        question_type=QuestionType.OTHER,
        page=page,
        prompt_summary=question_id,
        student_answer="",
        reference_answer="",
        status=QuestionStatus.CORRECT,
        decision_source="model",
        knowledge_points=(),
        error_categories=(),
        confidence=0.90,
        reason="clear",
    )


def test_mixed_batch_gets_stable_subject_children(document, analysis):
    plan = build_subject_split_plan(document, analysis, {}, 0.80)

    assert [(group.subject, group.document_id, group.page_numbers) for group in plan.groups] == [
        (Subject.MATH, f"{document.document_id}--math", (1, 2)),
        (Subject.ENGLISH, f"{document.document_id}--english", (3,)),
    ]


def test_low_confidence_forces_confirmation_even_when_model_says_false(document, analysis):
    analysis = replace(
        analysis,
        page_subjects=(
            PageSubjectAssignment(1, Subject.MATH, 0.79, False, "数字较模糊"),
            PageSubjectAssignment(2, Subject.MATH, 0.81, False, "应用题"),
            PageSubjectAssignment(3, Subject.ENGLISH, 0.90, False, "阅读题"),
        ),
    )

    plan = build_subject_split_plan(document, analysis, {}, 0.80)

    assert tuple(item.page for item in plan.unresolved_pages) == (1,)


def test_split_analysis_keeps_questions_with_their_pages(document, analysis):
    plan = build_subject_split_plan(document, analysis, {}, 0.80)

    math, english = split_analysis(analysis, plan)

    assert {question.page for question in math.questions} == {1, 2}
    assert {question.page for question in english.questions} == {3}
    assert math.document_id.endswith("--math")
    assert english.document_id.endswith("--english")


def test_human_confirmation_records_human_source_even_when_subject_matches_model(document, analysis):
    analysis = replace(
        analysis,
        page_subjects=(
            PageSubjectAssignment(1, Subject.MATH, 0.79, False, "数字较模糊"),
            PageSubjectAssignment(2, Subject.MATH, 0.81, False, "应用题"),
            PageSubjectAssignment(3, Subject.ENGLISH, 0.90, False, "阅读题"),
        ),
    )

    plan = build_subject_split_plan(document, analysis, {1: Subject.MATH}, 0.80)

    assert [(item.page, item.subject, item.source, item.confidence) for item in plan.resolved_pages] == [
        (1, Subject.MATH, "human", 1.0),
        (2, Subject.MATH, "model", 0.81),
        (3, Subject.ENGLISH, "model", 0.90),
    ]


def test_unresolved_pages_prevent_partial_groups(document, analysis):
    analysis = replace(
        analysis,
        page_subjects=(
            PageSubjectAssignment(1, Subject.MATH, 0.95, False, "计算题"),
            PageSubjectAssignment(2, Subject.MATH, 0.79, False, "数字较模糊"),
            PageSubjectAssignment(3, Subject.ENGLISH, 0.90, False, "阅读题"),
        ),
    )

    plan = build_subject_split_plan(document, analysis, {}, 0.80)

    assert plan.groups == ()
    assert tuple(item.page for item in plan.unresolved_pages) == (2,)


@pytest.mark.parametrize("overrides", [{4: Subject.MATH}, {1: Subject.ENGLISH}])
def test_rejects_unknown_or_unnecessary_human_override(document, analysis, overrides):
    with pytest.raises(ValueError):
        build_subject_split_plan(document, analysis, overrides, 0.80)


def test_rejects_missing_or_duplicate_page_assignments(document, analysis):
    missing = replace(analysis, page_subjects=analysis.page_subjects[:-1])
    duplicate = replace(analysis, page_subjects=(*analysis.page_subjects, analysis.page_subjects[-1]))

    with pytest.raises(ValueError):
        build_subject_split_plan(document, missing, {}, 0.80)
    with pytest.raises(ValueError):
        build_subject_split_plan(document, duplicate, {}, 0.80)


def test_rejects_runtime_malformed_model_subject_before_creating_partial_groups(document, analysis):
    malformed = replace(
        analysis,
        page_subjects=(
            PageSubjectAssignment(1, Subject.MATH, 0.95, False, "计算题"),
            PageSubjectAssignment(2, "科学", 0.95, False, "运行时损坏"),
            PageSubjectAssignment(3, Subject.ENGLISH, 0.90, False, "阅读题"),
        ),
    )

    with pytest.raises(ValueError):
        build_subject_split_plan(document, malformed, {}, 0.80)


def test_split_analysis_uses_final_assignments_and_effective_confidence(document, analysis):
    analysis = replace(
        analysis,
        page_subjects=(
            PageSubjectAssignment(1, Subject.ENGLISH, 0.79, True, "unclear"),
            PageSubjectAssignment(2, Subject.MATH, 0.81, False, "应用题"),
            PageSubjectAssignment(3, Subject.ENGLISH, 0.90, False, "阅读题"),
        ),
    )
    plan = build_subject_split_plan(document, analysis, {1: Subject.MATH}, 0.80)

    math, _english = split_analysis(analysis, plan)

    assert math.subject == Subject.MATH
    assert math.subject_confidence == 0.81
    assert tuple((item.page, item.suggested_subject, item.confidence) for item in math.page_subjects) == (
        (1, Subject.MATH, 1.0),
        (2, Subject.MATH, 0.81),
    )
    assert math.teacher_mark_evidence == analysis.teacher_mark_evidence
    assert math.summary == "来自批次 capture-001，共 2 页、2 题。"


@pytest.mark.parametrize(
    "plan",
    [
        SubjectSplitPlan("capture-001", (), (), ()),
        SubjectSplitPlan(
            "capture-001",
            (SubjectPageGroup(Subject.MATH, "capture-001", (1,)),),
            (),
            (ResolvedPageSubject(1, Subject.MATH, "model", 0.95),),
        ),
    ],
)
def test_split_analysis_rejects_empty_or_incomplete_plan(document, analysis, plan):
    with pytest.raises(ValueError):
        split_analysis(analysis, plan)


def test_split_analysis_rejects_duplicate_subject_groups(document, analysis):
    plan = build_subject_split_plan(document, analysis, {}, 0.80)
    malformed = replace(
        plan,
        groups=(
            SubjectPageGroup(Subject.MATH, f"{document.document_id}--math", (1,)),
            SubjectPageGroup(Subject.MATH, f"{document.document_id}--math", (2,)),
            plan.groups[1],
        ),
    )

    with pytest.raises(ValueError):
        split_analysis(analysis, malformed)


def test_split_analysis_rejects_arbitrary_child_document_id(document, analysis):
    plan = build_subject_split_plan(document, analysis, {}, 0.80)
    malformed = replace(
        plan,
        groups=(
            SubjectPageGroup(Subject.MATH, "arbitrary-id", (1, 2)),
            plan.groups[1],
        ),
    )

    with pytest.raises(ValueError):
        split_analysis(analysis, malformed)
