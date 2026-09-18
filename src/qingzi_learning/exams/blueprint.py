"""Build a deterministic, evidence-backed plan for a targeted practice exam."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from math import floor
from typing import Any

from qingzi_learning.reporting.profile import LearningProfileBuilder
from qingzi_learning.storage.repository import KnowledgeRepository


_CATEGORIES = ("primary", "related", "stable")
_WEIGHTS = {"primary": 0.50, "related": 0.30, "stable": 0.20}


@dataclass(frozen=True)
class ExamRequest:
    subject: str
    scope: str
    duration_minutes: int
    difficulty: str
    question_count: int
    include_composition: bool
    include_reading: bool


def allocate_question_counts(total: int, available: dict[str, bool]) -> dict[str, int]:
    """Apply the 50/30/20 policy, then move unavailable shares predictably."""
    if total <= 0:
        raise ValueError("题量必须大于零")
    if not any(available.get(name, False) for name in _CATEGORIES):
        raise ValueError("没有可用于组卷的有效学习证据")

    raw = {name: total * _WEIGHTS[name] for name in _CATEGORIES}
    result = {name: floor(raw[name]) for name in _CATEGORIES}
    remainder = total - sum(result.values())
    ranked = sorted(_CATEGORIES, key=lambda name: (-(raw[name] - result[name]), _CATEGORIES.index(name)))
    for name in ranked[:remainder]:
        result[name] += 1

    missing = sum(result[name] for name in _CATEGORIES if not available.get(name, False))
    for name in _CATEGORIES:
        if not available.get(name, False):
            result[name] = 0
    recipients = [name for name in _CATEGORIES if available.get(name, False)]
    for index in range(missing):
        result[recipients[index % len(recipients)]] += 1
    return result


class BlueprintBuilder:
    def __init__(self, repo: KnowledgeRepository) -> None:
        self.repo = repo

    def build(self, request: ExamRequest) -> dict[str, Any]:
        self._validate(request)
        profile = LearningProfileBuilder(self.repo).build(
            None, cutoff_at=datetime.now(timezone.utc)
        )
        subject_profile = profile["subjects"][request.subject]
        points = subject_profile["knowledge_points"]
        selected = {
            name: facts for name, facts in points.items()
            if self._matches_scope(request.scope, name, facts)
        }
        if request.scope.strip() and not selected:
            raise ValueError("考试范围内还没有已确认的有效学习证据")
        if not selected:
            raise ValueError("该学科还没有可用于组卷的有效学习证据")

        primary_names = {
            name for name, facts in selected.items()
            if facts["incorrect_count"] or facts["partial_count"] or facts["review_priority"] > 0
        }
        stable_names = {
            name for name, facts in selected.items()
            if facts["mastery_rate"] >= 0.80 and name not in primary_names
        }
        related_names = self._related_points(primary_names, selected) - primary_names - stable_names

        categories = {
            "primary": self._targets(request.subject, selected, primary_names, "primary"),
            "related": self._targets(request.subject, selected, related_names, "related"),
            "stable": self._targets(request.subject, selected, stable_names, "stable"),
        }
        available = {name: bool(categories[name]) for name in _CATEGORIES}
        allocation = allocate_question_counts(request.question_count, available)
        slots: list[dict[str, Any]] = []
        number = 1
        for category in _CATEGORIES:
            targets = categories[category]
            for index in range(allocation[category]):
                target = targets[index % len(targets)]
                slots.append({
                    "slot": number,
                    "category": category,
                    "subject": target["subject"],
                    "knowledge_point": target["knowledge_point"],
                    "evidence_id": target["evidence_id"],
                    "difficulty": request.difficulty,
                })
                number += 1

        absent = [name for name in _CATEGORIES if not available[name]]
        note = "按50%重点短板、30%关联巩固、20%稳定保持分配。"
        if absent:
            chinese = {"primary": "重点短板", "related": "关联巩固", "stable": "稳定保持"}
            note += "因%s证据不足，题量已自动补入现有类别。" % "、".join(chinese[name] for name in absent)
        return {
            "schema_version": 1,
            "request": asdict(request),
            "allocation": allocation,
            "allocation_note": note,
            "targets": [item for name in _CATEGORIES for item in categories[name]],
            "slots": slots,
            "evidence_summary": {
                "question_count": subject_profile["question_count"],
                "pending_count": profile["summary"]["pending_count"],
                "excluded_low_confidence_count": profile["summary"]["excluded_low_confidence_count"],
            },
        }

    def _validate(self, request: ExamRequest) -> None:
        if request.subject not in self.repo.config.subjects:
            raise ValueError("请选择有效学科")
        if request.difficulty not in {"基础", "适中", "提高"}:
            raise ValueError("请选择有效难度")
        if not 5 <= request.question_count <= 50:
            raise ValueError("题量应为5到50题")
        if not 10 <= request.duration_minutes <= 180:
            raise ValueError("考试时长应为10到180分钟")

    @staticmethod
    def _matches_scope(scope: str, name: str, facts: dict[str, Any]) -> bool:
        needle = "".join(scope.casefold().split())
        if not needle:
            return True
        haystacks = [name, *(item["prompt_summary"] for item in facts["representative_questions"])]
        return any(needle in "".join(value.casefold().split()) for value in haystacks)

    def _related_points(
        self, primary_names: set[str], selected: dict[str, dict[str, Any]]
    ) -> set[str]:
        if not primary_names:
            return set()
        document_questions: dict[tuple[str, str], set[str]] = {}
        for name, facts in selected.items():
            for item in facts["representative_questions"]:
                document_questions.setdefault(
                    (item["document_id"], item["question_id"]), set()
                ).add(name)
        related: set[str] = set()
        for names in document_questions.values():
            if names & primary_names:
                related.update(names - primary_names)
        return related

    @staticmethod
    def _targets(
        subject: str,
        selected: dict[str, dict[str, Any]],
        names: set[str],
        category: str,
    ) -> list[dict[str, Any]]:
        ordered = sorted(
            names,
            key=lambda name: (-selected[name]["review_priority"], name),
        )
        return [
            {
                "subject": subject,
                "knowledge_point": name,
                "evidence_id": selected[name]["evidence_id"],
                "category": category,
                "review_priority": selected[name]["review_priority"],
                "error_categories": selected[name]["error_categories"],
                "representative_questions": selected[name]["representative_questions"],
            }
            for name in ordered
        ]
