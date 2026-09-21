import html
import json
from pathlib import Path
import re

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.curriculum.graph_exporter import MathKnowledgeGraphExporter
from qingzi_learning.storage.repository import KnowledgeRepository
from scripts.verify_math_knowledge_graph import verify_graph_pages


@pytest.fixture
def repo(tmp_path: Path):
    repository = KnowledgeRepository(
        AppConfig(
            knowledge_root=tmp_path / "knowledge",
            subjects=("语文", "数学", "英语"),
            camera_vid=1,
            camera_pid=2,
            spool_root=tmp_path / "spool",
            app_data_root=tmp_path / "app-data",
        )
    )
    yield repository
    repository.close()


def _payload(page):
    match = re.search(
        r'<script type="application/json" id="graph-data">(.*?)</script>',
        page,
        re.S,
    )
    assert match
    return json.loads(html.unescape(match.group(1)))


def test_exporter_publishes_two_pages_from_one_payload(repo):
    exporter = MathKnowledgeGraphExporter(repo)
    panorama, explorer = exporter.export()
    assert panorama.name == "数学知识全景脑图.html"
    assert explorer.name == "数学掌握知识图谱.html"
    assert _payload(panorama.read_text("utf-8")) == _payload(explorer.read_text("utf-8"))
    assert exporter.expected_paths() <= set(panorama.parent.iterdir())
    assert all(path.is_file() for path in exporter.expected_paths())


def test_exported_pages_pass_offline_release_verifier(repo):
    exporter = MathKnowledgeGraphExporter(repo)
    panorama, explorer = exporter.export()

    result = verify_graph_pages(repo.config.knowledge_root, (panorama, explorer))

    assert result.ok, result
    assert result.node_count >= 50
    assert result.important_cross_link_count >= 1
    assert result.broken_links == ()
    assert result.external_assets == ()


def test_obsidian_notes_link_relations_sources_and_preserve_parent_edits(repo):
    exporter = MathKnowledgeGraphExporter(repo)
    exporter.export()
    parent = exporter.parent_note_paths()
    general = next(path for path in parent if path.name == "我的知识图谱笔记.md")
    concept = next(path for path in parent if path.name == "我的-number-decimal-multiply.md")
    general.write_text(general.read_text("utf-8") + "\n家长观察：计算时要检查小数点。\n", "utf-8")
    concept.write_text(concept.read_text("utf-8") + "\n晴子的心得。\n", "utf-8")

    exporter.export()

    assert "家长观察" in general.read_text("utf-8")
    assert "晴子的心得" in concept.read_text("utf-8")
    system = panorama_note = exporter.output_dir / "系统-number-decimal-multiply.md"
    text = system.read_text("utf-8")
    assert "数学知识全景脑图.html" in text
    assert "北师大" in text
    assert "样本" in text
    assert "我的-number-decimal-multiply.md" in text
