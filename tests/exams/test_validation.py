from dataclasses import replace

import pytest

from qingzi_learning.exams.blueprint import ExamRequest


@pytest.fixture
def exam_request() -> ExamRequest:
    return ExamRequest("数学", "分数", 40, "适中", 5, False, False)


@pytest.fixture
def blueprint() -> dict:
    slots = [
        {
            "slot": number,
            "category": "primary",
            "subject": "数学",
            "knowledge_point": "分数应用",
            "evidence_id": "kp-fraction",
            "difficulty": "适中",
        }
        for number in range(1, 6)
    ]
    return {
        "schema_version": 1,
        "request": {},
        "allocation": {"primary": 5, "related": 0, "stable": 0},
        "allocation_note": "test",
        "targets": [{"knowledge_point": "分数应用", "category": "primary"}],
        "slots": slots,
    }


def valid_generation() -> dict:
    points = [20, 20, 20, 20, 20]
    return {
        "title": "五年级数学针对性练习",
        "instructions": "认真读题，写出必要步骤。",
        "questions": [
            {
                "question_id": f"Q{index:02d}",
                "question_type": "application",
                "points": points[index - 1],
                "knowledge_points": ["分数应用"],
                "blueprint_category": "primary",
                "prompt": f"第{index}个新的分数应用情境题，请列式计算。",
                "answer": f"{index}/10",
                "explanation": f"先找单位一，再计算第{index}题。",
                "rubric": "列式正确10分，计算正确10分。",
            }
            for index in range(1, 6)
        ],
    }


def test_validator_accepts_a_complete_new_exam(exam_request, blueprint) -> None:
    from qingzi_learning.exams.validation import validate_exam

    questions = validate_exam(exam_request, blueprint, valid_generation())

    assert len(questions) == 5
    assert sum(item.points for item in questions) == 100
    assert questions[0].question_id == "Q01"


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda value: value["questions"][0].update(prompt="答案：3/4，请计算……"), "答案"),
        (lambda value: value["questions"][0].update(knowledge_points=["不存在"]), "知识点"),
        (lambda value: value["questions"][1].update(question_id="Q01"), "题号"),
        (lambda value: value["questions"][1].update(prompt=value["questions"][0]["prompt"]), "重复"),
        (lambda value: value["questions"][0].update(points=19), "100"),
    ],
)
def test_validator_fails_closed_for_unsafe_or_inconsistent_content(
    exam_request, blueprint, mutate, message
) -> None:
    from qingzi_learning.exams.validation import ExamValidationError, validate_exam

    generation = valid_generation()
    mutate(generation)
    with pytest.raises(ExamValidationError, match=message):
        validate_exam(exam_request, blueprint, generation)


def test_validator_rejects_answer_text_hidden_in_prompt(exam_request, blueprint) -> None:
    from qingzi_learning.exams.validation import ExamValidationError, validate_exam

    generation = valid_generation()
    generation["questions"][0]["prompt"] += " 结果是1/10。"
    with pytest.raises(ExamValidationError, match="泄露"):
        validate_exam(exam_request, blueprint, generation)


@pytest.mark.parametrize(
    ("prompt", "answer"),
    [
        ("选择合适的单词填空：Tom cannot sing ___ dance.（and / or）", "or"),
        ("选择合适的词填空：There ___ a ruler in the box.（is / are）", "is"),
    ],
)
def test_validator_allows_correct_answer_inside_explicit_blank_options(
    exam_request, blueprint, prompt, answer
) -> None:
    """Removing option blocks would wrongly reject ordinary multiple-choice blanks."""
    from qingzi_learning.exams.validation import validate_exam

    generation = valid_generation()
    generation["questions"][0].update(
        question_type="fill_blank", prompt=prompt, answer=answer
    )

    questions = validate_exam(exam_request, blueprint, generation)

    assert questions[0].answer == answer


def test_validator_does_not_treat_an_operand_equal_to_the_result_as_a_leak(
    exam_request, blueprint
) -> None:
    """The same number can legitimately be both an input value and the result."""
    from qingzi_learning.exams.validation import validate_exam

    generation = valid_generation()
    generation["questions"][0].update(
        question_type="calculation", prompt="计算10×2÷2。", answer="10"
    )

    questions = validate_exam(exam_request, blueprint, generation)

    assert questions[0].answer == "10"


def test_validator_checks_requested_question_count(exam_request, blueprint) -> None:
    from qingzi_learning.exams.validation import ExamValidationError, validate_exam

    with pytest.raises(ExamValidationError, match="题量"):
        validate_exam(replace(exam_request, question_count=6), blueprint, valid_generation())
