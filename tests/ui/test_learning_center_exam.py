from pathlib import Path
import queue

from qingzi_learning.config import AppConfig
from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.exams.service import ExamArtifacts
from qingzi_learning.storage.repository import ExamRun
from qingzi_learning.ui.app import CaptureViewModel, WorkflowWorker


def _exam(status="needs_parent_approval") -> ExamRun:
    output = {}
    if status == "approved":
        output = {
            "student": "模拟试卷/2026/09/QZ-TEST/学生试卷.html",
            "answer_sheet": "模拟试卷/2026/09/QZ-TEST/答题纸.html",
            "solutions": "模拟试卷/2026/09/QZ-TEST/答案与解析.html",
            "blueprint": "模拟试卷/2026/09/QZ-TEST/组卷说明.html",
            "manifest": "模拟试卷/2026/09/QZ-TEST/exam.json",
        }
    return ExamRun(
        exam_id="QZ-TEST", status=status, subject="数学",
        request={"scope": "分数", "duration_minutes": 40, "difficulty": "适中", "question_count": 5},
        blueprint={"allocation": {"primary": 5, "related": 0, "stable": 0}, "targets": []},
        generation={
            "title": "分数专项练习", "instructions": "认真答题",
            "questions": [{"question_id": "Q01", "prompt": "一道新题", "points": 20,
                           "answer": "答案", "explanation": "解析"}],
        },
        verification={"approved": True}, output_files=output, error_code=None,
        revision=2 if status == "approved" else 1,
        created_at="2026-09-18 08:00:00",
        approved_at="2026-09-18 08:10:00" if status == "approved" else None,
    )


def test_exam_form_rejects_invalid_duration_and_question_count(tk_interpreter) -> None:
    import tkinter as tk
    from qingzi_learning.ui.learning_center import ExamForm

    frame = tk.Frame(tk_interpreter)
    form = ExamForm(frame, ("语文", "数学", "英语"))
    form.duration.set("0")
    form.question_count.set("101")

    assert form.build_request() is None
    assert "时长" in form.error_text.get() and "题量" in form.error_text.get()


def test_approved_exam_has_four_separate_print_targets(tk_interpreter, tmp_path: Path) -> None:
    from qingzi_learning.ui.learning_center import LearningCenterDialog

    root = tmp_path / "knowledge"
    run = _exam("approved")
    for relative in run.output_files.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html></html>", "utf-8")
    printed = []
    dialog = LearningCenterDialog(
        tk_interpreter, "center", knowledge_root=root,
        on_generate_report=lambda _id: None, on_open=lambda _path: None,
        on_print=printed.append, on_close=lambda _id: None,
        on_preview_exam=lambda _id, _request: None,
        on_generate_exam=lambda _id, _request: None,
        on_approve_exam=lambda _id, _exam_id, _revision: None,
    )
    try:
        dialog.show_exams((run,), "已批准。", None, None)
        assert {path.name for path in dialog.exam_print_paths} == {
            "学生试卷.html", "答题纸.html", "答案与解析.html", "组卷说明.html"
        }
        dialog.print_exam("student")
        dialog.print_exam("solutions")
        assert [path.name for path in printed] == ["学生试卷.html", "答案与解析.html"]
    finally:
        dialog.destroy()


def test_blueprint_preview_is_required_before_generation(tk_interpreter, tmp_path: Path) -> None:
    from qingzi_learning.ui.learning_center import LearningCenterDialog

    generated = []
    dialog = LearningCenterDialog(
        tk_interpreter, "center", knowledge_root=tmp_path,
        on_generate_report=lambda _id: None, on_open=lambda _path: None,
        on_print=lambda _path: None, on_close=lambda _id: None,
        on_preview_exam=lambda _id, _request: None,
        on_generate_exam=lambda _id, request: generated.append(request),
        on_approve_exam=lambda *_args: None,
    )
    try:
        assert str(dialog.exam_buttons["generate"].cget("state")) == "disabled"
        dialog.show_exams((), "蓝图已生成。", {"allocation": {}, "targets": []}, None)
        assert str(dialog.exam_buttons["generate"].cget("state")) == "normal"
    finally:
        dialog.destroy()


def test_preview_blueprint_immediately_shows_progress_and_disables_actions(
    tk_interpreter, tmp_path: Path
) -> None:
    from qingzi_learning.ui.learning_center import LearningCenterDialog

    submitted = []
    dialog = LearningCenterDialog(
        tk_interpreter, "center", knowledge_root=tmp_path,
        on_generate_report=lambda _id: None, on_open=lambda _path: None,
        on_print=lambda _path: None, on_close=lambda _id: None,
        on_preview_exam=lambda _id, request: submitted.append(request) or True,
        on_generate_exam=lambda _id, _request: None,
        on_approve_exam=lambda *_args: None,
    )
    try:
        dialog.preview_exam()

        assert submitted
        assert "正在计算" in dialog.exam_message.get()
        assert str(dialog.exam_buttons["preview"].cget("state")) == "disabled"
    finally:
        dialog.destroy()


def test_preview_blueprint_rejected_by_worker_is_visible_in_exam_tab(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    request = ExamRequest("数学", "不存在的范围", 40, "适中", 5, False, False)

    class Controller:
        def __init__(self): self.repo = type("Repo", (), {"close": lambda self: None})()
        def exam_history(self): return ()
        def preview_exam_blueprint(self, _request):
            raise ValueError("考试范围内还没有已确认的有效学习证据")

    worker = WorkflowWorker(config, Controller, events=queue.Queue())
    worker._controller = Controller()
    operation = worker.submit(
        "preview_exam_blueprint", context="learning_center", dialog_id="center",
        exam_request=request,
    )

    worker._process(worker._commands.get_nowait())

    event = worker.events.get_nowait()
    assert event.kind == "exam_failed"
    assert "考试范围" in event.message
    view_model = CaptureViewModel()
    view_model.learning_center_dialog_id = "center"
    view_model.begin_work("正在计算", operation)
    assert view_model.apply_event(event)
    assert view_model.exam_message == event.message
    assert not view_model.busy


def test_generate_exam_immediately_shows_progress_and_disables_actions(
    tk_interpreter, tmp_path: Path
) -> None:
    from qingzi_learning.ui.learning_center import LearningCenterDialog

    submitted = []
    dialog = LearningCenterDialog(
        tk_interpreter, "center", knowledge_root=tmp_path,
        on_generate_report=lambda _id: None, on_open=lambda _path: None,
        on_print=lambda _path: None, on_close=lambda _id: None,
        on_preview_exam=lambda _id, _request: None,
        on_generate_exam=lambda _id, request: submitted.append(request) or True,
        on_approve_exam=lambda *_args: None,
    )
    try:
        dialog.show_exams((), "蓝图已生成。", {"allocation": {}, "targets": []}, None)

        dialog.generate_exam()

        assert submitted
        assert "正在生成" in dialog.exam_message.get()
        assert str(dialog.exam_buttons["generate"].cget("state")) == "disabled"
    finally:
        dialog.destroy()


def test_completed_generation_selects_the_new_exam_instead_of_an_old_failure(
    tk_interpreter, tmp_path: Path
) -> None:
    from qingzi_learning.ui.learning_center import LearningCenterDialog

    old_failure = _exam("failed")
    new_exam = _exam("needs_parent_approval")
    new_exam = new_exam.__class__(
        **{**new_exam.__dict__, "exam_id": "QZ-NEW", "created_at": "2026-09-18 09:15:23"}
    )
    dialog = LearningCenterDialog(
        tk_interpreter, "center", knowledge_root=tmp_path,
        on_generate_report=lambda _id: None, on_open=lambda _path: None,
        on_print=lambda _path: None, on_close=lambda _id: None,
        on_preview_exam=lambda _id, _request: None,
        on_generate_exam=lambda _id, _request: None,
        on_approve_exam=lambda *_args: None,
    )
    try:
        dialog.show_exams((old_failure,), "旧记录", None, None)
        assert dialog.selected_exam.exam_id == "QZ-TEST"

        dialog.show_exams(
            (new_exam, old_failure), "模拟卷已通过校验。", None, None,
            selected_exam_id="QZ-NEW",
        )

        assert dialog.selected_exam.exam_id == "QZ-NEW"
        assert "needs_parent_approval" in dialog.exam_preview.get("1.0", "end")
    finally:
        dialog.destroy()


def test_exam_validation_failure_is_visible_in_exam_tab(tmp_path: Path) -> None:
    from qingzi_learning.exams.service import ExamGenerationError

    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    request = ExamRequest("数学", "分数", 40, "适中", 5, False, False)

    class Controller:
        def __init__(self): self.repo = type("Repo", (), {"close": lambda self: None})()
        def exam_history(self): return (_exam("failed"),)
        def generate_exam(self, _request): raise ExamGenerationError("exam_validation_failed")

    worker = WorkflowWorker(config, Controller, events=queue.Queue())
    worker._controller = Controller()
    operation = worker.submit(
        "generate_exam", context="learning_center", dialog_id="center", exam_request=request
    )

    worker._process(worker._commands.get_nowait())

    event = worker.events.get_nowait()
    assert event.kind == "exam_failed"
    assert event.exam_runs[0].status == "failed"
    assert "未通过" in event.message and "校验" in event.message

    view_model = CaptureViewModel()
    view_model.learning_center_dialog_id = "center"
    view_model.begin_work("正在生成", operation)
    assert view_model.apply_event(event)
    assert view_model.exam_message == event.message
    assert not view_model.busy


def test_worker_routes_exam_preview_generation_approval_and_history(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    request = ExamRequest("数学", "分数", 40, "适中", 5, False, False)
    artifacts = ExamArtifacts(*(tmp_path / name for name in (
        "学生试卷.html", "答题纸.html", "答案与解析.html", "组卷说明.html", "exam.json"
    )))

    class Controller:
        def __init__(self): self.repo = type("Repo", (), {"close": lambda self: None})()
        def recover_jobs(self): return []
        def report_history(self): return ()
        def exam_history(self): return (_exam(),)
        def preview_exam_blueprint(self, value): assert value == request; return {"allocation": {}}
        def generate_exam(self, value): assert value == request; return _exam()
        def approve_exam(self, exam_id, expected_revision):
            assert (exam_id, expected_revision) == ("QZ-TEST", 1)
            return artifacts

    worker = WorkflowWorker(config, Controller, events=queue.Queue())
    worker._controller = Controller()
    for kind, expected_event, kwargs in (
        ("preview_exam_blueprint", "exam_blueprint", {"exam_request": request}),
        ("generate_exam", "exam_generated", {"exam_request": request}),
        ("approve_exam", "exam_approved", {"exam_id": "QZ-TEST", "expected_revision": 1}),
        ("list_exams", "exam_list", {}),
    ):
        worker.submit(kind, context="learning_center", dialog_id="center", **kwargs)
        worker._process(worker._commands.get_nowait())
        event = worker.events.get_nowait()
        assert event.kind == expected_event
        if expected_event == "exam_generated":
            assert event.target_id == "QZ-TEST"
