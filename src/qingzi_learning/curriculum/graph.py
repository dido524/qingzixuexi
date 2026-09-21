"""Versioned, provider-neutral primary mathematics knowledge graph."""

from __future__ import annotations

from dataclasses import dataclass
from importlib.resources import files
import json
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any, Mapping


_ID = re.compile(r"^[a-z][a-z0-9-]{1,79}$")
_NODE_KINDS = {"domain", "theme", "concept", "extension_group", "extension_concept"}
_EDGE_KINDS = {"prerequisite", "deepens_to", "applies_to", "confusable"}
_EDGE_BASES = {"official", "teaching_research", "source_evidence"}
_SOURCE_TYPES = {"textbook", "calculation_training", "tutoring", "assessment"}
_SOURCE_RELATIONS = {"curriculum_covers", "trains", "supplements", "assesses"}
_MAPPING_STATUSES = {"confirmed", "pending", "unmapped", "cross_subject"}
_STAGES = {"第一学段", "第二学段", "第三学段", "拓展"}
_GRADES = {str(value) for value in range(1, 10)}
_COMPETENCIES = {
    "数感", "量感", "符号意识", "运算能力", "几何直观", "空间观念",
    "推理意识", "数据意识", "模型意识", "应用意识", "创新意识",
}
_DOMAINS = {"domain-number", "domain-geometry", "domain-statistics", "domain-practice", "extension"}


@dataclass(frozen=True)
class GraphNode:
    node_id: str
    label: str
    kind: str
    domain: str
    parent_id: str | None
    stages: tuple[str, ...]
    grades: tuple[str, ...]
    competencies: tuple[str, ...]
    aliases: tuple[str, ...]
    official: bool


@dataclass(frozen=True)
class GraphEdge:
    source: str
    target: str
    kind: str
    basis: str
    importance: int


@dataclass(frozen=True)
class SourceMapping:
    source_id: str
    source_label: str
    source_type: str
    target_ids: tuple[str, ...]
    relation: str
    status: str
    basis: str
    provider: str = ""
    publisher: str = ""
    edition: str = ""
    grades: tuple[str, ...] = ()
    terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class MathGraph:
    graph_id: str
    version: int
    root_ids: tuple[str, ...]
    nodes: Mapping[str, GraphNode]
    edges: tuple[GraphEdge, ...]
    source_mappings: tuple[SourceMapping, ...]


def load_math_graph(path: Path | None = None) -> MathGraph:
    """Load the bundled graph or a caller-supplied graph for validation/testing."""
    if path is None:
        resource = files("qingzi_learning.curriculum").joinpath("graphs/primary_math_v1.json")
        payload = json.loads(resource.read_text(encoding="utf-8"))
    else:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return validate_math_graph(payload)


def validate_math_graph(payload: dict[str, Any]) -> MathGraph:
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise ValueError("数学知识图谱格式无效")
    graph_id = _required_id(payload.get("graph_id"), "图谱编号")
    version = payload.get("version")
    if type(version) is not int or version < 1:
        raise ValueError("图谱版本无效")

    raw_nodes = payload.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise ValueError("图谱节点缺失")
    nodes: dict[str, GraphNode] = {}
    searchable_labels: dict[str, str] = {}
    for raw in raw_nodes:
        if not isinstance(raw, dict):
            raise ValueError("图谱节点无效")
        node_id = _required_id(raw.get("node_id"), "节点编号")
        if node_id in nodes:
            raise ValueError("图谱节点编号重复")
        label = _required_text(raw.get("label"), "节点名称")
        kind = raw.get("kind")
        if kind not in _NODE_KINDS:
            raise ValueError("节点类型无效")
        domain = _required_id(raw.get("domain"), "节点领域")
        if domain not in _DOMAINS:
            raise ValueError("节点领域无效")
        parent_id = raw.get("parent_id")
        if parent_id is not None:
            parent_id = _required_id(parent_id, "父节点编号")
        stages = _string_tuple(raw.get("stages"), _STAGES, "节点学段")
        grades = _string_tuple(raw.get("grades"), _GRADES, "节点年级", allow_empty=True)
        competencies = _string_tuple(
            raw.get("competencies"), _COMPETENCIES, "核心素养", allow_empty=True
        )
        aliases = _text_tuple(raw.get("aliases"), "节点别名", allow_empty=True)
        official = raw.get("official")
        if type(official) is not bool:
            raise ValueError("节点课标属性无效")
        for term in (label, *aliases):
            normalized = _normalize_term(term)
            owner = searchable_labels.get(normalized)
            if owner is not None and owner != node_id:
                raise ValueError("节点名称或别名重复")
            searchable_labels[normalized] = node_id
        nodes[node_id] = GraphNode(
            node_id, label, kind, domain, parent_id, stages, grades,
            competencies, aliases, official,
        )

    for node in nodes.values():
        if node.parent_id is not None and node.parent_id not in nodes:
            raise ValueError("图谱父节点不存在")
        if node.official and node.parent_id is not None and not nodes[node.parent_id].official:
            raise ValueError("课标节点不能置于非课标拓展节点下")
        if node.parent_id is not None and nodes[node.parent_id].domain != node.domain:
            raise ValueError("父子节点领域不一致")
    _reject_parent_cycles(nodes)

    roots = payload.get("root_ids")
    if (not isinstance(roots, list) or not roots
            or any(not isinstance(item, str) or item not in nodes for item in roots)
            or len(roots) != len(set(roots))):
        raise ValueError("图谱根节点无效")
    if any(nodes[item].parent_id is not None or not nodes[item].official for item in roots):
        raise ValueError("图谱根节点必须是官方顶层节点")

    edges: list[GraphEdge] = []
    seen_edges: set[tuple[str, str, str]] = set()
    raw_edges = payload.get("edges")
    if not isinstance(raw_edges, list):
        raise ValueError("图谱关系无效")
    for raw in raw_edges:
        if not isinstance(raw, dict):
            raise ValueError("图谱关系无效")
        source, target = raw.get("source"), raw.get("target")
        kind, basis, importance = raw.get("kind"), raw.get("basis"), raw.get("importance")
        if (source not in nodes or target not in nodes or source == target
                or kind not in _EDGE_KINDS or basis not in _EDGE_BASES
                or type(importance) is not int or importance not in {1, 2, 3}):
            raise ValueError("图谱关系端点或属性无效")
        key = (source, target, kind)
        if key in seen_edges:
            raise ValueError("图谱关系重复")
        seen_edges.add(key)
        edges.append(GraphEdge(source, target, kind, basis, importance))

    mappings: list[SourceMapping] = []
    seen_sources: set[str] = set()
    raw_mappings = payload.get("source_mappings", [])
    if not isinstance(raw_mappings, list):
        raise ValueError("来源映射无效")
    for raw in raw_mappings:
        if not isinstance(raw, dict):
            raise ValueError("来源映射无效")
        source_id = _required_id(raw.get("source_id"), "来源编号")
        if source_id in seen_sources:
            raise ValueError("来源编号重复")
        seen_sources.add(source_id)
        source_label = _required_text(raw.get("source_label"), "来源名称")
        source_type, relation, status = raw.get("source_type"), raw.get("relation"), raw.get("status")
        target_ids = raw.get("target_ids")
        if (source_type not in _SOURCE_TYPES or relation not in _SOURCE_RELATIONS
                or status not in _MAPPING_STATUSES or not isinstance(target_ids, list)
                or any(target not in nodes for target in target_ids)
                or len(target_ids) != len(set(target_ids))
                or (status == "confirmed" and not target_ids)):
            raise ValueError("来源映射属性无效")
        basis = _required_text(raw.get("basis"), "映射依据")
        mappings.append(SourceMapping(
            source_id, source_label, source_type, tuple(target_ids), relation, status, basis
        ))

    return MathGraph(
        graph_id=graph_id,
        version=version,
        root_ids=tuple(roots),
        nodes=MappingProxyType(nodes),
        edges=tuple(edges),
        source_mappings=tuple(mappings),
    )


def _reject_parent_cycles(nodes: Mapping[str, GraphNode]) -> None:
    for node_id in nodes:
        seen: set[str] = set()
        current: str | None = node_id
        while current is not None:
            if current in seen:
                raise ValueError("图谱父节点存在循环")
            seen.add(current)
            current = nodes[current].parent_id


def _required_id(value: Any, label: str) -> str:
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError(f"{label}无效")
    return value


def _required_text(value: Any, label: str) -> str:
    if not isinstance(value, str) or not value.strip() or any(ord(char) < 32 for char in value):
        raise ValueError(f"{label}无效")
    return value.strip()


def _string_tuple(value: Any, allowed: set[str], label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if (not isinstance(value, list) or (not value and not allow_empty)
            or any(not isinstance(item, str) or item not in allowed for item in value)
            or len(value) != len(set(value))):
        raise ValueError(f"{label}无效")
    return tuple(value)


def _text_tuple(value: Any, label: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"{label}无效")
    result = tuple(_required_text(item, label) for item in value)
    if len({_normalize_term(item) for item in result}) != len(result):
        raise ValueError(f"{label}重复")
    return result


def _normalize_term(value: str) -> str:
    return " ".join(value.strip().split()).casefold()
