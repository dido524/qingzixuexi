from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import queue
import threading
from unittest.mock import Mock

from PIL import Image
from io import BytesIO
from dataclasses import replace

import pytest

from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig
from qingzi_learning.ui import app as ui_app
import qingzi_learning.ui.app as app_module
from qingzi_learning.ui.app import CaptureViewModel, CompletionSummary, LearningAssistantApp, UiEvent, WorkflowWorker
from qingzi_learning.workflow.controller import WorkflowOutcome
from qingzi_learning.workflow.subject_split import PendingPageSubject
from qingzi_learning.domain import Subject


@pytest.fixture
def config(tmp_path):
    return AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=0xBC15,
        camera_pid=0x2C1B,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "data",
    )


@pytest.fixture
def session(config):
    return CaptureSession(config, "ui-capture")


@pytest.fixture
def view_model(session):
    return CaptureViewModel(session)


def test_button_states_follow_capture_flow(view_model):
    assert view_model.can_capture and not view_model.can_finish
    view_model.on_page_captured()
    assert view_model.can_retake and view_model.can_next and view_model.can_finish
    view_model.on_next_page()
    assert view_model.page_number == 2 and view_model.can_capture


def test_close_during_capture_persists_recovery_state(view_model, session):
    view_model.on_page_captured()
    view_model.on_close()
    assert (session.session_dir / "session.json").exists()
    assert view_model.close_requested


def test_busy_state_rejects_double_clicks_and_preserves_current_page(view_model):
    view_model.on_page_captured()
    view_model.begin_work("正在保存第 1 页")
    assert not view_model.can_capture
    assert not view_model.can_finish
    assert view_model.page_number == 1
    view_model.finish_work()
    assert view_model.can_retake and view_model.can_finish


def test_recovery_failure_is_locatable_error_not_subject_prompt(view_model, session):
    outcome = WorkflowOutcome(
        job_id="",
        state="pending",
        subject=None,
        error_code="recovery_failed",
        recovery_path=session.session_dir,
    )
    view_model.on_workflow_outcome(outcome)
    assert view_model.recovery_error_path == session.session_dir
    assert not view_model.subject_confirmation_needed
    assert "恢复" in view_model.status


def test_unknown_subject_only_prompts_when_workflow_requests_confirmation(view_model):
    view_model.on_workflow_outcome(
        WorkflowOutcome("job-1", "needs_subject_confirmation", None)
    )
    assert view_model.subject_confirmation_needed
    assert view_model.pending_job_id == "job-1"
    view_model.on_subject_chosen("数学")
    assert not view_model.subject_confirmation_needed
    assert view_model.pending_job_id == "job-1"


@pytest.fixture
def pending_page_outcome():
    return WorkflowOutcome(
        "capture-1", "needs_subject_confirmation", None,
        pending_page_subjects=(
            PendingPageSubject(4, Path("page_004.jpg"), Subject.ENGLISH, .61, "题干为英文"),
            PendingPageSubject(2, Path("page_002.jpg"), Subject.MATH, .55, "含有公式"),
        ),
    )


def test_outcome_opens_first_pending_page_and_advances(view_model, pending_page_outcome):
    """A page-mode outcome selects the lowest outstanding page, not batch confirmation."""
    view_model.on_workflow_outcome(pending_page_outcome)

    assert view_model.page_subject_dialog_needed
    assert not view_model.subject_confirmation_needed
    assert [item.page for item in view_model.pending_page_subjects] == [2, 4]
    assert view_model.active_page_subject.page == 2

    view_model.on_workflow_outcome(replace(
        pending_page_outcome,
        pending_page_subjects=pending_page_outcome.pending_page_subjects[:1],
    ))

    assert view_model.page_subject_dialog_needed
    assert view_model.active_page_subject.page == 4


def test_pending_or_terminal_page_outcome_closes_page_dialog(view_model, pending_page_outcome):
    """Publication-pending and terminal outcomes cannot leave a stale page choice open."""
    view_model.on_workflow_outcome(pending_page_outcome)
    view_model.on_workflow_outcome(WorkflowOutcome("capture-1", "pending", None))

    assert not view_model.page_subject_dialog_needed
    assert view_model.pending_page_subjects == ()
    assert view_model.active_page_subject is None

    view_model.on_workflow_outcome(pending_page_outcome)
    view_model.on_workflow_outcome(WorkflowOutcome("capture-1", "completed", None))

    assert not view_model.page_subject_dialog_needed
    assert view_model.active_page_subject is None


def test_stale_page_confirmation_result_from_old_dialog_cannot_advance_current_dialog(view_model, pending_page_outcome):
    """An old page dialog cannot consume the operation now owned by a new dialog."""
    view_model.on_workflow_outcome(pending_page_outcome)
    old_dialog_id = view_model.page_subject_dialog_id
    view_model.defer_page_subject_dialog(old_dialog_id)
    view_model.on_workflow_outcome(pending_page_outcome)
    current_dialog_id = view_model.page_subject_dialog_id
    view_model.begin_work("正在继续", "confirm-page-op")

    accepted = view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="confirm-page-op", dialog_id=old_dialog_id,
        outcome=replace(pending_page_outcome, pending_page_subjects=pending_page_outcome.pending_page_subjects[:1]),
    ))

    assert old_dialog_id != current_dialog_id
    assert not accepted
    assert view_model.busy
    assert view_model.active_page_subject.page == 2


def test_page_confirmation_result_requires_current_dialog_id(view_model, pending_page_outcome):
    """A result without the page dialog ID is not an authorized page confirmation."""
    view_model.on_workflow_outcome(pending_page_outcome)
    dialog_id = view_model.page_subject_dialog_id
    view_model.on_page_subject_chosen("数学", "confirm-page-op")
    next_outcome = replace(
        pending_page_outcome, pending_page_subjects=pending_page_outcome.pending_page_subjects[:1],
    )

    missing_id = view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="confirm-page-op", outcome=next_outcome,
    ))

    assert not missing_id
    assert view_model.busy
    assert view_model.active_page_subject.page == 2

    accepted = view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="confirm-page-op", dialog_id=dialog_id, outcome=next_outcome,
    ))

    assert accepted
    assert not view_model.busy
    assert view_model.active_page_subject.page == 4


def test_page_subject_choice_immediately_disables_actions_until_matching_result(view_model, pending_page_outcome):
    view_model.on_workflow_outcome(pending_page_outcome, active_capture=False)
    dialog_id = view_model.page_subject_dialog_id
    assert view_model.can_capture

    page = view_model.on_page_subject_chosen("数学", "confirm-page-op")

    assert page == 2
    assert view_model.busy
    assert not view_model.can_capture and not view_model.can_retake and not view_model.can_next

    accepted = view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="confirm-page-op", dialog_id=dialog_id,
        outcome=replace(pending_page_outcome, pending_page_subjects=pending_page_outcome.pending_page_subjects[:1]),
    ))

    assert accepted
    assert not view_model.busy
    assert view_model.active_page_subject.page == 4


def test_page_subject_dialog_submits_selected_recovery_context_with_current_identity(monkeypatch, pending_page_outcome):
    """A recovered row owns its own job/context instead of the latest recovery event."""
    created = []

    class FakePageSubjectDialog:
        def __init__(self, parent, *, on_choose, on_defer):
            self.parent = parent
            self.on_choose = on_choose
            self.on_defer = on_defer
            self.busy = None
            self.shown = None
            created.append(self)

        def show(self, item, index, total):
            self.shown = (item, index, total)

        def set_busy(self, busy):
            self.busy = busy

        def destroy(self):
            pass

    monkeypatch.setattr(app_module, "PageSubjectDialog", FakePageSubjectDialog)
    app = object.__new__(LearningAssistantApp)
    app.root = object()
    app.vm = CaptureViewModel()
    app.vm.on_workflow_outcome(pending_page_outcome, active_capture=False)
    app.worker = Mock()
    app.worker.submit.return_value = "confirm-page-op"
    app._closing = False
    app._page_subject_dialog = None
    app._page_subject_context = None
    app._refresh = Mock()

    dialog_id = app.vm.page_subject_dialog_id
    app._show_page_subject_dialog("capture-1", "recovered_job", "recovery-gen", "recovery-session")
    created[0].on_choose(2, "数学")

    app.worker.submit.assert_called_once_with(
        "confirm_page_subject", job_id="capture-1", page_number=2, subject="数学",
        context="recovered_job", session_generation="recovery-gen", session_id="recovery-session",
        dialog_id=dialog_id,
    )
    assert app.vm.busy
    assert created[0].busy


def test_page_subject_dialog_refuses_main_window_workflow_commands(withdrawn_app, pending_page_outcome):
    """A visible page choice cannot interleave capture, retry, or review work."""
    app, _ = withdrawn_app

    class Dialog:
        def set_busy(self, busy):
            self.busy = busy

        def destroy(self):
            pass

    app.vm.on_workflow_outcome(pending_page_outcome, active_capture=False)
    app._page_subject_dialog = Dialog()
    app._page_subject_context = ("capture-1", "recovered_job", None, None, app.vm.page_subject_dialog_id)
    submit = Mock(wraps=app.worker.submit)
    app.worker.submit = submit
    app.vm.recovered_tasks["old-job"] = UiEvent(
        "recovered_job", outcome=WorkflowOutcome("old-job", "pending", "数学"),
    )
    app._refresh()
    _select_recovery(app, "old-job")

    assert not app._submit("new_capture", "正在准备下一份…", True)
    app.capture()
    app.open_reviews()
    app.retry_selected()

    assert not submit.called
    assert not app.vm.busy and app.vm.active_operation_id is None
    assert app._review_dialog is None
    assert app.buttons["capture"].cget("state") == "disabled"
    assert app.buttons["review"].cget("state") == "disabled"
    assert app.buttons["retry"].cget("state") == "disabled"


def test_accepted_event_that_clears_page_confirmation_closes_visible_dialog(pending_page_outcome):
    """Any accepted state reset releases a stale page dialog, not merely outcomes."""
    app = object.__new__(LearningAssistantApp)

    class Dialog:
        destroyed = False

        def destroy(self):
            self.destroyed = True

    app.vm = CaptureViewModel()
    app.vm.on_workflow_outcome(pending_page_outcome, active_capture=False)
    dialog = Dialog()
    app._page_subject_dialog = dialog
    app._page_subject_context = ("capture-1", "recovered_job", None, None, app.vm.page_subject_dialog_id)
    app.worker = type("Worker", (), {"preview_events": queue.Queue(), "events": queue.Queue()})()
    app._poll_after_id = None
    app._destroyed = False
    app._refresh = Mock()
    app._schedule_poll = Mock()
    app.vm.begin_work("开始新拍摄", "new-capture-op")
    app.worker.events.put(UiEvent("new_capture_ready", operation_id="new-capture-op", session_generation="fresh"))

    app.poll_events()

    assert dialog.destroyed
    assert app._page_subject_dialog is None
    assert app._page_subject_context is None
    assert not app.vm.page_subject_dialog_needed and not app.vm.busy


def test_page_subject_choice_finishes_without_a_stuck_dialog_or_busy_state(monkeypatch, withdrawn_app, pending_page_outcome):
    """The permitted confirmation still advances normally after modal blocking."""
    app, _ = withdrawn_app
    created = []

    class Dialog:
        def __init__(self, parent, *, on_choose, on_defer):
            self.on_choose = on_choose
            self.on_defer = on_defer
            self.destroyed = False
            created.append(self)

        def show(self, item, index, total):
            self.item = item

        def set_busy(self, busy):
            self.busy = busy

        def destroy(self):
            self.destroyed = True

    monkeypatch.setattr(app_module, "PageSubjectDialog", Dialog)
    app.vm.on_workflow_outcome(pending_page_outcome, active_capture=False)
    app.worker.submit = Mock(return_value="confirm-page-op")
    app._show_page_subject_dialog("capture-1", "recovered_job")
    dialog_id = app.vm.page_subject_dialog_id

    created[0].on_choose(2, "数学")
    app.worker.events.put(UiEvent(
        "workflow_outcome", operation_id="confirm-page-op", dialog_id=dialog_id,
        context="recovered_job", outcome=WorkflowOutcome("capture-1", "completed", "数学"),
    ))
    app.poll_events()

    assert created[0].busy
    assert created[0].destroyed
    assert app._page_subject_dialog is None
    assert not app.vm.busy and not app.vm.page_subject_dialog_needed


def test_page_confirmation_flashes_once_per_pending_set_and_focus_stops_it(pending_page_outcome):
    """Polling an unchanged confirmation cannot restart a flash after user focus."""
    class Notifier:
        def __init__(self):
            self.flashes = []
            self.stops = []

        def flash_if_background(self, handle):
            self.flashes.append(handle)
            return True

        def stop(self, handle):
            self.stops.append(handle)

    app = object.__new__(LearningAssistantApp)
    app.root = Mock()
    app.root.winfo_id.return_value = 42
    app.root.wm_frame.return_value = "0x2a"
    app.vm = CaptureViewModel()
    app.worker = type("Worker", (), {"preview_events": queue.Queue(), "events": queue.Queue()})()
    app._page_subject_dialog = None
    app._page_subject_context = None
    app._taskbar_notifier = Notifier()
    app._page_subject_flash_latches = set()
    app._active_taskbar_flash_handle = None
    app._poll_after_id = None
    app._destroyed = False
    app._refresh = Mock()
    app._schedule_poll = Mock()
    app._show_page_subject_dialog = Mock()

    app.vm.begin_work("正在完成…", "finish")
    app.worker.events.put(UiEvent("workflow_outcome", operation_id="finish", context="recovered_job",
                                  outcome=pending_page_outcome))
    app.poll_events()

    assert app._taskbar_notifier.flashes == [42]

    dialog_id = app.vm.page_subject_dialog_id
    app.vm.begin_work("正在同步…", "sync")
    app.worker.events.put(UiEvent("workflow_outcome", operation_id="sync", dialog_id=dialog_id,
                                  context="recovered_job", outcome=pending_page_outcome))
    app.poll_events()

    assert app._taskbar_notifier.flashes == [42]

    app._on_taskbar_focus()
    assert app._taskbar_notifier.stops == [42]

    app.vm.begin_work("正在同步…", "same-state")
    app.worker.events.put(UiEvent("workflow_outcome", operation_id="same-state", dialog_id=dialog_id,
                                  context="recovered_job", outcome=pending_page_outcome))
    app.poll_events()

    assert app._taskbar_notifier.flashes == [42]

    next_pending = replace(pending_page_outcome,
                           pending_page_subjects=pending_page_outcome.pending_page_subjects[:1])
    app.vm.begin_work("正在同步…", "next-page")
    app.worker.events.put(UiEvent("workflow_outcome", operation_id="next-page", dialog_id=dialog_id,
                                  context="recovered_job", outcome=next_pending))
    app.poll_events()

    assert app._taskbar_notifier.flashes == [42, 42]


def test_root_and_page_dialog_focus_bindings_stop_flash_without_rearming_same_state(monkeypatch, pending_page_outcome):
    """Both windows own a real FocusIn binding, while the page-state latch survives focus."""
    class Notifier:
        def __init__(self):
            self.flashes = []
            self.stops = []

        def flash_if_background(self, handle):
            self.flashes.append(handle)
            return True

        def stop(self, handle):
            self.stops.append(handle)

    class BindTarget:
        def __init__(self):
            self.bindings = {}

        def bind(self, sequence, callback, add=None):
            self.bindings.setdefault(sequence, []).append((callback, add))

    class Root(BindTarget):
        def protocol(self, *_):
            pass

        def after(self, *_):
            return "poll"

        def winfo_id(self):
            return 42

    class Dialog:
        def __init__(self, parent, *, on_choose, on_defer):
            self.window = BindTarget()
            self.on_choose = on_choose
            self.on_defer = on_defer

        def show(self, *_):
            pass

        def set_busy(self, *_):
            pass

        def destroy(self):
            pass

    class Worker:
        def __init__(self):
            self.preview_events = queue.Queue()
            self.events = queue.Queue()

        def start(self):
            pass

    monkeypatch.setattr(LearningAssistantApp, "_build", lambda self: None)
    monkeypatch.setattr(LearningAssistantApp, "_refresh", lambda self: None)
    monkeypatch.setattr(app_module, "PageSubjectDialog", Dialog)
    root, worker, notifier = Root(), Worker(), Notifier()
    app = LearningAssistantApp(root, object(), worker, taskbar_notifier=notifier)

    root_handler, root_add = root.bindings["<FocusIn>"][0]
    assert root_add == "+"

    app.vm.begin_work("正在完成…", "finish")
    worker.events.put(UiEvent("workflow_outcome", operation_id="finish", context="recovered_job",
                              outcome=pending_page_outcome))
    app.poll_events()

    dialog_handler, dialog_add = app._page_subject_dialog.window.bindings["<FocusIn>"][0]
    assert dialog_add == "+"
    key = ("capture-1", (2, 4))
    assert notifier.flashes == [42]
    assert key in app._page_subject_flash_latches

    root_handler(None)
    assert notifier.stops == [42]
    assert key in app._page_subject_flash_latches

    app._active_taskbar_flash_handle = 42
    dialog_handler(None)
    assert notifier.stops == [42, 42]
    assert key in app._page_subject_flash_latches

    dialog_id = app.vm.page_subject_dialog_id
    app.vm.begin_work("正在同步…", "sync")
    worker.events.put(UiEvent("workflow_outcome", operation_id="sync", dialog_id=dialog_id,
                              context="recovered_job", outcome=pending_page_outcome))
    app.poll_events()

    assert notifier.flashes == [42]


def test_open_dashboard_uses_injected_local_path_hook(config):
    dashboard = config.knowledge_root / "知识库首页.html"
    dashboard.parent.mkdir(parents=True)
    dashboard.write_text("<html></html>", encoding="utf-8")
    opened = []
    app = object.__new__(LearningAssistantApp)
    app.config = config
    app.vm = CaptureViewModel()
    app._open_path = opened.append
    app._refresh = lambda: None

    app.open_dashboard()

    assert opened == [dashboard]


@dataclass
class _FakePage:
    page_number: int = 1
    path: Path = Path("page_001.jpg")
    sha256: str = "0" * 64


class _FakeSession:
    def __init__(self):
        self.pages = []
        self.current_page_number = 1
        self.finished = False

    def capture(self, frame):
        page = _FakePage(self.current_page_number)
        self.pages.append(page)
        return page

    def retake(self, frame):
        return self.pages[-1]

    def retake_page(self, page_number, frame):
        return self.pages[page_number - 1]

    def next_page(self):
        self.current_page_number += 1
        return self.current_page_number

    def finish(self):
        self.finished = True
        return object()


class _FakeCamera:
    def __init__(self):
        self.released = False

    def read(self):
        return True, object()

    def release(self):
        self.released = True


class _FakeController:
    def __init__(self):
        self.repo = type("Repo", (), {"close": lambda self: None})()
        self.finished_on = None

    def recover_jobs(self):
        return []

    def finish_and_analyze(self, session):
        self.finished_on = threading.get_ident()
        return WorkflowOutcome("job-1", "completed", "数学")

    def confirm_subject(self, job_id, subject):
        return WorkflowOutcome(job_id, "completed", subject)

    def retry_pending(self, job_id):
        return WorkflowOutcome(job_id, "completed", "数学")


def test_worker_routes_page_subject_confirmation(config):
    """The worker forwards the selected page, then returns its next workflow outcome."""
    controller = Mock()
    controller.confirm_page_subject.return_value = WorkflowOutcome(
        "capture-1", "needs_subject_confirmation", None,
    )
    worker = WorkflowWorker(config, lambda: controller)
    worker._controller = controller

    operation = worker.submit("confirm_page_subject", job_id="capture-1",
                              page_number=2, subject="英语", dialog_id="page-dialog-1")
    command = worker._commands.get_nowait()
    worker._process(command)

    controller.confirm_page_subject.assert_called_once_with("capture-1", 2, "英语")
    event = worker.events.get_nowait()
    assert event.operation_id == operation
    assert event.dialog_id == "page-dialog-1"
    assert event.kind == "workflow_outcome"
    assert event.outcome == controller.confirm_page_subject.return_value


def test_split_parent_completion_aggregates_child_documents_without_cross_child_merging(config):
    """A mixed-subject parent has no document of its own, but both child facts count."""
    class Repo:
        def get_job(self, job_id):
            return type("Job", (), {"payload": {}})()
        def get_document(self, document_id):
            documents = {
                "mixed--math": {"questions": [{
                    "question_id": "q-1", "status": "incorrect", "knowledge_points": ["分数"],
                }]},
                "mixed--english": {"questions": [{
                    "question_id": "q-1", "status": "partial", "knowledge_points": ["时态"],
                }]},
            }
            return documents.get(document_id)
        def dashboard_snapshot(self):
            return {"summary": {"pending_count": 2}}

    controller = type("Controller", (), {"repo": Repo()})()
    worker = WorkflowWorker(config, lambda: controller)
    worker._controller = controller

    summary = worker._completion(WorkflowOutcome(
        "mixed-parent", "completed", None,
        child_document_ids=("mixed--math", "mixed--english"),
    ))

    assert summary.question_count == 2
    assert summary.error_count == 2
    assert summary.weak_knowledge_points == ("分数", "时态")
    assert summary.library_pending_count == 2


def test_worker_creates_and_uses_controller_on_one_dedicated_thread(config):
    made_on = []
    events: queue.Queue[UiEvent] = queue.Queue()
    camera = _FakeCamera()
    created = []

    def factory():
        made_on.append(threading.get_ident())
        controller = _FakeController()
        created.append(controller)
        return controller

    worker = WorkflowWorker(
        config,
        factory,
        camera_factory=lambda: camera,
        session_factory=lambda _: _FakeSession(),
        events=events,
    )
    worker.start()
    worker.submit("capture")
    event = events.get(timeout=2)
    worker.submit("finish")
    finished = events.get(timeout=2)
    worker.stop()
    worker.join(timeout=2)

    assert event.kind == "page_captured"
    assert finished.kind == "workflow_outcome"
    assert made_on == [worker.ident]
    assert created[0].finished_on == worker.ident
    assert worker.ident != threading.get_ident()
    assert camera.released


def test_recovery_event_cannot_clear_an_unrelated_active_operation(view_model):
    view_model.begin_work("正在分析", "active-op")
    view_model.apply_event(UiEvent(
        "recovered_job", operation_id=None,
        outcome=WorkflowOutcome("old-job", "pending", "数学"),
    ))
    assert view_model.busy
    assert view_model.active_operation_id == "active-op"
    assert "old-job" in view_model.recovered_tasks


def test_sealed_capture_disables_capture_controls_until_new_or_resumed_session(view_model):
    view_model.on_page_captured(1, Path("page_001.jpg"))
    view_model.begin_work("正在分析", "finish-op")
    view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="finish-op",
        outcome=WorkflowOutcome("job-1", "needs_subject_confirmation", None),
    ))
    assert view_model.sealed
    assert not view_model.can_capture and not view_model.can_retake and not view_model.can_next
    assert view_model.subject_confirmation_needed


def test_completed_capture_can_reset_for_a_new_document_without_losing_history(view_model):
    view_model.on_page_captured(1, Path("page_001.jpg"))
    view_model.begin_work("正在分析", "finish-op")
    view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="finish-op",
        outcome=WorkflowOutcome("job-1", "completed", "数学"),
        completion=CompletionSummary(question_count=5, error_count=1),
    ))
    assert view_model.can_start_new
    assert "job-1" in view_model.recovered_tasks

    view_model.begin_work("正在准备下一份", "new-op")
    accepted = view_model.apply_event(UiEvent(
        "new_capture_ready", operation_id="new-op", session_generation="new-generation"
    ))

    assert accepted
    assert view_model.session_generation == "new-generation"
    assert view_model.session_id is None
    assert view_model.page_number == 1 and view_model.page_paths == ()
    assert view_model.captured_page_count == 0 and view_model.can_capture
    assert not view_model.sealed and not view_model.can_start_new
    assert view_model.frozen_preview_jpeg is None
    assert view_model.completion == CompletionSummary()
    assert "job-1" in view_model.recovered_tasks


def test_worker_new_capture_discards_finished_session_and_uses_a_fresh_one(config):
    events: queue.Queue[UiEvent] = queue.Queue()
    sessions = []

    def make_session(_):
        session = _FakeSession()
        sessions.append(session)
        return session

    worker = WorkflowWorker(config, _FakeController, camera_factory=_FakeCamera,
                            session_factory=make_session, events=events)
    worker.start()
    try:
        worker.submit("capture")
        first = events.get(timeout=2)
        worker.submit("finish", session_generation=first.session_generation, session_id=first.session_id)
        assert events.get(timeout=2).kind == "workflow_outcome"

        worker.submit("new_capture")
        ready = events.get(timeout=2)
        assert ready.kind == "new_capture_ready"
        assert ready.session_generation != first.session_generation
        assert ready.session_id is None

        worker.submit("capture", session_generation=ready.session_generation)
        second = events.get(timeout=2)
        assert second.kind == "page_captured"
        assert second.session_generation == ready.session_generation
        assert len(sessions) == 2 and sessions[0] is not sessions[1]
    finally:
        worker.request_shutdown(); worker.join(timeout=3)


def test_preview_is_frozen_after_capture_and_cleared_only_on_next(view_model):
    view_model.begin_work("拍摄", "capture-op")
    view_model.apply_event(UiEvent("page_captured", operation_id="capture-op", page_number=1,
                                   page_path=Path("page_001.jpg"), preview_jpeg=b"page"))
    assert view_model.frozen_preview_jpeg == b"page"
    view_model.apply_event(UiEvent("preview", preview_jpeg=b"live"))
    assert view_model.frozen_preview_jpeg == b"page"
    view_model.begin_work("下一页", "next-op")
    view_model.apply_event(UiEvent("next_page", operation_id="next-op", page_number=2))
    assert view_model.frozen_preview_jpeg is None
    assert view_model.live_preview_jpeg == b"live"


def test_current_document_summary_excludes_cumulative_library_counts(view_model):
    view_model.begin_work("分析", "finish-op")
    completion = type("Summary", (), {
        "saved_folder": None, "question_count": 2, "error_count": 1,
        "weak_knowledge_points": ("分数",), "review_count": 0,
        "analysis_details_path": Path("detail.md"), "library_pending_count": 99,
    })()
    view_model.apply_event(UiEvent("workflow_outcome", operation_id="finish-op",
                                   outcome=WorkflowOutcome("job-1", "completed", "数学"), completion=completion))
    assert view_model.completion.question_count == 2
    assert view_model.completion.error_count == 1
    assert view_model.completion.library_pending_count == 99


def test_worker_preview_queue_is_bounded_and_shutdown_acknowledges_blocked_analysis(config):
    started = threading.Event()
    cancelled = threading.Event()
    events: queue.Queue[UiEvent] = queue.Queue()

    class BlockingController(_FakeController):
        def finish_and_analyze(self, session):
            started.set()
            cancelled.wait(2)
            return WorkflowOutcome("job-1", "pending", "数学")
        def cancel(self):
            cancelled.set()

    worker = WorkflowWorker(config, BlockingController, camera_factory=_FakeCamera,
                            session_factory=lambda _: _FakeSession(), events=events)
    worker.start()
    worker.submit("capture")
    assert events.get(timeout=2).kind == "page_captured"
    worker.submit("finish")
    assert started.wait(2)
    worker.request_shutdown()
    worker.join(timeout=2)
    kinds = []
    while not events.empty():
        kinds.append(events.get().kind)
    assert worker.preview_events.maxsize == 1
    assert not worker.is_alive() and "shutdown_ack" in kinds


def test_selected_earlier_page_is_sent_as_explicit_retake_target(config):
    events: queue.Queue[UiEvent] = queue.Queue()
    worker = WorkflowWorker(config, _FakeController, camera_factory=_FakeCamera,
                            session_factory=lambda _: _FakeSession(), events=events)
    worker.start()
    worker.submit("capture")
    events.get(timeout=2)
    worker.submit("retake_page", page_number=1)
    event = events.get(timeout=2)
    worker.request_shutdown()
    worker.join(timeout=2)
    assert event.kind == "page_retaken" and event.page_number == 1


def test_stale_workflow_event_is_rejected_without_sealing_active_capture(view_model):
    view_model.begin_work("拍摄", "new-op")
    accepted = view_model.apply_event(UiEvent("workflow_outcome", operation_id="old-op",
                                              outcome=WorkflowOutcome("old", "completed", "数学")))
    assert not accepted
    assert view_model.busy and not view_model.sealed


def test_recovered_job_outcome_never_replaces_active_capture_summary(view_model):
    view_model.completion = type("S", (), {"question_count": 3})()
    view_model.begin_work("重试旧任务", "retry-op")
    accepted = view_model.apply_event(UiEvent("workflow_outcome", operation_id="retry-op",
                                              outcome=WorkflowOutcome("old", "completed", "数学"),
                                              context="recovered_job", target_id="old"))
    assert accepted
    assert not view_model.sealed and view_model.completion.question_count == 3


def test_recovery_page_outcome_updates_confirmation_without_replacing_capture_summary(view_model, pending_page_outcome):
    view_model.completion = CompletionSummary(question_count=3)
    view_model.begin_work("重试旧任务", "retry-op")

    accepted = view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="retry-op", outcome=pending_page_outcome,
        context="recovered_job", target_id="capture-1",
    ))

    assert accepted
    assert view_model.page_subject_dialog_needed
    assert view_model.active_page_subject.page == 2
    assert not view_model.sealed and view_model.completion.question_count == 3


def test_stale_page_confirmation_outcome_cannot_advance_active_page_dialog(view_model, pending_page_outcome):
    view_model.on_workflow_outcome(pending_page_outcome)
    view_model.begin_work("正在继续", "confirm-page-op")

    accepted = view_model.apply_event(UiEvent(
        "workflow_outcome", operation_id="stale-page-op",
        outcome=replace(pending_page_outcome, pending_page_subjects=pending_page_outcome.pending_page_subjects[:1]),
    ))

    assert not accepted
    assert view_model.active_page_subject.page == 2


def test_resume_manifest_restores_cursor_and_selected_page(view_model):
    accepted = view_model.apply_event(UiEvent("capture_resumed", operation_id="resume-op",
                                              page_number=3, page_paths=(Path("p1.jpg"), Path("p2.jpg")),
                                              selected_page_number=2, preview_jpeg=b"p2"), force=True)
    assert accepted
    assert view_model.page_number == 3
    assert view_model.selected_page_number == 2
    assert view_model.page_paths == (Path("p1.jpg"), Path("p2.jpg"))
    assert view_model.frozen_preview_jpeg == b"p2"


def test_earlier_retake_keeps_unfilled_cursor_and_capture_state(view_model):
    view_model.on_page_captured(1, Path("p1.jpg")); view_model.on_next_page(2)
    view_model.on_page_captured(2, Path("p2.jpg")); view_model.on_next_page(3)
    view_model.on_page_retaken(1, Path("p1-new.jpg"))
    assert view_model.page_number == 3 and view_model.can_capture and not view_model.can_next
    assert view_model.selected_page_number == 1 and view_model.page_paths[0] == Path("p1-new.jpg")


def test_needs_subject_is_immediately_reopenable_recovery_task(view_model):
    view_model.on_workflow_outcome(WorkflowOutcome("old-job", "needs_subject_confirmation", None))
    assert "old-job" in view_model.recovered_tasks


def test_withdrawn_widget_buttons_and_preview_resize(withdrawn_app):
    app, _ = withdrawn_app
    app.vm.on_page_captured(1, Path("page_001.jpg"))
    image = Image.new("RGB", (37, 23), "white")
    raw = BytesIO(); image.save(raw, "JPEG")
    app.vm.live_preview_jpeg = raw.getvalue()
    app._refresh()
    assert app.buttons["capture"].cget("state") == "disabled"
    assert app.buttons["next"].cget("state") == "normal"
    assert app.preview.cget("width") == 37
    assert app.preview.cget("height") == 23


def test_enabled_and_disabled_buttons_use_uniform_distinct_visual_states(withdrawn_app):
    app, _ = withdrawn_app

    enabled = [button for button in app.buttons.values() if button.cget("state") == "normal"]
    disabled = [button for button in app.buttons.values() if button.cget("state") == "disabled"]

    assert enabled and disabled
    assert len({(button.cget("background"), button.cget("foreground"), button.cget("cursor"))
                for button in enabled}) == 1
    assert len({(button.cget("background"), button.cget("foreground"), button.cget("cursor"))
                for button in disabled}) == 1

    enabled_background = app.root.winfo_rgb(enabled[0].cget("background"))
    disabled_background = app.root.winfo_rgb(disabled[0].cget("background"))
    assert enabled_background != disabled_background
    assert max(disabled_background) - min(disabled_background) <= 2500
    assert enabled[0].cget("cursor") == "hand2"
    assert disabled[0].cget("cursor") == "arrow"


def test_button_visual_state_moves_with_available_action(withdrawn_app):
    app, _ = withdrawn_app
    enabled_style = (app.buttons["capture"].cget("background"), app.buttons["capture"].cget("foreground"))
    disabled_style = (app.buttons["next"].cget("background"), app.buttons["next"].cget("foreground"))
    assert enabled_style != disabled_style

    app.vm.on_page_captured(1, Path("page_001.jpg"))
    app._refresh()

    assert (app.buttons["capture"].cget("background"), app.buttons["capture"].cget("foreground")) == disabled_style
    assert (app.buttons["next"].cget("background"), app.buttons["next"].cget("foreground")) == enabled_style


def test_capture_button_starts_next_document_after_analysis(withdrawn_app):
    app, _ = withdrawn_app
    app.vm.on_page_captured(1, Path("page_001.jpg"))
    app.vm.on_workflow_outcome(WorkflowOutcome("job-1", "completed", "数学"))
    app._refresh()

    assert app.buttons["capture"].cget("text") == "开始下一份"
    assert app.buttons["capture"].cget("state") == "normal"

    app.buttons["capture"].invoke()
    event = _deliver_worker_result(app)

    assert event.kind == "new_capture_ready"
    assert app.buttons["capture"].cget("text") == "拍下这一页"
    assert app.buttons["capture"].cget("state") == "normal"
    assert app.vm.can_capture


def test_preview_size_fills_available_space_without_distortion_or_small_image_upscale():
    fit = getattr(ui_app, "_fit_preview_size", None)
    assert fit is not None, "responsive preview sizing helper is missing"
    assert fit((1600, 1200), (900, 600)) == (800, 600)
    assert fit((1200, 1600), (900, 600)) == (450, 600)
    assert fit((37, 23), (900, 600)) == (37, 23)


def test_live_preview_uses_responsive_detail_without_full_page_cost():
    import numpy as np

    frame = np.full((1200, 1600, 3), 180, dtype=np.uint8)
    encoded = ui_app._frame_preview(frame)

    with Image.open(BytesIO(encoded)) as preview:
        assert preview.size == (960, 720)


def test_captured_page_preview_keeps_high_detail(tmp_path):
    page = tmp_path / "page.jpg"
    Image.new("RGB", (1600, 1200), "white").save(page)

    with Image.open(BytesIO(ui_app._page_preview(page))) as preview:
        assert preview.size == (1280, 960)


def test_live_and_captured_previews_choose_cost_appropriate_resampling(withdrawn_app):
    app, _ = withdrawn_app
    live = Image.new("RGB", (80, 60), "blue")
    frozen = Image.new("RGB", (80, 60), "pink")
    live_raw = BytesIO(); live.save(live_raw, "JPEG")
    frozen_raw = BytesIO(); frozen.save(frozen_raw, "JPEG")

    app.vm.live_preview_jpeg = live_raw.getvalue()
    app._refresh()
    assert getattr(app, "_preview_resample", None) == Image.Resampling.BILINEAR

    app.vm.frozen_preview_jpeg = frozen_raw.getvalue()
    app._refresh()
    assert app._preview_resample == Image.Resampling.LANCZOS


def test_window_dimensions_never_exceed_a_small_screen():
    dimensions = getattr(ui_app, "_window_dimensions", None)
    assert dimensions is not None, "screen-aware window sizing helper is missing"
    width, height, minimum_width, minimum_height = dimensions(800, 600)
    assert minimum_width <= width <= 800
    assert minimum_height <= height <= 600


def test_preview_does_not_read_camera_while_latest_frame_is_waiting(config):
    class CountingCamera:
        read_count = 0

        def read(self):
            self.read_count += 1
            return True, object()

        def release(self):
            pass

    camera = CountingCamera()
    worker = WorkflowWorker(config, _FakeController, camera_factory=lambda: camera,
                            session_factory=lambda _: _FakeSession(), monotonic=lambda: 10.0)
    worker._camera = camera
    worker.preview_events.put(UiEvent("preview", preview_jpeg=b"pending"))

    worker._preview()

    assert camera.read_count == 0


def test_footer_stays_visible_after_large_preview_and_window_restore(withdrawn_app):
    app, _ = withdrawn_app
    assert hasattr(app, "footer"), "the action bar must have a fixed footer container"
    app.root.geometry("1024x700+0+0")
    app.root.deiconify()
    try:
        image = Image.new("RGB", (1280, 960), "#8fd8ff")
        raw = BytesIO(); image.save(raw, "JPEG")
        app.vm.live_preview_jpeg = raw.getvalue()
        app._refresh(); app.root.update()
        if os.name == "nt":
            app.root.state("zoomed"); app.root.update()
            app.root.state("normal"); app.root.update()
        else:
            app.root.geometry("1400x900+0+0"); app.root.update()
        app.root.geometry("1024x700+0+0"); app.root.update(); app.root.update_idletasks(); app.root.update()

        assert app.footer.winfo_ismapped()
        assert app.footer.winfo_y() + app.footer.winfo_height() <= app.root.winfo_height()
        assert all(button.winfo_ismapped() for button in app.buttons.values())
        assert int(app.preview.cget("width")) >= 520
        assert int(app.preview.cget("height")) >= 390
    finally:
        app.root.withdraw()


def test_preferred_window_gives_preview_most_of_the_workspace(withdrawn_app):
    app, _ = withdrawn_app
    app.root.geometry("1380x900+0+0")
    image = Image.new("RGB", (1280, 960), "#8fd8ff")
    raw = BytesIO(); image.save(raw, "JPEG")
    app.vm.live_preview_jpeg = raw.getvalue()
    app.root.deiconify()
    try:
        app._refresh(); app.root.update(); app.root.update_idletasks(); app.root.update()
        assert int(app.preview.cget("width")) >= 780
        assert int(app.preview.cget("height")) >= 580
        assert app.footer.winfo_y() + app.footer.winfo_height() <= app.root.winfo_height()
    finally:
        app.root.withdraw()


def test_early_large_preview_cannot_push_side_panel_outside_window(withdrawn_app):
    app, _ = withdrawn_app
    assert hasattr(app, "side_panel"), "the responsive layout must expose its side panel"
    app.root.geometry("1024x700+0+0")
    image = Image.new("RGB", (1280, 960), "#8fd8ff")
    raw = BytesIO(); image.save(raw, "JPEG")
    app.vm.live_preview_jpeg = raw.getvalue()
    app._refresh()
    app.root.deiconify()
    try:
        app.root.update()
        assert app.side_panel.winfo_ismapped()
        assert app.side_panel.winfo_rootx() + app.side_panel.winfo_width() <= app.root.winfo_rootx() + app.root.winfo_width()
    finally:
        app.root.withdraw()


def test_primary_button_text_has_child_friendly_readable_contrast(withdrawn_app):
    app, _ = withdrawn_app

    def luminance(color):
        red, green, blue = (value / 65535 for value in app.root.winfo_rgb(color))
        channels = [value / 12.92 if value <= .04045 else ((value + .055) / 1.055) ** 2.4
                    for value in (red, green, blue)]
        return .2126 * channels[0] + .7152 * channels[1] + .0722 * channels[2]

    def contrast(foreground, background):
        light, dark = sorted((luminance(foreground), luminance(background)), reverse=True)
        return (light + .05) / (dark + .05)

    for name in ("capture", "finish"):
        button = app.buttons[name]
        assert contrast(button.cget("foreground"), button.cget("background")) >= 4.5


@pytest.fixture
def withdrawn_app(config, tk_interpreter):
    import tkinter
    root = tkinter.Toplevel(tk_interpreter)
    root.withdraw()
    worker = WorkflowWorker(config, _FakeController, camera_factory=_FakeCamera,
                            session_factory=lambda _: _FakeSession())
    opened = []
    app = LearningAssistantApp(root, config, worker, open_path=opened.append)
    try:
        yield app, opened
    finally:
        worker.request_shutdown()
        worker.join(timeout=3)
        if not app._destroyed:
            app._destroy()


def _deliver_worker_result(app):
    event = app.worker.events.get(timeout=3)
    app.worker.events.put(event)
    app.poll_events()
    return event


def _select_recovery(app, key):
    rows = list(app.pages.get(0, "end"))
    app.pages.selection_clear(0, "end")
    app.pages.selection_set(rows.index(key))
    app._select(None)


@pytest.fixture
def recoverable_b(config):
    import numpy as np
    frame = np.full((1200, 1600, 3), 200, dtype=np.uint8)
    frame[:, ::80] = 40
    frame[::60, :] = 40
    b = CaptureSession(config, "session-b")
    b.capture(frame); b.next_page(); b.capture(frame); b.next_page()
    return b, frame


def test_old_subject_dialog_cannot_mutate_resumed_session_and_matching_result_can(config, recoverable_b, withdrawn_app):
    """A's dialog callback/results must retain A even after B is resumed."""
    app, _ = withdrawn_app
    b, frame = recoverable_b
    recovered = _deliver_worker_result(app)
    assert recovered.kind == "recovered_capture"
    app.capture()
    captured_a = _deliver_worker_result(app)
    app.worker._controller.finish_and_analyze = lambda session: WorkflowOutcome(
        "job-a", "needs_subject_confirmation", None)
    app.finish()
    subject_a = _deliver_worker_result(app)
    dialog_a = app._dialogs["job-a"]
    a_button = next(w for w in dialog_a.winfo_children() if w.cget("text") == "数学")
    app.vm.apply_event(UiEvent("recovered_capture", page_path=b.session_dir))
    app._refresh(); _select_recovery(app, str(b.session_dir)); app.retry_selected()
    resumed_b = _deliver_worker_result(app)
    before = (app.vm.page_number, app.vm.page_paths, app.vm.selected_page_number,
              app.vm.frozen_preview_jpeg, app.vm.sealed, app.vm.completion)
    a_button.invoke()
    confirmed_a = _deliver_worker_result(app)
    assert (app.vm.page_number, app.vm.page_paths, app.vm.selected_page_number,
            app.vm.frozen_preview_jpeg, app.vm.sealed, app.vm.completion) == before
    assert app.vm.recovered_tasks["job-a"].outcome.state == "completed"
    assert captured_a.session_generation == subject_a.session_generation == confirmed_a.session_generation
    assert captured_a.session_id == subject_a.session_id == confirmed_a.session_id
    assert resumed_b.session_generation != confirmed_a.session_generation
    assert resumed_b.session_id == "session-b"
    # A delayed duplicate cannot replace B, including while B has a matching op.
    app.worker._controller.finish_and_analyze = lambda session: WorkflowOutcome("session-b", "completed", "英语")
    app.worker._camera.read = lambda: (True, frame)
    app.capture(); _deliver_worker_result(app)
    app.finish()
    matching_b = app.worker.events.get(timeout=3)
    app.worker.events.put(confirmed_a)
    app.worker.events.put(matching_b)
    app.poll_events()
    assert matching_b.outcome.job_id == "session-b"
    assert matching_b.session_generation == resumed_b.session_generation
    assert app.vm.sealed
    assert app.vm.completion == matching_b.completion


def test_saved_and_selected_recovery_paths_are_visible_and_actions_are_bound(withdrawn_app, tmp_path):
    app, opened = withdrawn_app
    current = tmp_path / "current"; current.mkdir()
    current_details = current / "analysis.md"; current_details.touch()
    recovered = tmp_path / "recovered"; recovered.mkdir()
    recovered_details = recovered / "old-analysis.md"; recovered_details.touch()
    corrupt = tmp_path / "corrupt-session"; corrupt.mkdir()
    app.vm.completion = CompletionSummary(saved_folder=current, analysis_details_path=current_details)
    app.vm.apply_event(UiEvent("recovered_job", outcome=WorkflowOutcome("old", "completed", "数学"),
                               completion=CompletionSummary(recovered, analysis_details_path=recovered_details)))
    app.vm.apply_event(UiEvent("recovered_job", outcome=WorkflowOutcome("broken", "pending", None,
                               error_code="recovery_failed", recovery_path=corrupt)))
    app._refresh()
    assert str(current) in app.saved_path_var.get()
    app.buttons["folder"].invoke(); app.buttons["details"].invoke()
    assert opened == [current, current_details]
    _select_recovery(app, "old")
    assert str(recovered) in app.selected_path_var.get()
    assert str(current) in app.saved_path_var.get()
    app.buttons["folder"].invoke(); app.buttons["details"].invoke()
    assert opened[-2:] == [recovered, recovered_details]
    _select_recovery(app, "broken")
    assert str(corrupt) in app.selected_path_var.get()
    assert app.buttons["details"].cget("state") == "disabled"
    app.buttons["folder"].invoke()
    assert opened[-1] == corrupt
    app.vm.apply_event(UiEvent("recovered_job", outcome=WorkflowOutcome("missing", "pending", "英语")))
    app._refresh(); _select_recovery(app, "missing")
    assert app.buttons["folder"].cget("state") == "disabled"
    assert app.buttons["details"].cget("state") == "disabled"
    app.open_folder(); app.open_details()
    assert opened[-1] == corrupt


def test_saved_folder_visible_immediately_after_capture_and_empty_actions_disabled(withdrawn_app, tmp_path):
    app, opened = withdrawn_app
    assert app.buttons["folder"].cget("state") == "disabled"
    assert app.buttons["details"].cget("state") == "disabled"
    assert app.buttons["dashboard"].cget("state") == "disabled"
    page = tmp_path / "page_001.jpg"
    Image.new("RGB", (40, 30), "white").save(page)
    app.vm.begin_work("capture", "capture-op")
    app.worker.events.put(UiEvent("page_captured", "capture-op", page_number=1,
                                 page_path=page, session_generation=app.vm.session_generation,
                                 session_id="current"))
    app.poll_events()
    assert str(tmp_path) in app.saved_path_var.get()
    assert app.buttons["folder"].cget("state") == "normal"
    app.buttons["folder"].invoke()
    assert opened == [tmp_path]


def test_worker_resume_command_gets_new_generation_and_its_own_session_id(config, recoverable_b):
    b, _ = recoverable_b
    worker = WorkflowWorker(config, _FakeController, camera_factory=_FakeCamera,
                            session_factory=lambda _: _FakeSession())
    worker.start()
    try:
        assert worker.events.get(timeout=3).kind == "recovered_capture"
        worker.submit("capture")
        a = worker.events.get(timeout=3)
        worker.submit("resume_capture", job_id=str(b.session_dir))
        resumed = worker.events.get(timeout=3)
        assert resumed.session_id == "session-b"
        assert resumed.session_generation != a.session_generation
        worker.submit("next", session_generation=a.session_generation, session_id=a.session_id)
        stale = worker.events.get(timeout=3)
        assert stale.kind == "worker_error"
        assert worker._session.current_page_number == 3
    finally:
        worker.request_shutdown(); worker.join(timeout=3)


def test_unknown_subject_completion_exposes_its_durable_spool_folder(config, tmp_path):
    from types import SimpleNamespace
    from qingzi_learning.storage.repository import KnowledgeRepository, WorkflowJob
    folder = config.spool_root / "unknown-subject"
    folder.mkdir(parents=True)
    repo = KnowledgeRepository(config)
    try:
        repo.save_workflow_job(WorkflowJob("unknown-subject", "needs_subject_confirmation", None,
                                         False, None, {"session_dir": str(folder)}))
        worker = WorkflowWorker(config, _FakeController)
        worker._controller = SimpleNamespace(repo=repo)
        result = worker._completion(WorkflowOutcome("unknown-subject", "needs_subject_confirmation", None))
        assert result.saved_folder == folder
    finally:
        repo.close()


def test_matching_failed_resume_reports_error_and_restores_actions_without_adopting_target(withdrawn_app, tmp_path):
    app, _ = withdrawn_app
    app.capture(); _deliver_worker_result(app)
    before = (app.vm.session_generation, app.vm.session_id, app.vm.page_number, app.vm.page_paths,
              app.vm.selected_page_number, app.vm.frozen_preview_jpeg, app.vm.sealed, app.vm.completion)
    bad_path = tmp_path / "missing-session-sensitive-marker"
    app.vm.apply_event(UiEvent("recovered_capture", page_path=bad_path))
    app._refresh(); _select_recovery(app, str(bad_path)); app.retry_selected()
    operation_id = app.vm.active_operation_id
    assert app.vm.busy and not app.vm.can_capture and not app.vm.can_next and not app.vm.can_finish
    error = _deliver_worker_result(app)
    assert error.operation_id == operation_id and error.kind == "worker_error"
    assert error.session_generation != before[0]
    assert app.vm.error_message == error.message and error.message
    assert app.vm.status == error.message
    assert "sensitive-marker" not in app.vm.error_message
    assert not app.vm.busy and not app.vm.worker_failed
    assert app.vm.can_retake and app.vm.can_next and app.vm.can_finish
    assert not app.vm.can_capture
    assert app.buttons["next"].cget("state") == "normal"
    assert app.buttons["retry"].cget("state") == "normal"
    assert (app.vm.session_generation, app.vm.session_id, app.vm.page_number, app.vm.page_paths,
            app.vm.selected_page_number, app.vm.frozen_preview_jpeg, app.vm.sealed, app.vm.completion) == before
