"""Single serialization model shared by every mathematics graph view."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any, Iterable

from qingzi_learning.curriculum.graph import MathGraph, SourceMapping
from qingzi_learning.curriculum.mastery import MasteryProjection


def build_graph_view_model(
    graph: MathGraph,
    projection: MasteryProjection,
    mappings: Iterable[SourceMapping],
) -> dict[str, Any]:
    sources = tuple(mappings)
    sources_by_target: dict[str, list[dict[str, Any]]] = {node_id: [] for node_id in graph.nodes}
    source_rows: list[dict[str, Any]] = []
    for mapping in sources:
        row = {
            "source_id": mapping.source_id,
            "source_label": mapping.source_label,
            "source_type": mapping.source_type,
            "target_ids": list(mapping.target_ids),
            "relation": mapping.relation,
            "status": mapping.status,
            "basis": mapping.basis,
        }
        source_rows.append(row)
        for target_id in mapping.target_ids:
            sources_by_target.setdefault(target_id, []).append(row)

    nodes: dict[str, dict[str, Any]] = {}
    for node_id, node in graph.nodes.items():
        mastery = projection.by_concept[node_id]
        nodes[node_id] = {
            "id": node.node_id,
            "label": node.label,
            "kind": node.kind,
            "domain": node.domain,
            "parentId": node.parent_id,
            "stages": list(node.stages),
            "grades": list(node.grades),
            "competencies": list(node.competencies),
            "official": node.official,
            "mastery": asdict(mastery),
            "sources": sorted(
                sources_by_target.get(node_id, []),
                key=lambda item: (item["source_type"], item["source_id"]),
            ),
        }

    return {
        "graphId": graph.graph_id,
        "version": graph.version,
        "rootIds": list(graph.root_ids),
        "nodes": nodes,
        "edges": [asdict(edge) for edge in graph.edges],
        "sources": source_rows,
        "audit": [
            {
                "label": item.label,
                "status": item.status,
                "concept_ids": list(item.concept_ids),
                "evidence_count": item.evidence_count,
            }
            for item in projection.audit
        ],
        "legend": {
            "mastery": [
                {"id": "stable", "label": "稳定掌握", "symbol": "✓"},
                {"id": "basic", "label": "基本掌握", "symbol": "●"},
                {"id": "unstable", "label": "掌握不稳", "symbol": "!"},
                {"id": "weak", "label": "明显短板", "symbol": "×"},
                {"id": "evidence_insufficient", "label": "证据不足", "symbol": "?"},
                {"id": "no_data", "label": "尚无数据", "symbol": "—"},
            ],
            "domains": [
                {"id": "domain-number", "label": "数与代数"},
                {"id": "domain-geometry", "label": "图形与几何"},
                {"id": "domain-statistics", "label": "统计与概率"},
                {"id": "domain-practice", "label": "综合与实践"},
                {"id": "extension", "label": "拓展数学"},
            ],
        },
        "filters": {
            "stages": ["第一学段", "第二学段", "第三学段", "拓展"],
            "grades": ["1", "2", "3", "4", "5", "6"],
            "terms": ["upper", "lower"],
            "sourceTypes": ["textbook", "calculation_training", "tutoring", "assessment"],
            "masteryStates": [
                "stable", "basic", "unstable", "weak", "evidence_insufficient", "no_data",
            ],
            "competencies": [
                "数感", "量感", "符号意识", "运算能力", "几何直观", "空间观念",
                "推理意识", "数据意识", "模型意识", "应用意识", "创新意识",
            ],
        },
    }
