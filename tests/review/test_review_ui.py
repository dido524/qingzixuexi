"""Review work must stay on the database owner thread and retain dialog identity."""
import queue
import threading
import weakref
from dataclasses import replace

import pytest

from test_review_service import repo, seed, service
from qingzi_learning.camera.devices import CameraBusy
from qingzi_learning.storage.repository import KnowledgeRepository, WorkflowJob
from qingzi_learning.ui.app import WorkflowWorker, CaptureViewModel, UiEvent, CompletionSummary, LearningAssistantApp
from qingzi_learning.workflow.controller import WorkflowController, WorkflowOutcome


def camera_unavailable():
    raise CameraBusy()


def take_operation(worker, operation):
    while True:
        event = worker.events.get(timeout=15)
        if event.operation_id == operation:
            return event


def test_review_worker_owns_reads_writes_and_returns_updated_pending_and_completion(repo):
    seed(repo, ("needs_review", "needs_review"))
    threads = []
    def factory():
        owned = KnowledgeRepository(repo.config)
        owned.connection.set_trace_callback(lambda _: threads.append(threading.get_ident()))
        return WorkflowController(repo.config, object(), owned)
    worker = WorkflowWorker(repo.config, factory, camera_factory=camera_unavailable)
    worker.start()
    try:
        op = worker.submit("list_reviews", context="review_job", dialog_id="dialog-a")
        listed = take_operation(worker, op)
        assert listed.kind == "review_list" and len(listed.review_items) == 2
        item = listed.review_items[0]
        op = worker.submit("confirm_review", job_id=item.document_id, question_id=item.question_id,
                           final_status="incorrect", corrected_answer="正确答案", note="复核",
                           expected_version=item.version, dialog_id="dialog-a", context="review_job")
        saved = take_operation(worker, op)
        assert saved.kind == "review_saved" and saved.dialog_id == "dialog-a"
        assert saved.outcome.job_id == "doc" and saved.outcome.state == "needs_review"
        assert (saved.completion.error_count, saved.completion.review_count) == (1, 1)
        assert [q.question_id for q in saved.review_items] == ["2"]
        assert set(threads) == {worker.ident}
    finally:
        worker.request_shutdown(); worker.join(timeout=10)
    assert not worker.is_alive()


def test_review_worker_stays_in_new_capture_after_each_confirmation(repo):
    seed(repo, ("needs_review",), document_id="old")
    seed(repo, ("needs_review", "needs_review"), document_id="new")
    worker = WorkflowWorker(repo.config, lambda: WorkflowController(
        repo.config, object(), KnowledgeRepository(repo.config)), camera_factory=camera_unavailable)
    worker.start()
    try:
        listed = take_operation(worker, worker.submit(
            "list_reviews", job_id="new", review_scope_id="new", context="review_job", dialog_id="dialog-new"))
        assert [(item.document_id, item.question_id) for item in listed.review_items] == [
            ("new", "1"), ("new", "2")
        ]
        item = listed.review_items[0]
        saved = take_operation(worker, worker.submit(
            "confirm_review", job_id=item.document_id, question_id=item.question_id,
            final_status="incorrect", corrected_answer="正确答案", note="家长确认",
            expected_version=item.version, review_scope_id="new", context="review_job", dialog_id="dialog-new"))
        assert [(next_item.document_id, next_item.question_id) for next_item in saved.review_items] == [
            ("new", "2")
        ]
    finally:
        worker.request_shutdown(); worker.join(timeout=10)


def test_completion_offers_legacy_all_correct_capture_for_explicit_review(repo):
    seed(repo, ("correct", "correct"), document_id="latest")
    worker = WorkflowWorker(repo.config, lambda: WorkflowController(
        repo.config, object(), KnowledgeRepository(repo.config)), camera_factory=camera_unavailable)
    worker._controller = WorkflowController(repo.config, object(), repo)
    summary = worker._completion(WorkflowOutcome("latest", "completed", "语文"))
    assert summary.question_count == 2
    assert summary.review_count == 2


def test_review_worker_scopes_split_batch_to_its_children(repo):
    seed(repo, ("needs_review",), document_id="old")
    seed(repo, ("needs_review",), document_id="math")
    seed(repo, ("needs_review",), document_id="english")
    repo.save_workflow_job(WorkflowJob(
        "batch", "needs_review", None, False, None,
        {"child_document_ids": ["math", "english"], "pages": [], "archive_kind": None},
    ))
    worker = WorkflowWorker(repo.config, lambda: WorkflowController(
        repo.config, object(), KnowledgeRepository(repo.config)), camera_factory=camera_unavailable)
    worker.start()
    try:
        listed = take_operation(worker, worker.submit(
            "list_reviews", review_scope_id="batch", context="review_job", dialog_id="split-dialog"))
        assert [(item.document_id, item.question_id) for item in listed.review_items] == [
            ("english", "1"), ("math", "1")
        ]
        item = listed.review_items[0]
        saved = take_operation(worker, worker.submit(
            "confirm_review", job_id=item.document_id, question_id=item.question_id,
            final_status="incorrect", corrected_answer="参考答案", note="家长确认",
            expected_version=item.version, review_scope_id="batch",
            context="review_job", dialog_id="split-dialog"))
        assert saved.kind == "review_saved"
        assert saved.outcome.job_id == "batch"
        assert saved.completion.review_count == 1
    finally:
        worker.request_shutdown(); worker.join(timeout=10)


def test_restart_surfaces_latest_legacy_all_correct_capture_for_review(repo, monkeypatch):
    seed(repo, ("needs_review",), document_id="old")
    seed(repo, ("correct", "correct"), document_id="latest")
    repo.config = replace(repo.config, review_all_model_questions=True)
    controller = WorkflowController(repo.config, object(), repo)
    monkeypatch.setattr(controller.publication, "current", lambda: True)

    recovered = {outcome.job_id: outcome for outcome in controller.recover_jobs()}
    assert "latest" in recovered
    assert recovered["latest"].state == "completed"


def test_restart_does_not_prefer_older_legacy_capture_over_new_pending_capture(repo, monkeypatch):
    seed(repo, ("correct", "correct"), document_id="older")
    seed(repo, ("needs_review",), document_id="newer")
    repo.config = replace(repo.config, review_all_model_questions=True)
    controller = WorkflowController(repo.config, object(), repo)
    monkeypatch.setattr(controller.publication, "current", lambda: True)

    recovered = controller.recover_jobs()
    assert [outcome.job_id for outcome in recovered] == ["newer"]


def test_restart_isolates_corrupt_journal_with_all_question_review_enabled(repo, monkeypatch):
    seed(repo, ("needs_review",), document_id="healthy")
    seed(repo, ("correct",), document_id="corrupt")
    repo.connection.execute(
        "UPDATE workflow_jobs SET payload_json = ? WHERE job_id = ?", ("{invalid", "corrupt")
    )
    repo.connection.commit()
    repo.config = replace(repo.config, review_all_model_questions=True)
    controller = WorkflowController(repo.config, object(), repo)
    monkeypatch.setattr(controller.publication, "current", lambda: True)

    recovered = {outcome.job_id: outcome for outcome in controller.recover_jobs()}
    assert recovered["healthy"].state == "needs_review"
    assert recovered["corrupt"].error_code == "recovery_failed"


def test_workflow_job_order_tracks_insertion_when_timestamps_tie(repo):
    repo.save_workflow_job(WorkflowJob("z-older", "completed", "数学", False, None, {}))
    repo.save_workflow_job(WorkflowJob("a-newer", "completed", "数学", False, None, {}))
    assert repo.list_workflow_job_ids() == ["z-older", "a-newer"]


def test_stale_review_result_updates_own_recovery_but_cannot_replace_capture_or_dialog(repo):
    seed(repo, ("needs_review",))
    item = service(repo).list_pending()[0]
    vm = CaptureViewModel()
    vm.session_id = "session-b"
    vm.review_dialog_id = "new-dialog"
    vm.review_items = ()
    vm.completion = CompletionSummary(question_count=5)
    vm.begin_work("new dialog", "new-op")
    stale = UiEvent("review_saved", "old-op", target_id="doc", dialog_id="old-dialog",
                    context="review_job", outcome=WorkflowOutcome("doc", "completed", "语文"),
                    completion=CompletionSummary(question_count=1), review_items=(item,),
                    session_id="session-a", session_generation="generation-a")
    assert not vm.apply_event(stale)
    assert vm.recovered_tasks["doc"].outcome.state == "completed"
    assert vm.completion.question_count == 5 and vm.busy and vm.review_items == ()
    # A response to a dialog closed while saving clears its own busy operation,
    # but does not resurrect the closed dialog or alter a resumed capture.
    vm.begin_work("old save", "old-op")
    assert not vm.apply_event(stale)
    assert not vm.busy and vm.review_items == () and vm.session_id == "session-b"


@pytest.fixture
def review_app(repo, tk_interpreter):
    import tkinter as tk
    seed(repo, ("needs_review", "needs_review"))
    root = tk.Toplevel(tk_interpreter); root.withdraw()
    worker = WorkflowWorker(repo.config, lambda: WorkflowController(repo.config, object(), KnowledgeRepository(repo.config)),
                            camera_factory=camera_unavailable)
    app = LearningAssistantApp(root, repo.config, worker, open_path=lambda _: None)
    try:
        yield app
    finally:
        worker.request_shutdown(); worker.join(timeout=10)
        if not app._destroyed:
            app._destroy()


def deliver(app):
    event = take_operation(app.worker, app.vm.active_operation_id)
    app.worker.events.put(event)
    app.poll_events()
    return event


def test_review_dialog_shows_evidence_requires_choice_and_advances(review_app):
    app = review_app
    app.open_reviews(); deliver(app)
    dialog = app._review_dialog
    assert dialog.item.question_id == "1"
    assert "学生原答案" in dialog.evidence_var.get() and "0.60" in dialog.evidence_var.get()
    assert "系统原理由" in dialog.evidence_var.get() and "needs_review" in dialog.evidence_var.get()
    dialog.save_button.invoke()
    assert not app.vm.busy and "选择" in dialog.message_var.get()
    dialog.status_var.set("incorrect")
    dialog.answer_var.set("更正答案")
    dialog.note_var.set("家长确认")
    dialog.save_button.invoke()
    assert app.vm.busy and dialog.save_button.cget("state") == "disabled"
    saved = deliver(app)
    assert saved.kind == "review_saved"
    assert dialog.item.question_id == "2"
    assert not app.vm.busy and dialog.status_var.get() == ""


def test_closed_dialog_callback_cannot_submit_review_to_new_dialog(review_app):
    app = review_app
    app.open_reviews(); deliver(app)
    old = app._review_dialog
    old_item, old_id = old.item, old.dialog_id
    app.close_reviews(old_id)
    app.open_reviews(); deliver(app)
    assert not app._save_review(old_id, old_item, "incorrect", "", "")
    assert not app.vm.busy


def test_delayed_duplicate_review_event_cannot_regress_recovery_summary(repo):
    vm = CaptureViewModel()
    vm.session_id = "doc"; vm.review_dialog_id = "dialog"
    vm.begin_work("save", "new-op")
    current = UiEvent("review_saved", "new-op", target_id="doc", dialog_id="dialog", review_revision=2,
                      outcome=WorkflowOutcome("doc", "completed", "语文"), completion=CompletionSummary(error_count=0))
    assert vm.apply_event(current)
    stale = replace(current, operation_id="old-op", review_revision=1,
                    outcome=WorkflowOutcome("doc", "needs_review", "语文"), completion=CompletionSummary(error_count=1))
    assert not vm.apply_event(stale)
    assert vm.recovered_tasks["doc"].outcome.state == "completed"
    assert vm.completion.error_count == 0


def test_closed_review_releases_tcl_variables_even_when_callback_retains_dialog(review_app):
    import tkinter as tk
    app = review_app
    app.open_reviews(); deliver(app)
    dialog = app._review_dialog
    variables = [weakref.ref(value) for value in vars(dialog).values() if isinstance(value, tk.Variable)]
    assert variables
    app.close_reviews(dialog.dialog_id)
    # A retained stale callback must not keep Tcl finalizers alive to be collected
    # by an unrelated worker thread later. Destruction belongs to this Tk thread.
    assert all(reference() is None for reference in variables)
    dialog.save()
    assert not app.vm.busy
