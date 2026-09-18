"""Fail-closed validation for model-generated practice exams."""

from __future__ import annotations

from importlib.resources import files
import json
import re
from typing import Any

from jsonschema import ValidationError, validate

from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.storage.repository import ExamQuestion


class ExamValidationError(ValueError):
    pass


def _answer_search_prompt(item: dict[str, Any], prompt: str) -> str:
    """Exclude a displayed choice list from answer-leak detection.

    A blank question must display its candidate answers to be answerable.  The
    surrounding question text is still checked, so an answer repeated as a
    hint outside the option block remains a validation failure.
    """
    if item["question_type"] not in {"objective", "fill_blank"}:
        return prompt
    if not re.search(r"_{2,}|（\s*）|\(\s*\)", prompt):
        return prompt
    return re.sub(r"[（(][^（）()]{1,480}[）)]", "", prompt)


def _explicitly_reveals_answer(
    item: dict[str, Any], prompt: str, answer: str
) -> bool:
    searched = re.sub(r"\s+", "", _answer_search_prompt(item, prompt.casefold()))
    normalized_answer = re.sub(r"\s+", "", answer.casefold())
    if len(normalized_answer) < 2:
        return False
    markers = (
        "结果是", "结果为", "正确答案是", "正确答案为", "正确选项是", "正确选项为",
        "theansweris", "thecorrectansweris", "correctansweris",
    )
    return any(marker + normalized_answer in searched for marker in markers)


def validate_exam(
    request: ExamRequest, blueprint: dict[str, Any], generation: dict[str, Any]
) -> tuple[ExamQuestion, ...]:
    try:
        schema = json.loads(
            (files("qingzi_learning.schema") / "exam-generation.schema.json").read_text("utf-8")
        )
        validate(instance=generation, schema=schema)
    except (ValidationError, OSError, ValueError, TypeError) as exc:
        raise ExamValidationError("模拟卷结构不完整") from exc

    raw_questions = generation["questions"]
    if len(raw_questions) != request.question_count:
        raise ExamValidationError("模拟卷题量与要求不一致")
    expected_ids = [f"Q{number:02d}" for number in range(1, request.question_count + 1)]
    actual_ids = [item["question_id"] for item in raw_questions]
    if actual_ids != expected_ids or len(set(actual_ids)) != len(actual_ids):
        raise ExamValidationError("模拟卷题号必须连续且唯一")
    if sum(item["points"] for item in raw_questions) != 100:
        raise ExamValidationError("模拟卷总分必须为100分")

    allowed_points = {
        target["knowledge_point"] for target in blueprint.get("targets", [])
    }
    allowed_categories = {
        (slot["knowledge_point"], slot["category"])
        for slot in blueprint.get("slots", [])
    }
    if not allowed_points:
        raise ExamValidationError("组卷蓝图没有可用知识点")

    normalized_prompts: set[str] = set()
    questions = []
    unsafe = ("<script", "javascript:", "data:text/html", "答案：", "答案:", "标准答案")
    for item in raw_questions:
        points = set(item["knowledge_points"])
        if len(points) != len(item["knowledge_points"]):
            raise ExamValidationError("模拟题知识点不能重复")
        if not points or not points.issubset(allowed_points):
            raise ExamValidationError("模拟题引用了蓝图之外的知识点")
        if not any((point, item["blueprint_category"]) in allowed_categories for point in points):
            raise ExamValidationError("模拟题知识点与蓝图类别不一致")
        prompt = item["prompt"].strip()
        normalized = re.sub(r"[\W_]+", "", prompt.casefold())
        if not normalized or normalized in normalized_prompts:
            raise ExamValidationError("模拟题题干为空或重复")
        normalized_prompts.add(normalized)
        prompt_folded = prompt.casefold()
        if any(token.casefold() in prompt_folded for token in unsafe):
            raise ExamValidationError("模拟题题干包含答案或不安全内容")
        answer = item["answer"].strip()
        if _explicitly_reveals_answer(item, prompt, answer):
            raise ExamValidationError("模拟题题干泄露了答案")
        for key in ("answer", "explanation", "rubric"):
            value = item[key].strip()
            if not value or any(token in value.casefold() for token in ("<script", "javascript:")):
                raise ExamValidationError("模拟题答案或评分说明不安全")
        questions.append(ExamQuestion(
            exam_id="",
            question_id=item["question_id"],
            question_type=item["question_type"],
            points=item["points"],
            knowledge_points=tuple(item["knowledge_points"]),
            blueprint_category=item["blueprint_category"],
            prompt=prompt,
            answer=answer,
            explanation=item["explanation"].strip(),
            rubric=item["rubric"].strip(),
        ))
    return tuple(questions)
