"""A self-contained, offline HTML overview for the local knowledge base."""

from __future__ import annotations

import html
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qingzi_learning.export.labels import trend_label
from qingzi_learning.export.markdown import MarkdownExporter
from qingzi_learning.export.publication import write_output
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import KnowledgeRepository


class DashboardExporter:
    """Write a local dashboard whose links point only to generated/root-contained material."""

    def __init__(self, repo: KnowledgeRepository, paths: KnowledgePaths | None = None,
                 curriculum_catalogs: list[dict[str, Any]] | None = None) -> None:
        self.repo = repo
        self.paths = paths or KnowledgePaths(repo.config)
        self.markdown = MarkdownExporter(repo, self.paths)
        from qingzi_learning.curriculum.catalog import load_catalogs
        from qingzi_learning.curriculum.exporter import CurriculumExporter
        self.curricula = [CurriculumExporter(self.paths, catalog)
                          for catalog in (curriculum_catalogs if curriculum_catalogs is not None else load_catalogs())
                          if catalog["subject"] in repo.config.subjects]

    def export(self) -> Path:
        # Create every linked first-version Markdown destination before publishing HTML links.
        for subject in self.repo.config.subjects:
            self.markdown.export_subject(subject)
        for curriculum in self.curricula:
            curriculum.export()
        snapshot = self.repo.dashboard_snapshot()
        destination = self.paths.safe_root_file("知识库首页.html")
        latest = self.repo.latest_completed_report()
        latest_path = self.paths.latest_report_path()
        approved = self.repo.list_exam_runs(status="approved")
        self._atomic_write(
            destination,
            self._render(
                snapshot,
                latest_path if latest is not None and latest_path.is_file() else None,
                approved[0] if approved else None,
            ),
        )
        return destination

    def expected_paths(self) -> set[Path]:
        paths = {self.paths.safe_root_file("知识库首页.html")}
        for subject in self.repo.config.subjects:
            paths.update(self.markdown.expected_paths(subject))
        for curriculum in self.curricula:
            paths.update(curriculum.expected_paths())
        return paths

    def parent_note_paths(self) -> set[Path]:
        return {path for curriculum in self.curricula
                for path in curriculum.parent_note_paths()}

    def _render(
        self,
        snapshot: dict[str, Any],
        latest_report: Path | None = None,
        latest_exam=None,
    ) -> str:
        summary = snapshot["summary"]
        period = snapshot["period"]
        monthly = snapshot["monthly_period"]
        has_data = summary["document_count"] > 0
        cards = (
            self._card("本月收录资料", summary["monthly_document_count"], monthly["label"], monthly["sample_size"], has_data)
            + self._card("已分析题目", summary["question_count"], period["label"], period["sample_size"], has_data)
            + self._card("重点短板", summary["weak_knowledge_point_count"], period["label"], period["sample_size"], has_data)
            + self._card("待确认", summary["review_count"], period["label"], period["sample_size"], has_data)
        )
        subjects = "".join(self._subject_row(subject, data) for subject, data in snapshot["subjects"].items())
        embedded = self._safe_json(snapshot)
        report_entry = (
            f'<p><a href="{html.escape(self._href(latest_report), quote=True)}">查看最新学情报告</a></p>'
            if latest_report is not None else '<p class="future">尚未生成学情报告，请从桌面程序的“学习与复习”进入。</p>'
        )
        exam_entry = self._exam_entry(latest_exam)
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>晴子学习知识库</title><style>
:root {{ --ink:#1d2a3a;--muted:#64748b;--line:#dbe5ef;--paper:#f5f8fc;--card:#fff;--accent:#2d6ea3; }}
* {{ box-sizing:border-box; }} body {{ margin:0;background:var(--paper);color:var(--ink);font:15px/1.55 "Microsoft YaHei","Segoe UI",sans-serif; }}
aside {{ position:fixed;inset:0 auto 0 0;width:220px;padding:28px 20px;background:#183247;color:#fff; }} aside h1 {{ font-size:20px;line-height:1.35;margin:0 0 22px; }} nav a {{ display:block;color:#dcecf8;text-decoration:none;padding:7px 0; }}
main {{ max-width:1250px;margin-left:220px;padding:30px; }} h2 {{ margin:34px 0 14px;font-size:21px; }} .meta,.subtitle {{ color:var(--muted);font-size:13px; }} .cards {{ display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:14px; }} .card,.panel {{ background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px; }} main > section.panel {{ margin-top:18px; }} .number {{ font-size:28px;font-weight:700;margin:4px 0; }}
table {{ width:100%;border-collapse:collapse;background:var(--card);border:1px solid var(--line); }} th,td {{ text-align:left;padding:12px;border-bottom:1px solid var(--line); }} th {{ background:#edf4f9; }} ul {{ margin:0;padding-left:20px; }} li {{ margin:8px 0; }} a {{ color:var(--accent); }} .future {{ color:var(--muted); }} .pill {{ display:inline-block;padding:2px 8px;border-radius:999px;background:#fff2df;color:#8b4b0a;font-size:12px; }}
.weak-subjects {{ display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px; }} .weak-subject {{ min-width:0;padding:14px;border:1px solid var(--line);border-radius:10px;background:#f9fbfd; }} .weak-subject h3 {{ margin:0 0 10px;font-size:17px;color:var(--accent); }} .weak-subject .empty {{ margin:0;color:var(--muted); }}
@media (max-width:1050px) {{ .weak-subjects {{ grid-template-columns:1fr; }} }} @media (max-width:850px) {{ aside {{ position:static;width:auto; }} main {{ margin:0;padding:20px; }} .cards {{ grid-template-columns:1fr; }} }}
</style></head><body>
<aside><h1>晴子学习知识库</h1><nav><a href="#概览">学习总览</a>{self._subject_nav()}<a href="#错题本">错题本</a><a href="#知识点地图">知识点地图</a><a href="#学习报告">学情报告</a><a href="#模拟试卷">模拟试卷</a><a href="#待确认">待家长确认</a><a href="#最近更新">历次作业与试卷</a></nav></aside>
<main><section id="概览"><h2>学习总览</h2><p class="subtitle">累计统计：{html.escape(str(period['label']))}，题目样本 {period['sample_size']}；本月统计：{html.escape(str(monthly['label']))}，收录资料样本 {monthly['sample_size']}。</p><div class="cards">{cards}</div></section>
<section id="学科概况"><h2>三科概况</h2><table><thead><tr><th>科目</th><th>资料</th><th>题目样本</th><th>累计掌握度</th><th>近期趋势</th></tr></thead><tbody>{subjects}</tbody></table></section>
<section id="知识点地图" class="panel"><h2>知识点地图 · 重点短板</h2>{self._weak_points(snapshot['weak_knowledge_points'])}</section>
<section id="最近更新" class="panel"><h2>最近更新</h2>{self._recent_documents(snapshot['recent_documents'], snapshot['recent_documents_truncated'])}</section>
<section id="错题本" class="panel"><h2>错题本</h2>{self._error_bank(snapshot['error_bank'], snapshot['error_bank_truncated'])}</section>
<section id="学习报告" class="panel"><h2>学情报告</h2>{report_entry}</section>
<section id="模拟试卷" class="panel"><h2>针对性模拟试卷</h2>{exam_entry}<p class="future">新试卷请从桌面程序的“学习与复习”生成并由家长确认；这里只展示已批准版本。</p></section>
<section id="待确认" class="panel"><h2>待确认入口</h2>{self._pending(snapshot['pending_questions'], snapshot['pending_questions_truncated'])}<p class="future">待确认题目请在“晴子学习助手”桌面程序中复核；本网页仅供只读查看。</p></section>
<footer class="meta">此页面离线可用；原始资料、SQLite/JSON 为事实层，页面可随时重新生成。</footer></main>
<script>const dashboardSnapshot = {embedded};</script></body></html>"""

    def _subject_nav(self) -> str:
        return "".join(
            f'<a href="{self._href(self.paths.safe_file_path(subject, "知识点", "科目总览.md"))}">{html.escape(subject)}</a>'
            for subject in self.repo.config.subjects
        )

    def _exam_entry(self, run) -> str:
        if run is None:
            return '<p class="future">尚无已批准模拟卷。</p>'
        labels = {
            "student": "学生试卷",
            "answer_sheet": "答题纸",
            "solutions": "答案与解析",
            "blueprint": "组卷说明",
        }
        links = []
        root = self.paths.knowledge_root.resolve()
        for key, label in labels.items():
            relative = run.output_files.get(key)
            if not relative:
                continue
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            if candidate.is_file():
                links.append(self._link(label, candidate))
        if not links:
            return '<p class="future">已批准模拟卷的文件暂不可用。</p>'
        return (
            f'<p><strong>{html.escape(run.exam_id)}</strong> · {html.escape(run.subject)}</p>'
            '<p>' + "　".join(links) + "</p>"
        )

    def _subject_row(self, subject: str, data: dict[str, Any]) -> str:
        trend = data["recent_trend"]
        documents = str(data["document_count"]) if data["document_count"] else "尚无数据"
        questions = str(data["question_count"]) if data["document_count"] else "尚无数据"
        subject_link = self._link(subject, self.paths.safe_file_path(subject, "知识点", "科目总览.md"))
        return (
            f"<tr><td>{subject_link}</td><td>{documents}</td><td>{questions}</td>"
            f"<td>{self._percentage(data['mastery_rate'])}（样本 {data['mastery_sample_size']}）</td>"
            f"<td>{self._trend_text(trend)}</td></tr>"
        )

    def _weak_points(self, points: list[dict[str, Any]]) -> str:
        sections = []
        for subject in self.repo.config.subjects:
            subject_points = [point for point in points if point["subject"] == subject]
            if subject_points:
                body = "<ul>" + "".join(
                    f"<li><strong>{self._link(point['knowledge_point'], self.markdown.knowledge_point_path(subject, point['knowledge_point']))}</strong> "
                    f"<span class=\"pill\">优先级 {point['review_priority']}</span><br>掌握度 {self._percentage(point['mastery_rate'])}（样本 {point['exposure_count']}） · 累计趋势 {html.escape(trend_label(point['trend']))} · 错误 {point['incorrect_count']}</li>"
                    for point in subject_points
                ) + "</ul>"
            else:
                body = '<p class="empty">暂无重点短板。</p>'
            subject_curricula = [item for item in self.curricula if item.catalog["subject"] == subject]
            if subject_curricula:
                entries = "".join(
                    f'<p>{self._link(subject + "课程知识图（" + item.catalog["title"] + "）", item.entry_path())}'
                    f' · {"学校课程" if item.catalog["track"] == "school" else "兴趣班"}</p>'
                    for item in subject_curricula
                )
                body = (entries + '<p class="meta">教材目录确认的课程结构；橙色连线是教学整理建议。'
                        '尚未把历史题目自动归类到教材单元。</p>' + body)
            sections.append(
                f'<section class="weak-subject" data-subject="{html.escape(subject, quote=True)}">'
                f"<h3>{html.escape(subject)}</h3>{body}</section>"
            )
        return '<div class="weak-subjects">' + "".join(sections) + "</div>"

    def _recent_documents(self, documents: list[dict[str, Any]], truncated: bool) -> str:
        if not documents:
            return "<p>尚无数据。</p>"
        body = "<ul>" + "".join(
            f"<li><strong>{html.escape(item['subject'])}</strong> · {html.escape(item['document_type'])}<br>"
            f"{self._link(item['document_id'], self.markdown.document_path(item['subject'], item['document_id']))} · {html.escape(item['updated_at'] or '')}</li>"
            for item in documents if self._safe_linkable_document(item["subject"], item["document_id"])
        ) + "</ul>"
        if truncated:
            body += "<p class=\"meta\">仅显示最近 20 项；请从对应科目总览查看完整分析记录。</p>"
        return body

    def _error_bank(self, items: list[dict[str, Any]], truncated: bool) -> str:
        if not items:
            return "<p>尚无数据。</p>"
        body = "<ul>" + "".join(
            f"<li>{html.escape(item['subject'])} · {self._link(item['document_id'], self.markdown.document_path(item['subject'], item['document_id']))} · 第 {item['page']} 页第 {html.escape(item['question_id'])} 题：{html.escape(item['prompt_summary'])}（{html.escape(item['status'])}） · {html.escape('家长复核' if item['decision_source'] == 'parent' else item['decision_source'])} · 原图 {self._source_link(item.get('source_path'), item['page'])}</li>"
            for item in items if self._safe_linkable_document(item["subject"], item["document_id"])
        ) + "</ul>"
        if truncated:
            body += "<p class=\"meta\">仅显示最近 100 条错题；请从对应科目的完整错题索引查看全部记录。</p>"
        return body

    def _pending(self, items: list[dict[str, Any]], truncated: bool) -> str:
        if not items:
            return "<p>尚无数据。</p>"
        body = "<ul>" + "".join(
            f"<li>{html.escape(item['subject'])} · {self._link(item['document_id'], self.markdown.document_path(item['subject'], item['document_id']))} · 第 {item['page']} 页第 {html.escape(item['question_id'])} 题（置信度 {item['confidence']:.2f}）</li>"
            for item in items if self._safe_linkable_document(item["subject"], item["document_id"])
        ) + "</ul>"
        if truncated:
            body += "<p class=\"meta\">仅显示最近 100 项；请在桌面程序中处理完整待确认列表。</p>"
        return body

    def _source_link(self, source_path: str | None, page: int) -> str:
        if not source_path:
            return html.escape(f"page_{page:03d}.jpg（未找到路径）")
        candidate = Path(source_path)
        try:
            candidate.resolve().relative_to(self.paths.knowledge_root)
        except ValueError:
            return html.escape(f"{candidate.name or f'page_{page:03d}.jpg'}（待归档资料）")
        return self._link(candidate.name, candidate)

    def _href(self, target: Path) -> str:
        relative = os.path.relpath(target.resolve(), self.paths.knowledge_root).replace("\\", "/")
        return "/".join(
            segment if segment in (".", "..") else quote(segment, safe="-._~")
            for segment in relative.split("/")
        )

    def _link(self, label: str, target: Path) -> str:
        return f'<a href="{html.escape(self._href(target), quote=True)}">{html.escape(label)}</a>'

    def _safe_linkable_document(self, subject: str, document_id: str) -> bool:
        try:
            self.markdown.document_path(subject, document_id)
        except ValueError:
            return False
        return True

    @staticmethod
    def _card(label: str, value: int, period: str, sample_size: int, has_data: bool) -> str:
        rendered = str(value) if has_data else "尚无数据"
        return f'<div class="card"><div>{html.escape(label)}</div><div class="number">{rendered}</div><div class="meta">{html.escape(str(period))} · 样本 {sample_size}</div></div>'

    @staticmethod
    def _percentage(value: float | None) -> str:
        return "尚无数据" if value is None else f"{value * 100:.1f}%"

    @staticmethod
    def _trend_text(trend: dict[str, Any]) -> str:
        if trend["status"] == "insufficient_data":
            return f"样本不足，暂不判断趋势（{trend['period_label']}；资料 {trend['document_sample_size']}，题目样本 {trend['question_sample_size']}）"
        return f"{trend_label(trend['status'])}（{trend['period_label']}；资料 {trend['document_sample_size']}，题目样本 {trend['question_sample_size']}）"

    @staticmethod
    def _safe_json(snapshot: dict[str, Any]) -> str:
        return json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

    def _atomic_write(self, destination: Path, content: str) -> None:
        write_output(destination, content, self.paths.knowledge_root)
