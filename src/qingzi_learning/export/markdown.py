"""Obsidian-friendly Markdown exports for traceable learning records."""

from __future__ import annotations

import hashlib
import html
import json
import os
from pathlib import Path
import re
from typing import Any
from urllib.parse import quote

from qingzi_learning.export.labels import trend_label

from qingzi_learning.export.publication import write_output
from qingzi_learning.storage.paths import KnowledgePaths, SubjectTree
from qingzi_learning.storage.repository import KnowledgeRepository


class MarkdownExporter:
    """Regenerate complete, bounded-path Markdown reading views from SQLite facts."""

    def __init__(self, repo: KnowledgeRepository, paths: KnowledgePaths | None = None) -> None:
        self.repo = repo
        self.paths = paths or KnowledgePaths(repo.config)

    def export_document(self, document_id: str) -> Path:
        """Write one analysis note atomically, rejecting an unsafe persisted identifier."""
        document = self.repo.get_document(document_id)
        if document is None:
            raise ValueError("资料不存在")
        tree = self.paths.ensure_subject_tree(document["subject"])
        destination = self._export_document_record(document, tree)
        # A standalone document export must not leave its knowledge-point link dangling.
        self.export_subject(document["subject"])
        return destination

    def export_subject(self, subject: str) -> Path:
        """Write all subject notes, individual point pages, full error index, and index note."""
        tree = self.paths.ensure_subject_tree(subject)
        snapshot = self.repo.export_subject_snapshot(subject)
        documents = snapshot["documents"]
        for record in documents:
            document = self.repo.get_document(record["document_id"])
            if document is not None:
                self._export_document_record(document, tree)
        point_paths = {
            point["knowledge_point"]: self.knowledge_point_path(subject, point["knowledge_point"])
            for point in snapshot["knowledge_points"]
        }
        for point in snapshot["knowledge_points"]:
            self._export_knowledge_point(subject, point, point_paths[point["knowledge_point"]])
        self._export_error_index(subject, snapshot["error_bank"])
        return self._export_subject_index(subject, snapshot, point_paths)

    def knowledge_point_path(self, subject: str, knowledge_point: str) -> Path:
        """Return a fixed-root, deterministic page path for a knowledge-point name."""
        return self.paths.safe_file_path(subject, "知识点", self._knowledge_filename(knowledge_point))

    def expected_paths(self, subject: str) -> set[Path]:
        """Derive the complete current subject set from facts, never a manifest."""
        snapshot = self.repo.export_subject_snapshot(subject)
        return ({self.document_path(subject, item["document_id"], display_name=item["display_name"]) for item in snapshot["documents"]}
                | {self.knowledge_point_path(subject, item["knowledge_point"]) for item in snapshot["knowledge_points"]}
                | {self.error_index_path(subject), self.paths.safe_file_path(subject, "知识点", "科目总览.md")})

    def document_path(
        self, subject: str, document_id: str, *, display_name: str | None = None
    ) -> Path:
        """Return a human-readable analysis path while still validating the internal id."""
        self.paths.safe_file_path(subject, "分析记录", f"{document_id}.md")
        visible_name = display_name or self.repo.document_display_name(document_id)
        return self.paths.safe_file_path(subject, "分析记录", f"{visible_name}.md")

    def error_index_path(self, subject: str) -> Path:
        return self.paths.safe_file_path(subject, "错题", "错题索引.md")

    def _export_document_record(self, document: dict[str, Any], tree: SubjectTree) -> Path:
        destination = self.document_path(
            document["subject"], document["document_id"], display_name=document["display_name"]
        )
        page_paths = {page["page_number"]: Path(page["path"]) for page in document["pages"]}
        lines = [
            "---",
            f"document_id: {self._yaml_value(document['document_id'])}",
            f"subject: {self._yaml_value(document['subject'])}",
            f"document_type: {self._yaml_value(document['document_type'])}",
            f"grading_mode: {self._yaml_value(document.get('grading_mode') or '')}",
            "---",
            "",
            f"# {self._plain(document['display_name'])} 分析记录",
            "",
            "## 总结",
            "",
            self._block_plain(document.get("summary") or "尚无总结。"),
            "",
            "## 老师批改依据",
            "",
        ]
        evidence = document.get("teacher_mark_evidence", [])
        lines.extend([f"- {self._plain(item)}" for item in evidence] or ["- 尚未识别到老师批改依据。"])
        lines.extend(["", "## 题目分析", ""])
        if not document["questions"]:
            lines.append("尚无题目分析。")
        for question in document["questions"]:
            page = question["page"]
            point_links = [
                self._markdown_link(point, self.knowledge_point_path(document["subject"], point), destination)
                for point in question["knowledge_points"]
            ]
            lines.extend(
                [
                    f"### 题目 {self._plain(question['question_id'])}",
                    "",
                    f"- source_page: {page}",
                    f"- source_image: {self._source_reference(page_paths.get(page), destination, page)}",
                    f"- status: {self._plain(question['status'])}",
                    f"- decision_source: {self._plain(question['decision_source'])}",
                    f"- confidence: {question['confidence']:.2f}",
                    f"- question_type: {self._plain(question['question_type'])}",
                    f"- knowledge_points: {', '.join(point_links) or '尚未标注'}",
                    f"- error_categories: {', '.join(self._plain(item) for item in question['error_categories']) or '无'}",
                    f"- prompt_summary: {self._plain(question['prompt_summary'])}",
                    f"- student_answer: {self._plain(question['student_answer'] or '未识别')}",
                    f"- reference_answer: {self._plain(question['reference_answer'] or '待确认')}",
                    f"- reason: {self._plain(question['reason'] or '尚无说明')}",
                    "",
                ]
            )
            if question.get("review_revision"):
                lines.extend([
                    "#### 家长复核记录", "",
                    f"- 最终判断: {self._plain(question['status'])}（家长确认）",
                    f"- 原始判断: {self._plain(question['original_status'])}；来源: {self._plain(question['original_decision_source'])}；原置信度: {question['confidence']:.2f}",
                    f"- 原参考答案: {self._plain(question['original_reference_answer'])}",
                    f"- 复核备注: {self._plain(question['review_note'] or '无')}",
                    f"- 确认时间: {self._plain(question['confirmed_at'])}；版本: {question['review_revision']}", "",
                ])
                for entry in self.repo.review_history(document['document_id'], question['question_id']):
                    after = json.loads(entry['after_json'])
                    lines.append(f"- 历史复核 {entry['revision']}: {self._plain(after['final_status'])} · {self._plain(after['note'])} · {self._plain(entry['confirmed_at'])}")
                lines.append("")
        self._atomic_write(destination, "\n".join(lines).rstrip() + "\n")
        return destination

    def _export_subject_index(
        self, subject: str, snapshot: dict[str, Any], point_paths: dict[str, Path]
    ) -> Path:
        data = snapshot["subject"]
        destination = self.paths.safe_file_path(subject, "知识点", "科目总览.md")
        lines = [
            "---",
            f"subject: {self._yaml_value(subject)}",
            "view: subject_index",
            "statistics_period: 累计至今",
            f"question_sample_size: {data['question_count']}",
            "---",
            "",
            f"# {self._plain(subject)}知识库",
            "",
            "## 学习概况",
            "",
            f"- 累计资料数: {data['document_count']}（累计至今）",
            f"- 累计题目样本: {data['question_count']}（累计至今）",
            f"- 掌握度: {self._percentage_or_no_data(data['mastery_rate'])}（掌握度样本 {data['mastery_sample_size']}）",
            f"- 本月收录资料: {snapshot['monthly_period']['sample_size']}（{snapshot['monthly_period']['label']}）",
            f"- 最近趋势: {self._trend_text(data['recent_trend'])}",
            "",
            "## 重点短板与知识点地图",
            "",
        ]
        points = snapshot["knowledge_points"]
        if not points:
            lines.append("尚无数据。")
        for point in points:
            link = self._markdown_link(point["knowledge_point"], point_paths[point["knowledge_point"]], destination)
            lines.extend(
                [
                    f"### {link}",
                    "",
                    f"- 掌握度: {self._percentage_or_no_data(point['mastery_rate'])}（样本 {point['exposure_count']}）",
                    f"- 错误题数: {point['incorrect_count']}；部分正确: {point['partial_count']}；待确认: {point['needs_review']}",
                    f"- 累计趋势: {trend_label(point['trend'])}；复习优先级: {point['review_priority']}",
                    "",
                ]
            )
        error_index = self.error_index_path(subject)
        lines.extend(
            [
                "## 错题本",
                "",
                f"- {self._markdown_link('查看完整错题索引', error_index, destination)}（共 {len(snapshot['error_bank'])} 题）",
            ]
        )
        for mistake in snapshot["error_bank"][:5]:
            lines.append(
                f"- 第 {mistake['page']} 页第 {self._plain(mistake['question_id'])} 题：{self._plain(mistake['prompt_summary'])}（{self._plain(mistake['status'])}）"
            )
        lines.extend(["", "## 分析记录", ""])
        if not snapshot["documents"]:
            lines.append("尚无资料。")
        for document in snapshot["documents"]:
            lines.append(
                f"- {self._markdown_link(document['display_name'], self.document_path(subject, document['document_id'], display_name=document['display_name']), destination)} · {self._plain(document['document_type'])}"
            )
        self._atomic_write(destination, "\n".join(lines).rstrip() + "\n")
        return destination

    def _export_error_index(self, subject: str, mistakes: list[dict[str, Any]]) -> Path:
        destination = self.error_index_path(subject)
        lines = [
            "---",
            f"subject: {self._yaml_value(subject)}",
            "view: error_index",
            "statistics_period: 累计至今",
            f"error_sample_size: {len(mistakes)}",
            "---",
            "",
            f"# {self._plain(subject)}错题索引",
            "",
            "每一项均可回到分析记录、原始页码和题号。",
            "",
        ]
        if not mistakes:
            lines.append("尚无数据。")
        for item in mistakes:
            source = self._source_reference(
                Path(item["source_path"]) if item.get("source_path") else None,
                destination,
                item["page"],
            )
            lines.append(
                f"- 第 {item['page']} 页第 {self._plain(item['question_id'])} 题：{self._plain(item['prompt_summary'])}（{self._plain(item['status'])}） · "
                f"{self._markdown_link(item['display_name'], self.document_path(subject, item['document_id'], display_name=item['display_name']), destination)} · 原图 {source}"
            )
        self._atomic_write(destination, "\n".join(lines).rstrip() + "\n")
        return destination

    def _export_knowledge_point(self, subject: str, point: dict[str, Any], destination: Path) -> Path:
        evidence = self.repo.knowledge_point_evidence(subject, point["knowledge_point"])
        lines = [
            "---",
            f"subject: {self._yaml_value(subject)}",
            f"knowledge_point: {self._yaml_value(point['knowledge_point'])}",
            "statistics_period: 累计至今",
            f"question_sample_size: {point['exposure_count']}",
            "---",
            "",
            f"# {self._plain(point['knowledge_point'])}",
            "",
            f"- 掌握度: {self._percentage_or_no_data(point['mastery_rate'])}（样本 {point['exposure_count']}）",
            f"- 正确: {point['correct_count']}；错误: {point['incorrect_count']}；部分正确: {point['partial_count']}；待确认: {point['needs_review']}",
            f"- 累计趋势: {trend_label(point['trend'])}；复习优先级: {point['review_priority']}",
            "",
            "## 关联题目与原题追溯",
            "",
        ]
        if not evidence:
            lines.append("尚无数据。")
        for item in evidence:
            source = self._source_reference(
                Path(item["source_path"]) if item.get("source_path") else None,
                destination,
                item["page"],
            )
            lines.append(
                f"- 第 {item['page']} 页第 {self._plain(item['question_id'])} 题：{self._plain(item['prompt_summary'])}（{self._plain(item['status'])}，{self._plain(item['decision_source'])}） · "
                f"{self._markdown_link(item['display_name'], self.document_path(subject, item['document_id'], display_name=item['display_name']), destination)} · 原图 {source}"
            )
        self._atomic_write(destination, "\n".join(lines).rstrip() + "\n")
        return destination

    @staticmethod
    def _yaml_value(value: Any) -> str:
        return json.dumps(str(value), ensure_ascii=False)

    @staticmethod
    def _knowledge_filename(knowledge_point: str) -> str:
        digest = hashlib.sha256(str(knowledge_point).encode("utf-8")).hexdigest()[:12]
        return f"知识点-{digest}.md"

    def _source_reference(self, page: Path | None, note_path: Path, page_number: int) -> str:
        if page is None:
            return f"page_{page_number:03d}.jpg（未找到原图路径）"
        filename = page.name or f"page_{page_number:03d}.jpg"
        try:
            page.resolve().relative_to(self.paths.knowledge_root)
        except ValueError:
            return f"{self._plain(filename)}（原图标识；位于待归档资料，需通过桌面程序查看）"
        return self._markdown_link(filename, page.resolve(), note_path)

    def _markdown_link(self, label: str, target: Path, source: Path) -> str:
        return f"[{self._plain(label)}]({self._relative_url(target, source.parent)})"

    @staticmethod
    def _plain(value: Any) -> str:
        text = re.sub(r"\s*[\r\n]+\s*", " ", str(value)).strip()
        return MarkdownExporter._escape_characters(text)

    @staticmethod
    def _block_plain(value: Any) -> str:
        """Keep prose line breaks but make every untrusted line non-structural Markdown."""
        lines = str(value).replace("\r\n", "\n").replace("\r", "\n").split("\n")
        return "\n".join(MarkdownExporter._escape_characters(line) for line in lines)

    @staticmethod
    def _escape_characters(text: str) -> str:
        text = html.escape(text, quote=False).replace("\\", "\\\\")
        for character in "`*_{}[]()#+!|~-":
            text = text.replace(character, f"\\{character}")
        return text

    @staticmethod
    def _relative_url(target: Path, source_directory: Path) -> str:
        relative = os.path.relpath(target.resolve(), source_directory.resolve()).replace("\\", "/")
        return "/".join(
            segment if segment in (".", "..") else quote(segment, safe="-._~")
            for segment in relative.split("/")
        )

    @staticmethod
    def _percentage_or_no_data(value: float | None) -> str:
        return "尚无数据" if value is None else f"{value * 100:.1f}%"

    @staticmethod
    def _trend_text(trend: dict[str, Any]) -> str:
        prefix = (
            "样本不足，暂不判断趋势"
            if trend["status"] == "insufficient_data"
            else f"{trend_label(trend['status'])}（掌握度 {trend['mastery_rate'] * 100:.1f}%）"
        )
        return (
            f"{prefix}（{trend['period_label']}；资料 {trend['document_sample_size']}，"
            f"题目样本 {trend['question_sample_size']}）"
        )

    def _atomic_write(self, destination: Path, content: str) -> None:
        write_output(destination, content, self.paths.knowledge_root)
