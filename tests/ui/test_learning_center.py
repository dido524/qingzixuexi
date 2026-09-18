from datetime import datetime, timezone
from pathlib import Path
import queue

from qingzi_learning.config import AppConfig
from qingzi_learning.reporting.service import ReportArtifacts
from qingzi_learning.storage.repository import ReportRun
from qingzi_learning.ui.app import CaptureViewModel, UiEvent, WorkflowWorker


def _run(report_id: str = "report-one") -> ReportRun:
    return ReportRun(
        report_id=report_id,
        status="completed",
        previous_report_id=None,
        evidence_cutoff_at="2026-09-18T00:00:00Z",
        snapshot={"summary": {"question_count": 12}},
        narrative={"source": "local_template"},
        output_files={
            "child": f"学习报告/2026/09/{report_id}/孩子版.html",
            "parent": f"学习报告/2026/09/{report_id}/家长版.html",
            "latest": "学习报告/最新学情报告.html",
            "manifest": f"学习报告/2026/09/{report_id}/report.json",
        },
        error_code=None,
        created_at="2026-09-18 08:00:00",
        completed_at="2026-09-18 08:01:00",
    )


def test_worker_routes_report_history_and_generation_on_its_controller(tmp_path: Path) -> None:
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    artifacts = ReportArtifacts(
        "report-one",
        tmp_path / "child.html",
        tmp_path / "parent.html",
        tmp_path / "latest.html",
        tmp_path / "report.json",
    )

    class Controller:
        def __init__(self):
            self.repo = type("Repo", (), {"close": lambda self: None})()

        def recover_jobs(self):
            return []

        def report_history(self):
            return (_run(),)

        def generate_learning_report(self):
            return artifacts

    worker = WorkflowWorker(config, Controller, events=queue.Queue())
    worker._controller = Controller()

    list_operation = worker.submit("list_reports", context="learning_center", dialog_id="center")
    worker._process(worker._commands.get_nowait())
    listed = worker.events.get_nowait()
    assert listed.kind == "report_list" and listed.operation_id == list_operation
    assert listed.report_runs == (_run(),)

    generate_operation = worker.submit("generate_report", context="learning_center", dialog_id="center")
    worker._process(worker._commands.get_nowait())
    generated = worker.events.get_nowait()
    assert generated.kind == "report_generated" and generated.operation_id == generate_operation
    assert generated.report_artifacts == artifacts
    assert generated.report_runs == (_run(),)


def test_view_model_accepts_only_current_learning_center_dialog() -> None:
    view_model = CaptureViewModel()
    view_model.learning_center_dialog_id = "current"
    view_model.begin_work("读取报告", "op-current")

    stale = UiEvent(
        "report_list", operation_id="op-current", dialog_id="old",
        context="learning_center", report_runs=(_run("stale"),),
    )
    assert view_model.apply_event(stale) is False
    assert view_model.report_runs == ()

    view_model.begin_work("读取报告", "op-current")
    current = UiEvent(
        "report_list", operation_id="op-current", dialog_id="current",
        context="learning_center", report_runs=(_run(),), message="报告已加载。",
    )
    assert view_model.apply_event(current) is True
    assert view_model.report_runs == (_run(),)
    assert view_model.report_message == "报告已加载。"


def test_dialog_keeps_child_and_parent_print_targets_separate(
    tk_interpreter, tmp_path: Path
) -> None:
    from qingzi_learning.ui.learning_center import LearningCenterDialog

    root = tmp_path / "knowledge"
    run = _run()
    for relative in run.output_files.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("<html></html>", encoding="utf-8")
    opened = []
    printed = []
    dialog = LearningCenterDialog(
        tk_interpreter,
        "center",
        knowledge_root=root,
        on_generate_report=lambda _dialog_id: None,
        on_open=opened.append,
        on_print=printed.append,
        on_close=lambda _dialog_id: None,
    )
    try:
        dialog.show((run,), "报告已加载。", None)
        assert dialog.child_print_path.name == "孩子版.html"
        assert dialog.parent_print_path.name == "家长版.html"
        dialog.print_child()
        dialog.print_parent()
        assert printed == [dialog.child_print_path, dialog.parent_print_path]
        assert dialog.window.resizable() == (1, 1)
    finally:
        dialog.destroy()
