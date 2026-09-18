"""Build deterministic report facts before any narrative model is called."""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from hashlib import sha256
from typing import Any

from qingzi_learning.storage.repository import KnowledgeRepository


def evidence_level(question_count: int, study_day_count: int) -> str:
    """Return a wording guard, not an educational diagnosis."""
    if question_count <= 0:
        return "no_data"
    if question_count < 10 or study_day_count < 2:
        return "initial"
    if question_count < 30 or study_day_count < 3:
        return "forming"
    return "stable"


class LearningProfileBuilder:
    def __init__(self, repo: KnowledgeRepository) -> None:
        self.repo = repo

    def build(
        self, previous_snapshot: dict[str, Any] | None, *, cutoff_at: datetime
    ) -> dict[str, Any]:
        if cutoff_at.tzinfo is None:
            raise ValueError("报告截止时间必须包含时区")
        cutoff = cutoff_at.astimezone(timezone.utc)
        cutoff_sql = cutoff.strftime("%Y-%m-%d %H:%M:%S")
        evidence = self.repo.report_evidence_rows(cutoff_sql)
        retest_rows = self.repo.report_retest_rows(cutoff_sql)
        point_stats = {
            (item["subject"], item["knowledge_point"]): item
            for item in evidence["knowledge_stats"]
        }
        rows = evidence["effective_rows"]
        questions: dict[tuple[str, str], dict[str, Any]] = {}
        for row in rows:
            key = (row["document_id"], row["question_id"])
            question = questions.setdefault(key, {
                "document_id": row["document_id"],
                "question_id": row["question_id"],
                "subject": row["subject"],
                "status": row["status"],
                "created_at": row["created_at"],
                "prompt_summary": row["prompt_summary"],
                "error_categories": tuple(row["error_categories"]),
                "knowledge_points": [],
            })
            point = row["knowledge_point"]
            if point is not None and point not in question["knowledge_points"]:
                question["knowledge_points"].append(point)

        previous_subjects = (previous_snapshot or {}).get("subjects", {})
        subjects: dict[str, dict[str, Any]] = {}
        for subject in self.repo.config.subjects:
            subject_questions = [q for q in questions.values() if q["subject"] == subject]
            document_ids = {q["document_id"] for q in subject_questions}
            study_days = {q["created_at"][:10] for q in subject_questions}
            grouped: dict[str, list[dict[str, Any]]] = {}
            for question in subject_questions:
                for point in question["knowledge_points"]:
                    grouped.setdefault(point, []).append(question)
            previous_points = previous_subjects.get(subject, {}).get("knowledge_points", {})
            knowledge_points: dict[str, dict[str, Any]] = {}
            ordered = sorted(
                grouped,
                key=lambda point: (
                    -int(point_stats.get((subject, point), {}).get("review_priority", 0)),
                    point,
                ),
            )
            for point in ordered:
                attempts = grouped[point]
                counts = Counter(item["status"] for item in attempts)
                exposure = len(attempts)
                mastery = (counts["correct"] + counts["partial"] * 0.5) / exposure
                errors = Counter(
                    category
                    for item in attempts
                    if item["status"] in {"incorrect", "partial"}
                    for category in item["error_categories"]
                )
                previous = previous_points.get(point)
                delta = {
                    name: value - int((previous or {}).get(name, 0))
                    for name, value in {
                        "exposure_count": exposure,
                        "correct_count": counts["correct"],
                        "incorrect_count": counts["incorrect"],
                        "partial_count": counts["partial"],
                    }.items()
                }
                if previous is None:
                    change = "new"
                elif delta["exposure_count"] <= 0:
                    change = "unchanged"
                else:
                    difference = mastery - float(previous.get("mastery_rate", 0.0))
                    change = "improved" if difference > 0.05 else "declined" if difference < -0.05 else "steady"
                stored = point_stats.get((subject, point), {})
                knowledge_points[point] = {
                    "evidence_id": "kp-" + sha256(f"{subject}\0{point}".encode("utf-8")).hexdigest()[:12],
                    "knowledge_point": point,
                    "exposure_count": exposure,
                    "correct_count": counts["correct"],
                    "incorrect_count": counts["incorrect"],
                    "partial_count": counts["partial"],
                    "mastery_rate": mastery,
                    "review_priority": int(stored.get("review_priority", 0)),
                    "trend": str(stored.get("trend", "no_data")),
                    "last_seen_at": max(item["created_at"] for item in attempts),
                    "error_categories": [
                        {"name": name, "count": count}
                        for name, count in sorted(errors.items(), key=lambda item: (-item[1], item[0]))
                    ],
                    "representative_questions": [
                        {
                            "document_id": item["document_id"],
                            "question_id": item["question_id"],
                            "prompt_summary": item["prompt_summary"],
                            "status": item["status"],
                        }
                        for item in attempts[-3:]
                    ],
                    "delta": delta,
                    "change": change,
                }
            subjects[subject] = {
                "question_count": len(subject_questions),
                "document_count": len(document_ids),
                "study_day_count": len(study_days),
                "evidence_level": evidence_level(len(subject_questions), len(study_days)),
                "correct_count": sum(q["status"] == "correct" for q in subject_questions),
                "incorrect_count": sum(q["status"] == "incorrect" for q in subject_questions),
                "partial_count": sum(q["status"] == "partial" for q in subject_questions),
                "knowledge_points": knowledge_points,
            }

        document_ids = {q["document_id"] for q in questions.values()}
        previous_summary = (previous_snapshot or {}).get("summary", {})
        profile = {
            "schema_version": 1,
            "cutoff_at": cutoff.isoformat().replace("+00:00", "Z"),
            "summary": {
                "question_count": len(questions),
                "document_count": len(document_ids),
                "pending_count": evidence["pending_count"],
                "excluded_low_confidence_count": evidence["excluded_low_confidence_count"],
            },
            "subjects": subjects,
            "retests": self._retests(retest_rows, previous_snapshot, subjects),
        }
        profile["delta"] = {
            "question_count": len(questions) - int(previous_summary.get("question_count", 0)),
            "new_document_count": len(document_ids) - int(previous_summary.get("document_count", 0)),
            "pending_count": evidence["pending_count"] - int(previous_summary.get("pending_count", 0)),
        }
        return profile

    @staticmethod
    def _retests(
        rows: list[dict[str, Any]],
        previous_snapshot: dict[str, Any] | None,
        subjects: dict[str, dict[str, Any]],
    ) -> list[dict[str, Any]]:
        grouped: dict[tuple[str, str], dict[str, Any]] = {}
        previous_subjects = (previous_snapshot or {}).get("subjects", {})
        for row in rows:
            key = (row["exam_id"], row["exam_question_id"])
            item = grouped.setdefault(key, {
                "exam_id": row["exam_id"],
                "exam_question_id": row["exam_question_id"],
                "subject": row["subject"],
                "attempt_count": 0,
                "previous_status": "unknown",
                "current_status": row["current_status"],
                "knowledge_points": [],
                "attempts": [],
            })
            item["attempt_count"] += 1
            item["current_status"] = row["current_status"]
            for point in row["knowledge_points"]:
                if point not in item["knowledge_points"]:
                    item["knowledge_points"].append(point)
            item["attempts"].append({
                "document_id": row["document_id"],
                "document_question_id": row["document_question_id"],
                "status": row["current_status"],
                "linked_at": row["linked_at"],
            })
            previous = LearningProfileBuilder._blueprint_previous_status(
                row["blueprint"], row["knowledge_points"]
            )
            if previous == "unknown":
                previous = LearningProfileBuilder._snapshot_previous_status(
                    previous_subjects, row["subject"], row["knowledge_points"]
                )
            if item["previous_status"] == "unknown":
                item["previous_status"] = previous
        return list(grouped.values())

    @staticmethod
    def _blueprint_previous_status(blueprint: dict[str, Any], points: list[str]) -> str:
        statuses = []
        for target in blueprint.get("targets", []):
            if target.get("knowledge_point") not in points:
                continue
            statuses.extend(
                question.get("status")
                for question in target.get("representative_questions", [])
                if question.get("status") in {"incorrect", "partial", "correct"}
            )
        for preferred in ("incorrect", "partial", "correct"):
            if preferred in statuses:
                return preferred
        return "unknown"

    @staticmethod
    def _snapshot_previous_status(
        subjects: dict[str, Any], subject: str, points: list[str]
    ) -> str:
        facts = subjects.get(subject, {}).get("knowledge_points", {})
        selected = [facts[point] for point in points if point in facts]
        if any(item.get("incorrect_count", 0) for item in selected):
            return "incorrect"
        if any(item.get("partial_count", 0) for item in selected):
            return "partial"
        if any(item.get("correct_count", 0) for item in selected):
            return "correct"
        return "unknown"
