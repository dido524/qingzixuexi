import html
import json
import re

from qingzi_learning.curriculum.explorer import render_explorer_page
from qingzi_learning.curriculum.graph import load_math_graph
from qingzi_learning.curriculum.mastery import build_mastery_projection, load_course_mappings
from qingzi_learning.curriculum.view_model import build_graph_view_model


def _model(labels=()):
    graph = load_math_graph()
    points = [
        {
            "knowledge_point": label,
            "exposure_count": 7,
            "correct_count": 2,
            "partial_count": 1,
            "incorrect_count": 4,
            "last_seen_at": "2026-09-20 12:00:00",
            "trend": "declining",
        }
        for label in labels
    ]
    projection = build_mastery_projection(graph, {"knowledge_points": points})
    return build_graph_view_model(graph, projection, load_course_mappings())


def _payload(page):
    match = re.search(r'<script type="application/json" id="graph-data">(.*?)</script>', page, re.S)
    assert match
    return json.loads(html.unescape(match.group(1)))


def test_explorer_has_required_filters_and_shortcoming_mode():
    page = render_explorer_page(_model(["小数乘法"]))
    for marker in ("学段", "年级", "学期", "教材与课程", "掌握状态", "核心素养", "短板模式"):
        assert marker in page
    assert "前置知识路径" in page and "确认题目" in page
    assert "学校教材" in page and "计算训练" in page and "补习班" in page


def test_audit_items_stay_out_of_graph_nodes():
    page = render_explorer_page(_model(["情态动词can"]))
    payload = _payload(page)
    assert "情态动词can" not in {node["label"] for node in payload["nodes"].values()}
    assert any(
        item["label"] == "情态动词can" and item["status"] == "cross_subject"
        for item in payload["audit"]
    )
    assert "待整理映射" in page


def test_explorer_renders_hierarchy_cross_links_and_print_appendix():
    page = render_explorer_page(_model(["小数乘法"]))
    assert 'data-edge-kind="contains"' in page
    assert 'data-edge-kind="applies_to"' in page
    assert 'data-state="weak"' in page
    assert 'id="weak-appendix"' in page
    assert "当前筛选的薄弱知识点" in page
    assert "@media print" in page
    assert "min-height:560px" in page
