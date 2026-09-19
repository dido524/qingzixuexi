from copy import deepcopy
from html.parser import HTMLParser
from pathlib import Path
import re
from urllib.parse import unquote

from qingzi_learning.config import AppConfig
from qingzi_learning.curriculum.catalog import load_catalog
from qingzi_learning.curriculum.exporter import CurriculumExporter
from qingzi_learning.storage.paths import KnowledgePaths


def paths(tmp_path: Path) -> KnowledgePaths:
    return KnowledgePaths(AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2, tmp_path / "spool", tmp_path / "app"))


def test_export_has_real_links_and_is_repeatable(tmp_path):
    exporter = CurriculumExporter(paths(tmp_path))
    entry = exporter.export()
    expected = exporter.expected_paths()
    assert entry in expected
    assert len(expected) == 14
    assert all(path.is_file() for path in expected)
    html = entry.read_text("utf-8")
    assert "教材目录确认" in html
    assert "教学整理建议" in html
    assert "用字母表示（一）" in html
    assert "学校课程" in html
    assert "兴趣班" in html
    class Links(HTMLParser):
        def __init__(self):
            super().__init__()
            self.hrefs = []

        def handle_starttag(self, _tag, attrs):
            self.hrefs.extend(value for key, value in attrs if key == "href")

    links = Links()
    links.feed(html)
    assert links.hrefs
    assert all((entry.parent / unquote(href)).is_file() for href in links.hrefs)
    for note in (path for path in expected if path.suffix == ".md"):
        targets = re.findall(r"\]\(([^)]+)\)", note.read_text("utf-8"))
        assert all((note.parent / unquote(target)).is_file() for target in targets)
    contents = {path: path.read_bytes() for path in expected}
    exporter.export()
    assert contents == {path: path.read_bytes() for path in expected}


def test_html_escapes_untrusted_catalog_text_and_other_track(tmp_path):
    catalog = deepcopy(load_catalog())
    catalog.update(catalog_id="club-math-g6-v1", track="enrichment", grade="6", title="兴趣班<unsafe>")
    catalog["nodes"][0]["title"] = "<script>alert(1)</script>"
    exporter = CurriculumExporter(paths(tmp_path), catalog)
    html = exporter.export().read_text("utf-8")
    assert "&lt;script&gt;" in html and "<script>alert(1)</script>" not in html
    assert "&lt;unsafe&gt;" in html
    assert "club-math-g6-v1" in str(exporter.expected_paths())
    assert "<script>" not in exporter._file("系统课程总览.md").read_text("utf-8")


def test_new_course_uses_its_own_nodes_without_inheriting_school_contents(tmp_path):
    catalog = deepcopy(load_catalog())
    catalog.update(catalog_id="club-math-junior-v1", track="enrichment", stage="初中", grade="7")
    catalog["nodes"] = [
        {"node_id": "c01", "order": 1, "type": "unit", "title": "数与式", "page": 1},
        {"node_id": "c02", "order": 2, "type": "unit", "title": "方程", "page": 20},
    ]
    catalog["edges"] = [{"from": "c01", "to": "c02", "type": "related", "basis": "suggestion"}]
    exporter = CurriculumExporter(paths(tmp_path), catalog)
    page = exporter.export().read_text("utf-8")
    assert "数与式" in page and "方程" in page
    assert "小数乘法" not in page
    assert len(exporter.expected_paths()) == 4


def test_parent_edits_to_obsidian_notes_survive_regeneration(tmp_path):
    knowledge_paths = paths(tmp_path)
    exporter = CurriculumExporter(knowledge_paths)
    exporter.export()
    parent_note = exporter._file("我的-u01.md")
    parent_note.write_text(parent_note.read_text("utf-8") + "\n我的练习体会：还要检查小数点。\n", "utf-8")
    legacy_note = exporter._file("u01.md")
    legacy_note.write_text("旧笔记里的家长备注", "utf-8")
    legacy_index = exporter._file("课程总览.md")
    legacy_index.write_text("旧课程总览里的家长备注", "utf-8")

    updated = deepcopy(load_catalog())
    updated["nodes"][1]["title"] = "三角形的再认识（更新）"
    CurriculumExporter(knowledge_paths, updated).export()

    assert "我的练习体会：还要检查小数点。" in parent_note.read_text("utf-8")
    assert legacy_note.read_text("utf-8") == "旧笔记里的家长备注"
    assert legacy_index.read_text("utf-8") == "旧课程总览里的家长备注"
    system_index = exporter._file("系统课程总览.md").read_text("utf-8")
    assert "三角形的再认识（更新）" in system_index
    assert "课程总览.md" in system_index
    system_note = exporter._file("系统-u01.md").read_text("utf-8")
    assert "我的-u01.md" in system_note and "u01.md" in system_note
