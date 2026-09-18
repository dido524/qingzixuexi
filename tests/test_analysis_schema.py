import json
from importlib.resources import files

import pytest
from jsonschema import ValidationError, validate

from qingzi_learning.domain import QuestionAnalysis, QuestionStatus, validate_analysis_payload


@pytest.fixture
def schema():
    return json.loads(
        (files("qingzi_learning.schema") / "analysis-result.schema.json").read_text("utf-8")
    )


@pytest.fixture
def valid_payload():
    return {
        "document_id": "doc-20260915-001",
        "subject": "数学",
        "subject_confidence": 0.96,
        "document_type": "试卷",
        "source_exam_id": None,
        "grading_mode": "mixed",
        "teacher_mark_evidence": ["page_002: red cross near question 4"],
        "page_subjects": [
            {
                "page": 1,
                "suggested_subject": "数学",
                "confidence": 0.99,
                "needs_confirmation": False,
                "reason": "计算题",
            },
        ],
        "questions": [
            {
                "question_id": "4",
                "source_exam_question_id": None,
                "question_type": "application",
                "page": 2,
                "prompt_summary": "分数除法应用题",
                "student_answer": "12",
                "reference_answer": "18",
                "status": "incorrect",
                "decision_source": "teacher",
                "knowledge_points": ["分数除法应用题"],
                "error_categories": ["列式"],
                "confidence": 0.93,
                "reason": "单位1识别错误",
            }
        ],
        "summary": "本次主要问题是单位1识别。",
    }


def test_mixed_analysis_payload_matches_schema(valid_payload, schema):
    validate(valid_payload, schema)


def test_schema_rejects_unknown_subject(valid_payload, schema):
    valid_payload["subject"] = "科学"
    with pytest.raises(ValidationError):
        validate(valid_payload, schema)


def test_schema_rejects_fabricated_question_fields(valid_payload, schema):
    valid_payload["questions"][0]["invented_fact"] = "not evidenced"
    with pytest.raises(ValidationError):
        validate(valid_payload, schema)


def test_teacher_marked_analysis_rejects_model_only_decision(valid_payload, schema):
    valid_payload["grading_mode"] = "teacher_marked"
    valid_payload["questions"][0]["decision_source"] = "model"
    with pytest.raises(ValidationError):
        validate(valid_payload, schema)


@pytest.mark.parametrize(
    ("status", "confidence", "expected"),
    [
        (QuestionStatus.CORRECT, 0.80, True),
        (QuestionStatus.NEEDS_REVIEW, 0.99, False),
        (QuestionStatus.INCORRECT, 0.79, False),
    ],
)
def test_only_confident_confirmed_questions_count_toward_mastery(
    status, confidence, expected
):
    question = QuestionAnalysis(
        question_id="4",
        question_type="application",
        page=2,
        prompt_summary="分数除法应用题",
        student_answer="12",
        reference_answer="18",
        status=status,
        decision_source="teacher",
        knowledge_points=("分数除法应用题",),
        error_categories=("列式",),
        confidence=confidence,
        reason="单位1识别错误",
    )
    assert question.counts_toward_mastery is expected


def test_validate_analysis_payload_uses_packaged_schema(valid_payload):
    assert validate_analysis_payload(valid_payload) is None


def test_normal_homework_requires_explicit_null_exam_fields(valid_payload):
    assert validate_analysis_payload(valid_payload) is None
    del valid_payload["source_exam_id"]
    with pytest.raises(ValidationError):
        validate_analysis_payload(valid_payload)


def test_exam_question_identifier_is_required_even_for_normal_homework(valid_payload):
    del valid_payload["questions"][0]["source_exam_question_id"]
    with pytest.raises(ValidationError):
        validate_analysis_payload(valid_payload)


@pytest.mark.parametrize("subject", ["科学", "", None])
def test_schema_rejects_invalid_page_subject(valid_payload, subject):
    """Reject a page mapping whose subject is outside the supported subjects."""
    valid_payload["page_subjects"][0]["suggested_subject"] = subject
    with pytest.raises(ValidationError):
        validate_analysis_payload(valid_payload)


@pytest.mark.parametrize("value", [None, "invented"])
def test_question_type_is_required_and_restricted(valid_payload, value):
    if value is None:
        del valid_payload["questions"][0]["question_type"]
    else:
        valid_payload["questions"][0]["question_type"] = value
    with pytest.raises(ValidationError):
        validate_analysis_payload(valid_payload)
