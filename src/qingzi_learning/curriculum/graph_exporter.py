"""Publish both mathematics graph views and ordinary Obsidian notes."""

from __future__ import annotations

import html
import os
from pathlib import Path
from urllib.parse import quote

from qingzi_learning.curriculum.explorer import render_explorer_page
from qingzi_learning.curriculum.graph import load_math_graph
from qingzi_learning.curriculum.mastery import build_mastery_projection, load_course_mappings
from qingzi_learning.curriculum.panorama import render_panorama_page
from qingzi_learning.curriculum.view_model import build_graph_view_model
from qingzi_learning.export.publication import write_output
from qingzi_learning.export.markdown import MarkdownExporter
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import KnowledgeRepository


_RELATION_LABELS = {
    "prerequisite": "前置于", "deepens_to": "深化为",
    "applies_to": "应用于", "confusable": "易混淆",
}
_STATE_LABELS = {
    "stable": "稳定掌握", "basic": "基本掌握", "unstable": "掌握不稳",
    "weak": "明显短板", "evidence_insufficient": "证据不足", "no_data": "尚无数据",
}


class MathKnowledgeGraphExporter:
    """Generate one canonical math graph in child and parent reading modes."""

    def __init__(self, repo: KnowledgeRepository, paths: KnowledgePaths | None = None) -> None:
        if "数学" not in repo.config.subjects:
            raise ValueError("知识库未配置数学科目")
        self.repo = repo
        self.paths = paths or KnowledgePaths(repo.config)
        self.graph = load_math_graph()
        self.mappings = load_course_mappings()

    @property
    def output_dir(self) -> Path:
        return self.paths.knowledge_graph_file("数学", self.graph.graph_id, "系统知识图谱总览.md").parent

    def _file(self, name: str) -> Path:
        return self.paths.knowledge_graph_file("数学", self.graph.graph_id, name)

    def panorama_path(self) -> Path:
        return self._file("数学知识全景脑图.html")

    def explorer_path(self) -> Path:
        return self._file("数学掌握知识图谱.html")

    def index_path(self) -> Path:
        return self._file("系统知识图谱总览.md")

    def expected_paths(self) -> set[Path]:
        return {
            self.panorama_path(), self.explorer_path(), self.index_path(),
            *(self._file(f"系统-{node_id}.md") for node_id in self.graph.nodes),
        }

    def parent_note_paths(self) -> set[Path]:
        return {
            self._file("我的知识图谱笔记.md"),
            *(self._file(f"我的-{node_id}.md") for node_id in self.graph.nodes),
        }

    def export(self) -> tuple[Path, Path]:
        projection = build_mastery_projection(
            self.graph,
            self.repo.export_subject_snapshot("数学"),
        )
        model = build_graph_view_model(self.graph, projection, self.mappings)
        self._attach_evidence(model, projection)
        self._create_parent_notes()
        write_output(self.panorama_path(), render_panorama_page(model), self.paths.knowledge_root)
        write_output(self.explorer_path(), render_explorer_page(model), self.paths.knowledge_root)
        self._write_index(model)
        self._write_nodes(model)
        return self.panorama_path(), self.explorer_path()

    def _attach_evidence(self, model: dict, projection) -> None:
        markdown = MarkdownExporter(self.repo, self.paths)
        seen_by_concept: dict[str, set[tuple[str, str, int]]] = {}
        for item in projection.audit:
            if item.status != "confirmed" or not item.concept_ids:
                continue
            concept_id = item.concept_ids[0]
            evidence = model["nodes"][concept_id]["mastery"].setdefault("evidence", [])
            seen = seen_by_concept.setdefault(concept_id, set())
            for row in self.repo.confirmed_knowledge_point_evidence("数学", item.label):
                document_id = str(row.get("document_id", ""))
                question_id = str(row.get("question_id", ""))
                page = row.get("page")
                if not document_id or not question_id or type(page) is not int:
                    continue
                key = (document_id, question_id, page)
                if key in seen or len(evidence) >= 50:
                    continue
                seen.add(key)
                destination = markdown.document_path("数学", document_id)
                relative = os.path.relpath(destination, self.output_dir).replace("\\", "/")
                href = "/".join(
                    segment if segment in {".", ".."} else quote(segment, safe="-._~")
                    for segment in relative.split("/")
                )
                evidence.append({
                    "label": f"资料 {document_id} · 第 {page} 页 · 第 {question_id} 题",
                    "href": href,
                })

    def _create_parent_notes(self) -> None:
        self._create_once(
            self._file("我的知识图谱笔记.md"),
            "# 我的数学知识图谱笔记\n\n在这里记录长期观察、课程安排和复习想法。\n",
        )
        for node in self.graph.nodes.values():
            self._create_once(
                self._file(f"我的-{node.node_id}.md"),
                f"# 我的笔记：{_md(node.label)}\n\n在这里记录孩子对这个知识点的理解和练习心得。\n",
            )

    @staticmethod
    def _create_once(path: Path, content: str) -> None:
        try:
            with path.open("x", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
        except FileExistsError:
            pass

    def _write_index(self, model: dict) -> None:
        lines = [
            "# 晴子数学知识图谱", "",
            "同一份规范化数学知识底座的两个视图：", "",
            "- [数学知识全景脑图](数学知识全景脑图.html)：帮助孩子反复观看知识之间的联系。",
            "- [数学掌握知识图谱](数学掌握知识图谱.html)：按掌握状态、年级和来源筛选。",
            "- [我的知识图谱笔记](我的知识图谱笔记.md)：家长和孩子共同维护，系统不会覆盖。",
            "", "## 四大领域", "",
        ]
        for root_id in self.graph.root_ids:
            node = self.graph.nodes[root_id]
            lines.append(f"- [{_md(node.label)}](系统-{node.node_id}.md)")
        lines += ["", "## 数据说明", "",
                  "只有已确认的知识点映射参与掌握度；未匹配和疑似跨学科标签只在交互图谱的待整理区显示。", ""]
        write_output(self.index_path(), "\n".join(lines), self.paths.knowledge_root)

    def _write_nodes(self, model: dict) -> None:
        for node_id, item in model["nodes"].items():
            relations = []
            for edge in model["edges"]:
                if node_id not in (edge["source"], edge["target"]):
                    continue
                other = edge["target"] if edge["source"] == node_id else edge["source"]
                relations.append(
                    f"- [{_md(model['nodes'][other]['label'])}](系统-{other}.md)：{_RELATION_LABELS[edge['kind']]}"
                )
            sources = [
                f"- {_md(('北师大版五年级上册 · ' if source['source_id'].startswith('bnu-g5u-') else '') + source['source_label'])} · "
                f"{'已确认' if source['status'] == 'confirmed' else '待确认'}"
                for source in item["sources"]
            ]
            mastery = item["mastery"]
            rate = "暂无" if mastery["weighted_rate"] is None else f"{round(mastery['weighted_rate'] * 100)}%"
            lines = [
                f"# {_md(item['label'])}", "",
                f"所属领域：{_md(self.graph.nodes[item['domain']].label) if item['domain'] in self.graph.nodes else '拓展数学'}",
                f"掌握状态：{_STATE_LABELS[mastery['state']]} · 加权正确率 {rate} · 样本 {mastery['evidence_count']}",
                "",
                "[打开全景脑图](数学知识全景脑图.html) · [打开掌握图谱](数学掌握知识图谱.html) · "
                f"[我的笔记](我的-{node_id}.md) · [返回总览](系统知识图谱总览.md)",
                "", "## 知识联系", "", *(relations or ["暂无已核实横向联系。"]),
                "", "## 教材与课程来源", "", *(sources or ["暂无已确认来源。"]), "",
            ]
            write_output(self._file(f"系统-{node_id}.md"), "\n".join(lines), self.paths.knowledge_root)


def _md(value: str) -> str:
    return html.escape(value, quote=False).replace("\\", "\\\\").replace("[", "\\[").replace("]", "\\]")
