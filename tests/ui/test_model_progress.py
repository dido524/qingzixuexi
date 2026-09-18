from __future__ import annotations

from qingzi_learning.ui.app import CaptureViewModel, LearningAssistantApp
from qingzi_learning.ui.model_progress import progress_snapshot
from qingzi_learning.ui.model_progress import ModelProgressDialog


def test_exam_progress_explains_stage_elapsed_time_and_estimated_remaining_time():
    start = progress_snapshot("generate_exam", 0)
    middle = progress_snapshot("generate_exam", 120)
    late = progress_snapshot("generate_exam", 320)

    assert start.title == "正在认真准备模拟卷"
    assert start.percent == 8
    assert "预计还需约 5 分钟" in start.time_hint
    assert middle.elapsed_text == "已用 2分00秒"
    assert "独立校验" in middle.stage
    assert "预计还需约 3 分钟" in middle.time_hint
    assert late.percent == 92
    assert "最后检查" in late.time_hint


def test_only_model_operations_open_and_close_the_progress_card(tk_interpreter):
    root = __import__("tkinter").Toplevel(tk_interpreter)
    root.withdraw()

    class Worker:
        def submit(self, kind, **_kwargs):
            return f"op-{kind}"

    app = object.__new__(LearningAssistantApp)
    app.root = root
    app.worker = Worker()
    app.vm = CaptureViewModel()
    app._closing = False
    app._page_subject_dialog = None
    app._learning_center = None
    app._model_progress = None
    app._model_progress_operation_id = None
    app._refresh = lambda: None

    try:
        assert app._submit("capture", "拍摄") is True
        assert app._model_progress is None

        app.vm.finish_work("op-capture")
        assert app._submit("generate_report", "生成报告", context="learning_center") is True
        assert app._model_progress_operation_id == "op-generate_report"
        assert app._model_progress is not None

        app._finish_model_progress("some-other-operation")
        assert app._model_progress is not None
        app._finish_model_progress("op-generate_report")
        assert app._model_progress is None
    finally:
        app._finish_model_progress("op-generate_report", force=True)
        root.destroy()


def test_progress_card_uses_its_pink_fill_instead_of_the_native_green_bar(tk_interpreter):
    root = __import__("tkinter").Toplevel(tk_interpreter)
    root.withdraw()
    dialog = ModelProgressDialog(root, "generate_exam", now=lambda: 0)
    try:
        dialog.window.update_idletasks()
        fills = {
            dialog.progress_canvas.itemcget(item, "fill")
            for item in dialog.progress_canvas.find_all()
        }
        assert "#c75b8d" in fills
        assert "#f4e9f1" in fills
    finally:
        dialog.close()
        root.destroy()
