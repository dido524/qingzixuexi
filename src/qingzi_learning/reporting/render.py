"""Self-contained child and parent HTML learning reports."""

from __future__ import annotations

import html
import os
from pathlib import Path
from typing import Any
from urllib.parse import quote

from qingzi_learning.export.labels import trend_label
from qingzi_learning.storage.paths import KnowledgePaths


_LEVELS = {
    "no_data": "尚无足够资料",
    "initial": "初步观察",
    "forming": "逐步形成",
    "stable": "趋势较稳定",
}


class ReportRenderer:
    def __init__(self, paths: KnowledgePaths, report_directory: Path) -> None:
        self.paths = paths
        self.report_directory = report_directory.resolve()

    def render_child(self, profile: dict[str, Any], narrative: dict[str, Any]) -> str:
        summary = profile["summary"]
        delta = profile["delta"]
        subjects = "".join(
            self._child_subject(subject, data)
            for subject, data in profile["subjects"].items()
        )
        progress = self._items(narrative["child_progress"], "还在积累可以比较的练习记录。")
        focus = self._items(narrative["child_focus"], "目前没有需要特别标出的练习重点。")
        goals = self._items(narrative["child_goals"], "继续保持正常练习，并认真检查答案。")
        retests = self._child_retests(profile.get("retests", []))
        body = f"""
<header><p class="eyebrow">阶段学习小结</p><h1>{html.escape(narrative['child_headline'])}</h1>
<p class="lead">{html.escape(narrative['child_closing'])}</p></header>
<section class="facts"><div><strong>{summary['document_count']}</strong><span>累计资料</span></div>
<div><strong>{summary['question_count']}</strong><span>有效题目</span></div>
<div><strong>{max(0, delta['new_document_count'])}</strong><span>本次新增资料</span></div></section>
<section><h2>各科学习情况</h2><div class="subjects">{subjects}</div></section>
<section class="two"><article><h2>看得见的进步</h2>{progress}</article><article><h2>接下来重点练习</h2>{focus}</article></section>
<section class="goals"><h2>下一步的小目标</h2>{goals}</section>
{retests}
<footer>这份报告只根据已经确认的练习记录生成。资料增加后，观察会继续更新。</footer>"""
        return self._page("晴子阶段学习小结", body, "child")

    def render_parent(self, profile: dict[str, Any], narrative: dict[str, Any]) -> str:
        summary = profile["summary"]
        subject_sections = "".join(
            self._parent_subject(subject, data)
            for subject, data in profile["subjects"].items()
        )
        observations = self._items(narrative["parent_observations"], "暂无可确认的重点观察。")
        recommendations = self._items(narrative["parent_recommendations"], "继续积累已确认样本后再制定专项安排。")
        retests = self._parent_retests(profile.get("retests", []))
        body = f"""
<header><p class="eyebrow">家长阅读材料</p><h1>家长版学情报告</h1>
<p class="lead">{html.escape(narrative['parent_summary'])}</p></header>
<section class="facts"><div><strong>{summary['document_count']}</strong><span>累计资料</span></div>
<div><strong>{summary['question_count']}</strong><span>有效题目</span></div>
<div><strong>{summary['pending_count']}</strong><span>待确认</span></div>
<div><strong>{summary['excluded_low_confidence_count']}</strong><span>未纳入的低置信度判断</span></div></section>
<section class="two"><article><h2>主要观察</h2>{observations}</article><article><h2>建议安排</h2>{recommendations}</article></section>
{subject_sections}
{retests}
<footer>截止时间：{html.escape(profile['cutoff_at'])}。掌握度必须结合样本数阅读；待确认题目不计入掌握统计。</footer>"""
        return self._page("晴子家长版学情报告", body, "parent")

    def _child_subject(self, subject: str, data: dict[str, Any]) -> str:
        level = _LEVELS[data["evidence_level"]]
        if data["question_count"]:
            detail = f"有效题目 {data['question_count']} · 学习日期 {data['study_day_count']}"
        else:
            detail = "继续积累资料后会逐步丰富"
        return (
            f'<article class="subject"><h3>{html.escape(subject)}</h3>'
            f'<span class="tag">{html.escape(level)}</span><p>{html.escape(detail)}</p></article>'
        )

    def _parent_subject(self, subject: str, data: dict[str, Any]) -> str:
        rows = []
        for point in data["knowledge_points"].values():
            evidence = "".join(
                self._evidence_link(subject, item)
                for item in point["representative_questions"]
            ) or "<span>暂无可链接题目</span>"
            categories = "、".join(item["name"] for item in point["error_categories"]) or "暂无集中错因"
            rows.append(
                "<tr>"
                f"<td>{html.escape(point['knowledge_point'])}</td>"
                f"<td>{point['mastery_rate'] * 100:.1f}%（样本 {point['exposure_count']}）</td>"
                f"<td>{point['review_priority']}</td>"
                f"<td>{html.escape(trend_label(point['trend']))}</td>"
                f"<td>{html.escape(categories)}</td><td>{evidence}</td></tr>"
            )
        table = self._knowledge_tables(rows) if rows else "<p class=\"empty\">暂无有效知识点样本。</p>"
        level = _LEVELS[data["evidence_level"]]
        return (
            f'<section><div class="section-title"><h2>{html.escape(subject)}</h2>'
            f'<span class="tag">{html.escape(level)}</span></div>'
            f'<p class="meta">资料 {data["document_count"]} · 有效题目 {data["question_count"]} · 学习日期 {data["study_day_count"]}</p>{table}</section>'
        )

    def _evidence_link(self, subject: str, item: dict[str, Any]) -> str:
        try:
            self.paths.safe_file_path(subject, "分析记录", f"{item['document_id']}.md")
            display_name = item.get("display_name") or item["document_id"]
            target = self.paths.safe_file_path(subject, "分析记录", f"{display_name}.md")
            relative = os.path.relpath(target, self.report_directory).replace("\\", "/")
            href = "/".join(
                segment if segment in (".", "..") else quote(segment, safe="-._~")
                for segment in relative.split("/")
            )
            label = f"{display_name} · 题号 {item['question_id']}"
            return f'<a href="{html.escape(href, quote=True)}">{html.escape(label)}</a> '
        except ValueError:
            return ""

    @staticmethod
    def _child_retests(retests: list[dict[str, Any]]) -> str:
        if not retests:
            return ""
        items = []
        for retest in retests[-3:]:
            points = "、".join(retest["knowledge_points"])
            if retest["current_status"] == "correct":
                text = f"{points}：这次复测已答对，建议再确认一次。"
            elif retest["current_status"] == "partial":
                text = f"{points}：这次复测已经完成一部分，可以再练一次。"
            else:
                text = f"{points}：这次复测仍需要练习，先回看方法再尝试。"
            items.append(f"<li>{html.escape(text)}</li>")
        return f'<section><h2>专项复测进展</h2><ul>{"".join(items)}</ul></section>'

    def _parent_retests(self, retests: list[dict[str, Any]]) -> str:
        if not retests:
            return ""
        labels = {"correct": "正确", "incorrect": "错误", "partial": "部分正确", "unknown": "无可比记录"}
        rows = []
        for retest in retests:
            attempts = " ".join(
                self._evidence_link(retest["subject"], {
                    "document_id": attempt["document_id"],
                    "display_name": attempt.get("display_name"),
                    "question_id": attempt["document_question_id"],
                })
                for attempt in retest["attempts"]
            )
            rows.append(
                "<tr>"
                f"<td>{html.escape(retest['exam_id'])}</td>"
                f"<td>{html.escape(retest['exam_question_id'])}</td>"
                f"<td>{html.escape('、'.join(retest['knowledge_points']))}</td>"
                f"<td>{html.escape(labels.get(retest['previous_status'], retest['previous_status']))}</td>"
                f"<td>{html.escape(labels.get(retest['current_status'], retest['current_status']))}</td>"
                f"<td>{attempts}</td></tr>"
            )
        return (
            '<section><h2>模拟卷复测追踪</h2><table><thead><tr>'
            '<th>试卷编号</th><th>打印题号</th><th>目标知识点</th><th>原证据</th><th>复测结果</th><th>回拍记录</th>'
            f'</tr></thead><tbody>{"".join(rows)}</tbody></table></section>'
        )

    @staticmethod
    def _items(items: list[dict[str, Any]], empty: str) -> str:
        if not items:
            return f'<p class="empty">{html.escape(empty)}</p>'
        return "<ul>" + "".join(f"<li>{html.escape(item['text'])}</li>" for item in items) + "</ul>"

    @staticmethod
    def _knowledge_tables(rows: list[str]) -> str:
        header = (
            "<thead><tr><th>知识点</th><th>掌握情况</th><th>优先级</th>"
            "<th>趋势</th><th>常见错因</th><th>证据</th></tr></thead>"
        )
        return "".join(
            '<table class="knowledge-table">'
            f"{header}<tbody>{''.join(rows[start:start + 4])}</tbody></table>"
            for start in range(0, len(rows), 4)
        )

    @staticmethod
    def _page(title: str, body: str, kind: str) -> str:
        return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
:root{{--ink:#243447;--muted:#65758b;--line:#dce5ed;--paper:#f4f7f8;--card:#fff;--blue:#2f6f89;--green:#3f7b6b;}}
*{{box-sizing:border-box}} body{{margin:0;background:var(--paper);color:var(--ink);font:15px/1.65 "Microsoft YaHei","Segoe UI",sans-serif}}
main{{max-width:1040px;margin:30px auto;padding:0 24px}} header,section{{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:24px;margin:0 0 16px}}
h1{{font-size:30px;margin:0 0 8px}} h2{{font-size:20px;margin:0 0 14px}} h3{{margin:0 0 8px}} .eyebrow{{color:var(--blue);font-weight:700;letter-spacing:.08em;margin:0 0 5px}}
.lead,.meta,.empty,footer{{color:var(--muted)}} .facts{{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}} .facts div{{padding:12px;background:#f3f8fa;border-radius:10px}}
.facts strong{{display:block;font-size:24px;color:var(--blue)}} .facts span{{color:var(--muted)}} .subjects{{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}}
.subject{{padding:16px;border:1px solid var(--line);border-radius:10px}} .tag{{display:inline-block;padding:2px 9px;border-radius:999px;background:#e8f3ef;color:var(--green);font-size:12px}}
.two{{display:grid;grid-template-columns:1fr 1fr;gap:16px}} .two article{{border-left:3px solid #b8d7d0;padding-left:16px}} ul{{padding-left:20px}} li{{margin:8px 0}}
.section-title{{display:flex;align-items:center;gap:10px}} table{{width:100%;border-collapse:collapse;font-size:13px}} .knowledge-table+.knowledge-table{{margin-top:12px}} th,td{{padding:9px;border-bottom:1px solid var(--line);vertical-align:top;text-align:left}} th{{background:#f1f6f8}} a{{color:var(--blue)}}
.print-button{{position:fixed;right:24px;bottom:24px;border:0;border-radius:999px;background:var(--blue);color:#fff;padding:12px 20px;font-weight:700;cursor:pointer}} footer{{max-width:1040px;margin:18px auto;padding:0 24px 30px}}
@media(max-width:760px){{.facts,.subjects,.two{{grid-template-columns:1fr}} main{{padding:0 12px}} table{{display:block;overflow:auto}}}}
@page{{size:A4 portrait;margin:14mm}} @media print{{body{{background:#fff;font-size:11pt}} main{{margin:0;max-width:none;padding:0}} header,section{{break-inside:avoid;border-color:#c9d2d9;box-shadow:none;margin-bottom:8mm}} table{{break-inside:avoid-page!important;page-break-inside:avoid!important}} thead{{display:table-header-group}} tr{{break-inside:avoid-page!important;page-break-inside:avoid!important}} thead tr,tbody tr{{display:grid;grid-template-columns:12% 14% 7% 7% 19% 41%}} th,td{{min-width:0;overflow-wrap:anywhere}} .print-button{{display:none}} a{{color:inherit;text-decoration:none}} footer{{padding:0}}}}
</style></head><body data-report-kind="{html.escape(kind, quote=True)}"><main>{body}</main>
<button class="print-button" type="button" onclick="window.print()">打印这份报告</button>
<script>addEventListener("load",()=>{{if(new URLSearchParams(location.search).get("print")==="1")setTimeout(()=>window.print(),150);}});</script>
</body></html>"""
