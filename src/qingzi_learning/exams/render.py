"""Render isolated, self-contained A4 practice-exam artifacts."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import ExamQuestion, ExamRun


class ExamRenderer:
    def __init__(self, paths: KnowledgePaths, exam_directory: Path) -> None:
        self.paths = paths
        self.exam_directory = exam_directory.resolve()

    def render_preview(self, run: ExamRun, questions: tuple[ExamQuestion, ...]) -> str:
        question_html = "".join(self._question(question, show_solution=True) for question in questions)
        body = (
            '<div class="warning">家长预览 · 尚未批准 · 不可作为正式试卷打印</div>'
            f"{self._header(run)}<section><h2>题目与答案预览</h2>{question_html}</section>"
            f"<section><h2>组卷依据</h2>{self._blueprint_body(run)}</section>"
        )
        return self._page("家长预览", run.exam_id, body, printable=False)

    def render_approved(
        self, run: ExamRun, questions: tuple[ExamQuestion, ...]
    ) -> dict[str, str]:
        student_questions = "".join(self._question(q, show_solution=False) for q in questions)
        student = self._page(
            run.generation.get("title", "针对性练习卷"), run.exam_id,
            f"{self._header(run)}<section class=\"questions\">{student_questions}</section>",
            printable=True,
        )
        answer_rows = "".join(
            f'<section class="answer-box"><h2>{html.escape(q.question_id)} · {q.points}分</h2>'
            '<div class="writing-lines"></div></section>' for q in questions
        )
        answer_sheet = self._page(
            "答题纸", run.exam_id,
            f"{self._header(run, title='答题纸')}<section><p>姓名：__________　日期：__________</p></section>{answer_rows}",
            printable=True,
        )
        solution_rows = "".join(self._question(q, show_solution=True) for q in questions)
        solutions = self._page(
            "答案与解析", run.exam_id,
            f"{self._header(run, title='答案与解析')}<section class=\"worked-pages\">{solution_rows}</section>",
            printable=True,
        )
        blueprint = self._page(
            "组卷说明", run.exam_id,
            f"{self._header(run, title='组卷说明')}<section>{self._blueprint_body(run)}</section>",
            printable=True,
        )
        return {
            "student": student,
            "answer_sheet": answer_sheet,
            "solutions": solutions,
            "blueprint": blueprint,
        }

    @staticmethod
    def _header(run: ExamRun, *, title: str | None = None) -> str:
        request = run.request
        heading = title or run.generation.get("title", "针对性练习卷")
        meta = " · ".join(filter(None, (
            run.subject,
            str(request.get("scope", "全部范围")) or "全部范围",
            f"{request.get('duration_minutes', '')}分钟" if request.get("duration_minutes") else "",
            str(request.get("difficulty", "")),
        )))
        return (
            f'<header><p class="eyebrow">五年级针对性练习</p><h1>{html.escape(heading)}</h1>'
            f'<p>{html.escape(meta)}</p><p class="exam-id">试卷编号：{html.escape(run.exam_id)}</p></header>'
        )

    @staticmethod
    def _question(question: ExamQuestion, *, show_solution: bool) -> str:
        points = "、".join(question.knowledge_points)
        solution = ""
        if show_solution:
            solution = (
                f'<div class="solution"><h3>标准答案</h3><p>{html.escape(question.answer)}</p>'
                f'<h3>解析</h3><p>{html.escape(question.explanation)}</p>'
                f'<h3>评分标准</h3><p>{html.escape(question.rubric)}</p></div>'
            )
        return (
            f'<article class="question"><h2>{html.escape(question.question_id)} '
            f'<span>{question.points}分</span></h2><p>{html.escape(question.prompt)}</p>'
            f'<p class="target">知识点：{html.escape(points)}</p>{solution}</article>'
        )

    @staticmethod
    def _blueprint_body(run: ExamRun) -> str:
        allocation = run.blueprint.get("allocation", {})
        targets = run.blueprint.get("targets", [])
        rows = "".join(
            f'<tr><td>{html.escape(str(item.get("knowledge_point", "")))}</td>'
            f'<td>{html.escape(str(item.get("category", "")))}</td></tr>'
            for item in targets
        )
        return (
            "<h2>题量结构</h2>"
            f"<p>重点短板 {int(allocation.get('primary', 0))} 题；关联巩固 {int(allocation.get('related', 0))} 题；"
            f"稳定保持 {int(allocation.get('stable', 0))} 题。</p>"
            f'<p>{html.escape(str(run.blueprint.get("allocation_note", "")))}</p>'
            f"<table><thead><tr><th>目标知识点</th><th>类别</th></tr></thead><tbody>{rows}</tbody></table>"
            "<p class=\"muted\">本卷根据已确认学习证据生成；新题用于复测，不复制原错题。</p>"
        )

    @staticmethod
    def _page(title: str, exam_id: str, body: str, *, printable: bool) -> str:
        button = (
            '<button class="print-button" type="button" onclick="window.print()">打印本页</button>'
            '<script>addEventListener("load",()=>{if(new URLSearchParams(location.search).get("print")==="1")setTimeout(()=>window.print(),150);});</script>'
            if printable else ""
        )
        return f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{html.escape(title)}</title>
<style>
*{{box-sizing:border-box}} body{{margin:0;background:#eef2f4;color:#24313d;font:15px/1.65 "Microsoft YaHei","Segoe UI",sans-serif}}
main{{max-width:900px;margin:28px auto;background:#fff;padding:34px;border:1px solid #d8e0e5}}
header{{border-bottom:2px solid #315f72;padding-bottom:16px;margin-bottom:22px}} h1{{margin:0 0 8px;font-size:28px}} h2{{font-size:18px}}
.eyebrow,.target{{color:#315f72}} .exam-id{{font-family:Consolas,monospace}} .question{{break-inside:avoid;border-bottom:1px solid #d8e0e5;padding:12px 0 18px}}
.question h2 span{{float:right;font-weight:400;font-size:14px}} .solution{{background:#f3f7f8;border-left:3px solid #6f99a8;padding:10px 16px}}
.worked-pages .question+.question{{break-before:page;page-break-before:always;padding-top:12mm}}
.solution h3{{margin:5px 0 0;font-size:14px}} .answer-box{{break-inside:avoid}} .writing-lines{{height:150px;background:repeating-linear-gradient(#fff,#fff 31px,#d8e0e5 32px)}}
table{{width:100%;border-collapse:collapse}} th,td{{border:1px solid #d8e0e5;padding:8px;text-align:left}} .muted{{color:#667784}}
.warning{{padding:12px 16px;background:#fff2d8;color:#7a4b00;border:1px solid #eccb8c;margin-bottom:18px;font-weight:700}}
.print-button{{position:fixed;right:24px;bottom:24px;border:0;border-radius:999px;background:#315f72;color:#fff;padding:12px 20px;font-weight:700;cursor:pointer}}
@page{{size:A4 portrait;margin:14mm}} @media print{{body{{background:#fff;font-size:11pt}} main{{max-width:none;margin:0;padding:0;border:0}} .print-button{{display:none}}}}
</style></head><body data-exam-id="{html.escape(exam_id, quote=True)}"><main>{body}</main>{button}</body></html>'''
