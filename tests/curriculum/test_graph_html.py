import json
import re

from qingzi_learning.curriculum.graph import load_math_graph
from qingzi_learning.curriculum.html_shell import render_graph_shell, safe_embedded_json
from qingzi_learning.curriculum.mastery import build_mastery_projection, load_course_mappings
from qingzi_learning.curriculum.view_model import build_graph_view_model


def _model():
    graph = load_math_graph()
    projection = build_mastery_projection(
        graph,
        {
            "knowledge_points": [
                {
                    "knowledge_point": "小数乘法",
                    "exposure_count": 6,
                    "correct_count": 4,
                    "partial_count": 1,
                    "incorrect_count": 1,
                    "last_seen_at": "2026-09-20 12:00:00",
                    "trend": "no_data",
                }
            ]
        },
    )
    return build_graph_view_model(graph, projection, load_course_mappings())


def test_shared_view_model_carries_nodes_edges_sources_and_audit():
    model = _model()
    assert {"nodes", "edges", "sources", "audit", "legend", "filters"} <= model.keys()
    decimal = model["nodes"]["number-decimal-multiply"]
    assert decimal["mastery"]["evidence_count"] == 6
    assert decimal["sources"][0]["source_label"] == "小数乘法"
    assert decimal["sources"][0]["grades"] == ["5"]
    assert decimal["sources"][0]["terms"] == ["upper"]
    application = model["nodes"]["relation-application-model"]
    assert all(source["status"] == "confirmed" for source in application["sources"])
    assert any(item["source_label"] == "鸡兔同笼" for item in model["sourceAudit"])
    assert model["filters"]["grades"] == ["1", "2", "3", "4", "5", "6"]


def test_shell_escapes_markup_and_script_terminators():
    page = render_graph_shell(
        title='<img src=x onerror="alert(1)">',
        mode="panorama",
        payload={"label": "</script><script>alert(1)</script>\u2028&"},
        body="",
        styles="",
        script="",
    )
    assert '<img src=x onerror="alert(1)">' not in page
    assert "</script><script>alert(1)</script>" not in page
    assert "&lt;img" in page
    assert "\\u003c/script\\u003e" in page
    assert "\\u2028" in page


def test_embedded_payload_round_trips_without_external_dependencies():
    model = _model()
    encoded = safe_embedded_json(model)
    assert json.loads(encoded) == model
    page = render_graph_shell(
        title="小学数学知识图谱",
        mode="explorer",
        payload=model,
        body='<div id="graph-canvas"></div>',
        styles="",
        script="",
    )
    assert not re.search(r"<(?:script|link)[^>]+(?:src|href)=[\"']https?://", page, re.I)
    assert 'href="#graph-main"' in page
    assert 'role="toolbar"' in page
    assert 'id="graph-legend"' in page
    assert 'id="graph-details"' in page
    assert 'aria-live="polite"' in page
    assert "prefers-reduced-motion" in page
    assert "@media print" in page
