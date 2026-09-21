from copy import deepcopy
import json
from pathlib import Path

import pytest

from qingzi_learning.curriculum.graph import load_math_graph, validate_math_graph


GRAPH_PATH = (
    Path(__file__).parents[2]
    / "src"
    / "qingzi_learning"
    / "curriculum"
    / "graphs"
    / "primary_math_v1.json"
)


def test_primary_math_graph_has_official_spine_and_cross_links():
    graph = load_math_graph()
    assert [graph.nodes[node_id].label for node_id in graph.root_ids] == [
        "数与代数",
        "图形与几何",
        "统计与概率",
        "综合与实践",
    ]
    assert {"prerequisite", "deepens_to", "applies_to", "confusable"} <= {
        edge.kind for edge in graph.edges
    }
    assert any(
        edge.source == "number-fractions" and edge.target == "relation-ratio"
        for edge in graph.edges
    )


def _duplicate_alias(data):
    data["nodes"][0]["aliases"] = ["重复别名"]
    data["nodes"][1]["aliases"] = ["重复别名"]


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data["nodes"].append(deepcopy(data["nodes"][0])),
        lambda data: data["edges"].append(
            {
                "source": "missing",
                "target": "number-fractions",
                "kind": "prerequisite",
                "basis": "official",
                "importance": 1,
            }
        ),
        lambda data: data["nodes"][0].update(parent_id="missing"),
        _duplicate_alias,
    ],
)
def test_graph_rejects_duplicate_or_dangling_content(mutation):
    data = json.loads(GRAPH_PATH.read_text("utf-8"))
    mutation(data)
    with pytest.raises(ValueError):
        validate_math_graph(data)


def test_graph_rejects_parent_cycles_and_official_nodes_below_extension():
    data = json.loads(GRAPH_PATH.read_text("utf-8"))
    by_id = {node["node_id"]: node for node in data["nodes"]}
    by_id["domain-number"]["parent_id"] = "theme-number-operations"
    with pytest.raises(ValueError, match="循环"):
        validate_math_graph(data)

    data = json.loads(GRAPH_PATH.read_text("utf-8"))
    by_id = {node["node_id"]: node for node in data["nodes"]}
    by_id["theme-number-operations"]["parent_id"] = "extension-number-theory"
    with pytest.raises(ValueError, match="非课标"):
        validate_math_graph(data)


def test_graph_rejects_unknown_or_cross_domain_parentage():
    data = json.loads(GRAPH_PATH.read_text("utf-8"))
    by_id = {node["node_id"]: node for node in data["nodes"]}
    by_id["number-decimals"]["domain"] = "domain-typo"
    with pytest.raises(ValueError, match="领域"):
        validate_math_graph(data)

    data = json.loads(GRAPH_PATH.read_text("utf-8"))
    by_id = {node["node_id"]: node for node in data["nodes"]}
    by_id["number-decimals"]["parent_id"] = "theme-shapes-measure"
    with pytest.raises(ValueError, match="领域"):
        validate_math_graph(data)
