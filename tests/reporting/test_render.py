from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from qingzi_learning.config import AppConfig
from qingzi_learning.storage.paths import KnowledgePaths


def test_child_report_is_printable_and_contains_no_parent_detail(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    text = renderer.render_child(_profile(), _narrative())

    assert '@media print' in text
    assert 'window.print()' in text
    assert 'URLSearchParams' in text
    assert "初步观察" in text
    assert "下一步的小目标" in text
    assert "家长复核明细" not in text
    assert "decision_source" not in text


def test_parent_report_shows_samples_and_traceable_document_link(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    text = renderer.render_parent(_profile(), _narrative())

    assert "家长版学情报告" in text
    assert "样本 2" in text
    assert "doc-one" in text
    assert "../../../../%E6%95%B0%E5%AD%A6/%E5%88%86%E6%9E%90%E8%AE%B0%E5%BD%95/doc-one.md" in text
    assert "题号 1" in text
    assert "<strong>1</strong><span>待确认</span>" in text


def test_parent_report_keeps_rows_together_and_repeats_headers_when_printed(
    tmp_path: Path,
) -> None:
    renderer = _renderer(tmp_path)

    text = renderer.render_parent(_profile(), _narrative())

    assert "thead{display:table-header-group}" in text
    assert "tr{break-inside:avoid-page!important;page-break-inside:avoid!important}" in text
    assert "thead tr,tbody tr{display:grid;grid-template-columns:12% 14% 7% 7% 19% 41%" in text


def test_parent_report_chunks_long_knowledge_tables_for_printing(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    profile = _profile()
    original = next(iter(profile["subjects"]["数学"]["knowledge_points"].values()))
    profile["subjects"]["数学"]["knowledge_points"] = {
        f"知识点{i}": {**deepcopy(original), "knowledge_point": f"知识点{i}"}
        for i in range(5)
    }

    text = renderer.render_parent(profile, _narrative())

    assert text.count('<table class="knowledge-table">') == 2
    assert text.count("<th>知识点</th>") == 2
    assert "table{break-inside:avoid-page!important;page-break-inside:avoid!important}" in text


def test_report_renderer_escapes_model_and_knowledge_text(tmp_path: Path) -> None:
    renderer = _renderer(tmp_path)
    profile = _profile()
    point = profile["subjects"]["数学"]["knowledge_points"].pop("分数应用")
    point["knowledge_point"] = "分数</script><script>bad()"
    profile["subjects"]["数学"]["knowledge_points"][point["knowledge_point"]] = point
    narrative = _narrative()
    narrative["child_headline"] = "稳步<script>bad()"

    text = renderer.render_child(profile, narrative)

    assert "<script>bad()" not in text
    assert "&lt;script&gt;bad()" in text


def _renderer(tmp_path: Path) -> ReportRenderer:
    from qingzi_learning.reporting.render import ReportRenderer

    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "app-data",
    )
    paths = KnowledgePaths(config)
    report_directory = paths.report_directory(2026, 9, "report-test")
    return ReportRenderer(paths, report_directory)


def _profile() -> dict:
    empty = {
        "question_count": 0,
        "document_count": 0,
        "study_day_count": 0,
        "evidence_level": "no_data",
        "correct_count": 0,
        "incorrect_count": 0,
        "partial_count": 0,
        "knowledge_points": {},
    }
    math = {
        **empty,
        "question_count": 2,
        "document_count": 1,
        "study_day_count": 1,
        "evidence_level": "initial",
        "correct_count": 1,
        "incorrect_count": 1,
        "knowledge_points": {
            "分数应用": {
                "evidence_id": "kp-math",
                "knowledge_point": "分数应用",
                "exposure_count": 2,
                "correct_count": 1,
                "incorrect_count": 1,
                "partial_count": 0,
                "mastery_rate": 0.5,
                "review_priority": 55,
                "trend": "improving",
                "last_seen_at": "2026-09-18 10:00:00",
                "error_categories": [{"name": "审题", "count": 1}],
                "representative_questions": [{
                    "document_id": "doc-one",
                    "question_id": "1",
                    "prompt_summary": "分数应用题",
                    "status": "incorrect",
                }],
                "delta": {
                    "exposure_count": 1,
                    "correct_count": 1,
                    "incorrect_count": 0,
                    "partial_count": 0,
                },
                "change": "improved",
            }
        },
    }
    return {
        "schema_version": 1,
        "cutoff_at": "2026-09-18T00:00:00Z",
        "summary": {
            "question_count": 2,
            "document_count": 1,
            "pending_count": 1,
            "excluded_low_confidence_count": 0,
        },
        "delta": {"question_count": 1, "new_document_count": 1, "pending_count": 1},
        "subjects": {"语文": empty, "数学": math, "英语": empty},
    }


def _narrative() -> dict:
    item = {"text": "分数应用值得继续练习。", "evidence_ids": ["kp-math"]}
    return {
        "source": "local_template",
        "child_headline": "把已经会的内容练得更稳",
        "child_progress": [item],
        "child_focus": [item],
        "child_goals": [item],
        "child_closing": "按自己的节奏继续练习。",
        "parent_summary": "当前仍需要后续样本验证。",
        "parent_observations": [item],
        "parent_recommendations": [item],
    }
