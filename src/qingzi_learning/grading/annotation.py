"""Render immutable, printable grading copies from trusted page manifests."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from html import escape
from io import BytesIO
from pathlib import Path
from urllib.parse import quote

from PIL import Image, ImageDraw, ImageFont

from qingzi_learning.domain import AnalysisResult, CapturedDocument, QuestionStatus
from qingzi_learning.export.safe_write import _guard, atomic_write, atomic_write_bytes
from qingzi_learning.grading.question_number import display_question_number


ANNOTATION_LAYOUT_VERSION = 3


def answer_box_stroke_width(width: int, height: int) -> int:
    """Use a visible but unobtrusive outline at the scan's native scale."""
    scale = max(1, min(width, height) / 900)
    return max(1, round(2 * scale))


@dataclass(frozen=True)
class AnnotationArtifacts:
    pages: tuple[Path, ...]
    gallery: Path


def annotated_page_path(source: Path, page_number: int) -> Path:
    return source.parent / f"批改结果_第{page_number:03d}页.png"


class AnnotationRenderer:
    def __init__(self, root: Path) -> None:
        self.root = root.absolute()

    def render(self, document: CapturedDocument, analysis: AnalysisResult) -> AnnotationArtifacts:
        if document.document_id != analysis.document_id:
            raise ValueError("批改结果与资料不匹配")
        page_numbers = {page.page_number for page in document.pages}
        if any(question.page not in page_numbers for question in analysis.questions):
            raise ValueError("批改题目页码不存在")
        outputs = []
        for page in sorted(document.pages, key=lambda item: item.page_number):
            _guard(page.path.absolute(), self.root)
            before = self._digest(page.path)
            if before != page.sha256:
                raise ValueError("批改原图哈希不匹配")
            with Image.open(page.path) as opened:
                image = opened.convert("RGB")
            image = self._draw_page(image, page.page_number, tuple(
                question for question in analysis.questions if question.page == page.page_number
            ))
            buffer = BytesIO()
            image.save(buffer, format="PNG", optimize=True)
            destination = annotated_page_path(page.path, page.page_number)
            _guard(destination.absolute(), self.root)
            atomic_write_bytes(destination, buffer.getvalue(), self.root)
            if self._digest(page.path) != before:
                raise ValueError("批改过程修改了原图")
            outputs.append(destination)
        gallery = document.session_dir / "批改结果.html"
        _guard(gallery.absolute(), self.root)
        atomic_write(gallery, self._gallery(document, analysis, tuple(outputs)), self.root)
        return AnnotationArtifacts(tuple(outputs), gallery)

    @staticmethod
    def _digest(path: Path) -> str:
        return sha256(path.read_bytes()).hexdigest()

    @staticmethod
    def _font(size: int, bold: bool = False):
        candidates = (
            Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc"),
            Path("C:/Windows/Fonts/simhei.ttf"),
        )
        for path in candidates:
            try:
                return ImageFont.truetype(str(path), size)
            except OSError:
                continue
        return ImageFont.load_default()

    def _draw_page(self, image: Image.Image, page_number: int, questions) -> Image.Image:
        """Put verdict text beside the worksheet, never on top of its content."""
        width, height = image.size
        scale = max(1, min(width, height) / 900)
        gutter_width = max(300, min(520, round(width * .42)))
        composed = Image.new("RGB", (width + gutter_width, height), "white")
        composed.paste(image, (0, 0))
        draw = ImageDraw.Draw(composed, "RGBA")
        title_font = self._font(max(18, round(24 * scale)), True)
        body_font = self._font(max(14, round(18 * scale)), True)
        draw.rectangle((width, 0, width + gutter_width, height), fill=(255, 248, 251, 255))
        draw.line((width, 0, width, height), fill=(231, 199, 215, 255), width=max(2, round(2 * scale)))
        gutter_left = width + max(18, round(22 * scale))
        draw.text((gutter_left, max(18, round(22 * scale))), f"第 {page_number} 页批改",
                  font=title_font, fill=(105, 43, 73, 255))
        draw.text((gutter_left, max(54, round(62 * scale))), "彩色框对应原题，说明集中列在这里",
                  font=self._font(max(12, round(14 * scale))), fill=(113, 103, 125, 255))
        colors = {
            QuestionStatus.CORRECT: ((34, 139, 94, 255), "✓ 正确"),
            QuestionStatus.INCORRECT: ((218, 57, 73, 255), "× 错误"),
            QuestionStatus.PARTIAL: ((224, 132, 32, 255), "△ 部分正确"),
            QuestionStatus.NEEDS_REVIEW: ((126, 87, 194, 255), "? 待确认"),
        }
        visible_questions = tuple(question for question in questions if question.answer_bbox is not None)
        row_top = max(90, round(106 * scale))
        row_height = max(40, round(48 * scale))
        row_gap = max(7, round(9 * scale))
        for index, question in enumerate(visible_questions):
            if question.answer_bbox is None:
                continue
            x, y, box_width, box_height = question.answer_bbox
            left, top = round(x * width), round(y * height)
            right, bottom = round((x + box_width) * width), round((y + box_height) * height)
            color, label = colors[question.status]
            stroke = answer_box_stroke_width(width, height)
            draw.rectangle((left, top, right, bottom), outline=color, width=stroke)
            pending = question.decision_source == "model" and question.status in {
                QuestionStatus.INCORRECT, QuestionStatus.PARTIAL,
            }
            label_top = row_top + index * (row_height + row_gap)
            if label_top + row_height > height - 14:
                break
            label_right = width + gutter_width - max(14, round(18 * scale))
            draw.rounded_rectangle((gutter_left, label_top, label_right, label_top + row_height),
                                   radius=max(7, round(9 * scale)), fill=color)
            text = f"题号 {display_question_number(question.question_id)}  {label}"
            draw.text((gutter_left + 10, label_top + max(5, round(7 * scale))), text,
                      font=body_font, fill=(255, 255, 255, 255))
            if pending:
                hint_font = self._font(max(10, round(12 * scale)))
                draw.text((gutter_left + 10, label_top + row_height - max(16, round(18 * scale))),
                          "AI 建议 · 待家长确认", font=hint_font, fill=(255, 255, 255, 235))
        remaining = len(visible_questions) - max(0, (height - row_top - 14) // (row_height + row_gap))
        if remaining > 0:
            draw.text((gutter_left, height - max(34, round(38 * scale))),
                      f"另有 {remaining} 道题，请见下方明细表", font=body_font, fill=(105, 43, 73, 255))
        return composed

    @staticmethod
    def _gallery(document: CapturedDocument, analysis: AnalysisResult, outputs: tuple[Path, ...]) -> str:
        cards = []
        for page, path in zip(sorted(document.pages, key=lambda item: item.page_number), outputs):
            rows = []
            labels = {
                QuestionStatus.CORRECT: "正确", QuestionStatus.INCORRECT: "错误",
                QuestionStatus.PARTIAL: "部分正确", QuestionStatus.NEEDS_REVIEW: "待确认",
            }
            for question in (item for item in analysis.questions if item.page == page.page_number):
                pending = (question.decision_source == "model" and question.status in {
                    QuestionStatus.INCORRECT, QuestionStatus.PARTIAL,
                })
                verdict = labels[question.status] + ("（模型建议，待家长确认）" if pending else "")
                rows.append(
                    "<tr>" + "".join(f"<td>{escape(str(value))}</td>" for value in (
                        display_question_number(question.question_id), question.student_answer, verdict,
                        question.reference_answer, question.reason,
                    )) + "</tr>"
                )
            cards.append(
                f'<section class="page"><h2>第 {page.page_number} 页</h2>'
                f'<img src="{quote(path.name)}" alt="第 {page.page_number} 页批改结果">'
                '<table><thead><tr><th>题号</th><th>学生答案</th><th>判断</th><th>参考答案</th><th>说明</th></tr></thead>'
                f'<tbody>{"".join(rows)}</tbody></table></section>'
            )
        return """<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<title>批改结果</title><style>
body{font-family:'Microsoft YaHei UI',sans-serif;margin:0;background:#fff7fa;color:#402635}
header{position:sticky;top:0;background:#fff;padding:18px 5vw;border-bottom:1px solid #efcad9;z-index:2}
button{background:#d65284;color:#fff;border:0;border-radius:8px;padding:10px 18px;font-weight:700}
main{max-width:1100px;margin:20px auto;padding:0 18px}.page{background:white;padding:18px;margin:18px 0;border-radius:14px;box-shadow:0 3px 18px #9b607522}
img{display:block;max-width:100%;height:auto;margin:auto}table{width:100%;border-collapse:collapse;margin-top:16px;font-size:14px}th,td{border:1px solid #ecd3dd;padding:8px;text-align:left;vertical-align:top}th{background:#fff0f5}@media print{header button{display:none}.page{box-shadow:none;break-after:page;margin:0;padding:0}body{background:white}}
</style></head><body><header><strong>""" + escape(analysis.subject.value) + " · " + escape(analysis.document_type) + """批改结果</strong>
<button onclick="window.print()">打印批改结果</button></header><main>""" + "".join(cards) + "</main></body></html>"
