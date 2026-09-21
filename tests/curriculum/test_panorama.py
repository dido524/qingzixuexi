from qingzi_learning.curriculum.graph import load_math_graph
from qingzi_learning.curriculum.mastery import build_mastery_projection, load_course_mappings
from qingzi_learning.curriculum.panorama import render_panorama_page
from qingzi_learning.curriculum.view_model import build_graph_view_model


def _model():
    graph = load_math_graph()
    projection = build_mastery_projection(graph, {"knowledge_points": []})
    return build_graph_view_model(graph, projection, load_course_mappings())


def test_panorama_is_not_only_a_tree():
    page = render_panorama_page(_model())
    assert 'data-edge-kind="contains"' in page
    assert 'data-edge-kind="applies_to"' in page
    assert 'data-edge-kind="prerequisite"' in page
    assert "显示重要联系" in page and "显示全部联系" in page
    assert "分数的意义与性质" in page and "比" in page


def test_panorama_has_child_view_controls_and_print_contract():
    page = render_panorama_page(_model())
    for marker in ("全部小学", "当前年级", "本学期", "恢复全貌", "全屏", "打印"):
        assert marker in page
    for detail_heading in ("我在哪里学过", "和什么有关", "最近掌握情况"):
        assert detail_heading in page
    assert "@media print" in page and "prefers-reduced-motion" in page
    assert "localStorage" not in page and "sessionStorage" not in page


def test_panorama_renders_four_domain_zones_and_accessible_nodes():
    page = render_panorama_page(_model())
    for domain in ("domain-number", "domain-geometry", "domain-statistics", "domain-practice"):
        assert f'data-domain="{domain}"' in page
    assert 'role="button"' in page
    assert 'tabindex="0"' in page
    assert "#E8F1FF" in page and "#EEE9FF" in page
    assert "#E7F7F1" in page and "#FFF1E8" in page
