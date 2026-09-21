"""Conservative projection of confirmed learning evidence onto canonical concepts."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.resources import files
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping

from qingzi_learning.curriculum.graph import MathGraph, SourceMapping, load_math_graph


_SOURCE_TYPES = {"textbook", "calculation_training", "tutoring", "assessment"}
_RELATIONS = {"curriculum_covers", "trains", "supplements", "assesses"}
_STATUSES = {"confirmed", "pending", "unmapped", "cross_subject"}
_ENGLISH_GRAMMAR = re.compile(
    r"(?:情态动词|一般将来时|一般过去时|一般现在时|现在进行时|过去进行时|"
    r"\b(?:can|could|would|should|like|doing|to do|future tense|past tense)\b)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ConceptMastery:
    concept_id: str
    state: str
    weighted_rate: float | None
    evidence_count: int
    recent_count: int
    last_seen_at: str | None
    trend: str


@dataclass(frozen=True)
class MappingAuditItem:
    label: str
    status: str
    concept_ids: tuple[str, ...]
    evidence_count: int


@dataclass(frozen=True)
class MasteryProjection:
    by_concept: Mapping[str, ConceptMastery]
    audit: tuple[MappingAuditItem, ...]

    @property
    def audit_by_label(self) -> Mapping[str, MappingAuditItem]:
        return MappingProxyType({item.label: item for item in self.audit})


def load_course_mappings(path: Path | None = None) -> tuple[SourceMapping, ...]:
    """Load explicit, evidence-labelled mappings for the photographed BNU book."""
    if path is None:
        resource = files("qingzi_learning.curriculum").joinpath(
            "mappings/bnu_math_g5_upper_2024.json"
        )
        payload = json.loads(resource.read_text(encoding="utf-8"))
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("课程知识映射格式无效")
    raw_mappings = payload.get("mappings")
    if not isinstance(raw_mappings, list) or not raw_mappings:
        raise ValueError("课程知识映射为空")
    graph = load_math_graph()
    results: list[SourceMapping] = []
    seen: set[str] = set()
    for raw in raw_mappings:
        if not isinstance(raw, dict):
            raise ValueError("课程知识映射无效")
        source_id = _required_text(raw.get("source_id"), "来源编号")
        if not re.fullmatch(r"[a-z][a-z0-9-]{1,119}", source_id) or source_id in seen:
            raise ValueError("课程来源编号无效或重复")
        seen.add(source_id)
        source_label = _required_text(raw.get("source_label"), "来源名称")
        source_type, relation, status = raw.get("source_type"), raw.get("relation"), raw.get("status")
        targets = raw.get("target_ids")
        if (source_type not in _SOURCE_TYPES or relation not in _RELATIONS
                or status not in _STATUSES or not isinstance(targets, list)
                or any(target not in graph.nodes for target in targets)
                or len(targets) != len(set(targets))
                or (status == "confirmed" and not targets)):
            raise ValueError("课程知识映射属性无效")
        results.append(
            SourceMapping(
                source_id=source_id,
                source_label=source_label,
                source_type=source_type,
                target_ids=tuple(targets),
                relation=relation,
                status=status,
                basis=_required_text(raw.get("basis"), "映射依据"),
            )
        )
    return tuple(results)


def build_mastery_projection(
    graph: MathGraph,
    snapshot: dict[str, Any],
    now: datetime | None = None,
) -> MasteryProjection:
    """Join only exact, confirmed aliases; fuzzy matches remain auditable and inert."""
    now = now or datetime.now(timezone.utc)
    del now  # reserved for future recency buckets; current repository already owns trends
    aliases: dict[str, str] = {}
    for node in graph.nodes.values():
        for label in (node.label, *node.aliases):
            aliases[_normalize(label)] = node.node_id

    totals = {
        node_id: {
            "exposure": 0,
            "correct": 0,
            "partial": 0,
            "incorrect": 0,
            "recent": 0,
            "last_seen": None,
            "trends": [],
        }
        for node_id in graph.nodes
    }
    audit: list[MappingAuditItem] = []
    points = snapshot.get("knowledge_points", [])
    if not isinstance(points, list):
        raise ValueError("数学知识统计格式无效")
    for row in points:
        if not isinstance(row, dict):
            raise ValueError("数学知识统计条目无效")
        label = _required_text(row.get("knowledge_point"), "知识点名称")
        exposure = _nonnegative_int(row.get("exposure_count", 0), "知识点样本数")
        normalized = _normalize(label)
        concept_id = aliases.get(normalized)
        if concept_id is None:
            status = "cross_subject" if _ENGLISH_GRAMMAR.search(label) else "unmapped"
            audit.append(MappingAuditItem(label, status, (), exposure))
            continue
        correct = _nonnegative_int(row.get("correct_count", 0), "正确数")
        partial = _nonnegative_int(row.get("partial_count", 0), "部分正确数")
        incorrect = _nonnegative_int(row.get("incorrect_count", 0), "错误数")
        recent = _nonnegative_int(row.get("recent_count", 0), "近期样本数")
        bucket = totals[concept_id]
        bucket["exposure"] += exposure
        bucket["correct"] += correct
        bucket["partial"] += partial
        bucket["incorrect"] += incorrect
        bucket["recent"] += recent
        last_seen = row.get("last_seen_at")
        if isinstance(last_seen, str) and last_seen and (
            bucket["last_seen"] is None or last_seen > bucket["last_seen"]
        ):
            bucket["last_seen"] = last_seen
        trend = row.get("trend")
        if isinstance(trend, str) and trend not in {"", "no_data", "insufficient_data"}:
            bucket["trends"].append(trend)
        audit.append(MappingAuditItem(label, "confirmed", (concept_id,), exposure))

    projected: dict[str, ConceptMastery] = {}
    for concept_id, bucket in totals.items():
        exposure = bucket["exposure"]
        rate = None if exposure == 0 else min(
            1.0, (bucket["correct"] + 0.5 * bucket["partial"]) / exposure
        )
        state = _mastery_state(exposure, rate)
        trend = bucket["trends"][-1] if bucket["recent"] >= 5 and bucket["trends"] else "unknown"
        projected[concept_id] = ConceptMastery(
            concept_id=concept_id,
            state=state,
            weighted_rate=rate,
            evidence_count=exposure,
            recent_count=bucket["recent"],
            last_seen_at=bucket["last_seen"],
            trend=trend,
        )
    return MasteryProjection(MappingProxyType(projected), tuple(audit))


def _mastery_state(exposure: int, rate: float | None) -> str:
    if exposure == 0 or rate is None:
        return "no_data"
    if exposure < 5:
        return "evidence_insufficient"
    if rate >= 0.85:
        return "stable"
    if rate >= 0.70:
        return "basic"
    if rate >= 0.50:
        return "unstable"
    return "weak"


def _normalize(value: str) -> str:
    return " ".join(value.strip().split()).casefold()


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label}无效")
    return value.strip()


def _nonnegative_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label}无效")
    return value
