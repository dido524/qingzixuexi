import json
from pathlib import Path

from qingzi_learning.config import AppConfig
from qingzi_learning.exams.render import ExamRenderer
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import ExamQuestion, ExamRun


def _run(status: str = "needs_parent_approval") -> ExamRun:
    return ExamRun(
        exam_id="QZ-MATH-20260918-TEST",
        status=status,
        subject="数学",
        request={"scope": "分数", "duration_minutes": 40, "difficulty": "适中"},
        blueprint={
            "allocation": {"primary": 1, "related": 0, "stable": 0},
            "allocation_note": "关联证据不足，已重分配。",
            "targets": [{"knowledge_point": "分数应用", "category": "primary"}],
        },
        generation={"title": "五年级数学针对性练习", "instructions": "认真答题"},
        verification={"approved": True, "issues": []},
        output_files={},
        error_code=None,
        revision=1,
        created_at="2026-09-18 08:30:00",
        approved_at=None,
    )


def _questions() -> tuple[ExamQuestion, ...]:
    return (
        ExamQuestion(
            exam_id="QZ-MATH-20260918-TEST",
            question_id="Q01",
            question_type="application",
            points=100,
            knowledge_points=("分数应用",),
            blueprint_category="primary",
            prompt="一桶水用去四分之一，还剩多少？",
            answer="四分之三",
            explanation="把整桶看作单位一。",
            rubric="列式50分，答案50分。",
        ),
    )


def _renderer(tmp_path: Path) -> ExamRenderer:
    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "data",
    )
    paths = KnowledgePaths(config)
    directory = paths.exam_directory(2026, 9, "QZ-MATH-20260918-TEST")
    return ExamRenderer(paths, directory)


def test_student_artifacts_contain_no_answers_or_solution_links(tmp_path: Path) -> None:
    rendered = _renderer(tmp_path).render_approved(_run(), _questions())
    student = rendered["student"]
    answer_sheet = rendered["answer_sheet"]

    assert "四分之三" not in student + answer_sheet
    assert "标准答案" not in student + answer_sheet
    assert "答案与解析" not in student + answer_sheet
    assert "solutions" not in (student + answer_sheet).lower()
    assert "QZ-MATH-20260918-TEST" in student and "Q01" in student


def test_each_approved_page_is_independently_printable(tmp_path: Path) -> None:
    rendered = _renderer(tmp_path).render_approved(_run(), _questions())

    assert set(rendered) == {"student", "answer_sheet", "solutions", "blueprint"}
    for text in rendered.values():
        assert "@page" in text
        assert "window.print()" in text
        assert "QZ-MATH-20260918-TEST" in text
    assert 'class="worked-pages"' in rendered["solutions"]
    assert ".worked-pages .question+.question{break-before:page;page-break-before:always;padding-top:12mm" in rendered["solutions"]


def test_parent_preview_is_visibly_unapproved_and_not_printable(tmp_path: Path) -> None:
    text = _renderer(tmp_path).render_preview(_run(), _questions())

    assert "家长预览" in text and "尚未批准" in text
    assert "四分之三" in text
    assert "window.print()" not in text


def test_rendered_content_escapes_model_text(tmp_path: Path) -> None:
    question = _questions()[0]
    unsafe = ExamQuestion(**{**question.__dict__, "prompt": "题目<script>bad()</script>"})
    text = _renderer(tmp_path).render_approved(_run(), (unsafe,))["student"]

    assert "<script>bad()" not in text
    assert "&lt;script&gt;bad()" in text
