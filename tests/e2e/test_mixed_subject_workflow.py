"""Synthetic acceptance through the real chain, replacing only the CLI process."""
from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path
import subprocess

import numpy as np
from PIL import Image
import pytest

from qingzi_learning.analysis.codex_cli import CodexCliAnalyzer
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig
from qingzi_learning.export.publication import PublicationCoordinator
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.workflow.controller import WorkflowController
from qingzi_learning.ui.app import LearningAssistantApp, WorkflowWorker
from qingzi_learning.ui.taskbar import TaskbarNotifier


FIXTURES = Path(__file__).parents[1] / "fixtures" / "analysis"
MATH_POINTS = {"同分母分数加法", "长方形面积", "小数乘整数", "米与厘米换算"}
ENGLISH_POINTS = {"第三人称单数", "规则名词复数"}


class FixtureProcess:
    def __init__(self, payload, page_count):
        self.payload, self.page_count, self.calls = payload, page_count, 0

    def run(self, args, **kwargs):
        self.calls += 1
        assert args.count("--image") == self.page_count
        assert kwargs["shell"] is False
        assert args[args.index("--sandbox") + 1] == "read-only"
        output = Path(args[args.index("--output-last-message") + 1])
        output.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def app_harness(tmp_path, monkeypatch):
    monkeypatch.setattr("qingzi_learning.analysis.codex_cli.resolve_codex_cli",
                        lambda: "C:/offline-fixture/codex.cmd")
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 0xBC15, 0x2C1B,
                       tmp_path / "spool", tmp_path / "data")
    repo = KnowledgeRepository(config)
    yield config, repo
    repo.close()


def capture_fixture(config, payload, page_count):
    session = CaptureSession(config, payload["document_id"])
    rng = np.random.default_rng(29)
    for number in range(page_count):
        if number:
            session.next_page()
        session.capture(rng.integers(40, 220, (1200, 1600, 3), dtype=np.uint8))
    return session


def facts(repo):
    """Compare persisted rows, including statistics, without relying on row counts alone."""
    return {
        table: sorted(tuple(row) for row in repo.connection.execute(f"SELECT * FROM {table}"))
        for table in ("documents", "pages", "questions", "question_knowledge_points", "knowledge_stats")
    }


def assert_split_published(config, repo, controller, outcome, original_hashes):
    assert outcome.state == "completed"
    assert outcome.subject is None
    assert not outcome.pending_page_subjects
    assert outcome.child_document_ids == ("six-page-mixed--math", "six-page-mixed--english")
    assert repo.get_document(outcome.job_id) is None
    assert repo.connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 2
    assert repo.connection.execute("SELECT count(*) FROM questions").fetchone()[0] == 6
    dashboard = outcome.dashboard_path.read_text("utf-8")
    for child_id, subject, pages, points, other_points in (
        ("six-page-mixed--math", "数学", [1, 2, 3, 4], MATH_POINTS, ENGLISH_POINTS),
        ("six-page-mixed--english", "英语", [5, 6], ENGLISH_POINTS, MATH_POINTS),
    ):
        document = repo.get_document(child_id)
        assert document["subject"] == subject
        assert [page["page_number"] for page in document["pages"]] == pages
        assert [(q["question_id"], q["page"]) for q in document["questions"]] == [
            (str(page), page) for page in pages
        ]
        assert {point for q in document["questions"] for point in q["knowledge_points"]} == points
        snapshot = repo.export_subject_snapshot(subject)
        assert {point["knowledge_point"] for point in snapshot["knowledge_points"]} == points
        for page in document["pages"]:
            path = Path(page["path"])
            assert path.is_relative_to(config.knowledge_root / subject)
            assert path.name == f"page_{page['page_number']:03d}.jpg"
            assert sha256(path.read_bytes()).hexdigest() == page["sha256"] == original_hashes[page["page_number"]]
            with Image.open(path) as image:
                image.verify()
        note = controller.markdown.document_path(subject, child_id).read_text("utf-8")
        assert all(point in note for point in points)
        assert all(point not in note for point in other_points)
        assert child_id in dashboard
        for point in points:
            assert repo.get_knowledge_stats(subject, point).exposure_count == 1
    assert PublicationCoordinator(repo).current()


def test_six_page_batch_auto_splits_math_and_english(app_harness):
    config, repo = app_harness
    payload = json.loads((FIXTURES / "mixed_subject_analysis.json").read_text("utf-8"))
    session = capture_fixture(config, payload, 6)
    original_hashes = {p.page_number: sha256(p.path.read_bytes()).hexdigest() for p in session.pages}
    runner = FixtureProcess(payload, 6)
    controller = WorkflowController(config, CodexCliAnalyzer(runner), repo)

    outcome = controller.finish_and_analyze(session)

    assert_split_published(config, repo, controller, outcome, original_hashes)
    assert runner.calls == 1
    before = facts(repo)
    for _ in range(2):
        assert controller.finish_and_analyze(session).state == "completed"
        for job_id in (outcome.job_id, *outcome.child_document_ids):
            assert controller.retry_pending(job_id).state == "completed"
    assert facts(repo) == before
    assert runner.calls == 1
    assert_split_published(config, repo, controller, outcome, original_hashes)


def test_last_uncertain_page_confirmation_resumes_and_publishes_cached_analysis(app_harness):
    config, repo = app_harness
    payload = json.loads((FIXTURES / "mixed_subject_analysis.json").read_text("utf-8"))
    payload["page_subjects"][1].update(suggested_subject="语文", confidence=0.4, needs_confirmation=True)
    payload["page_subjects"][5].update(confidence=0.4, needs_confirmation=True, reason="页面题材混合，需确认")
    session = capture_fixture(config, payload, 6)
    hashes = {p.page_number: p.sha256 for p in session.pages}
    runner = FixtureProcess(payload, 6)
    controller = WorkflowController(config, CodexCliAnalyzer(runner), repo)
    first = controller.finish_and_analyze(session)
    assert first.state == "needs_subject_confirmation"
    assert [p.page for p in first.pending_page_subjects] == [2]
    second = controller.confirm_page_subject(first.job_id, 2, "数学")
    assert [p.page for p in second.pending_page_subjects] == [6]
    assert repo.connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
    restarted = WorkflowController(config, CodexCliAnalyzer(runner), repo)
    recovered = {o.job_id: o for o in restarted.recover_jobs()}[first.job_id]
    assert [p.page for p in recovered.pending_page_subjects] == [6]

    # No extra Finish/Retry click: the last confirmation must complete publication.
    final = restarted.confirm_page_subject(first.job_id, 6, "英语")

    assert_split_published(config, repo, restarted, final, hashes)
    assert repo.get_job(final.job_id).payload["page_subject_overrides"] == {"2": "数学", "6": "英语"}
    assert runner.calls == 1


def test_persisted_legacy_cache_recovers_with_whole_batch_confirmation_and_no_model_call(app_harness):
    config, repo = app_harness
    legacy = json.loads((FIXTURES / "unmarked_math.json").read_text("utf-8"))
    legacy.pop("page_subjects")
    legacy["subject_confidence"] = 0.4
    session = capture_fixture(config, legacy, 2)
    hashes = {p.page_number: p.sha256 for p in session.pages}
    session.finish()
    runner = FixtureProcess(legacy, 2)
    controller = WorkflowController(config, CodexCliAnalyzer(runner), repo)
    controller.recover_jobs()  # Public recovery stages a finished capture without analysis.
    staged = repo.get_job(legacy["document_id"])
    analyzing = replace(staged, state="analyzing")
    repo.save_workflow_job(analyzing)
    # Simulate the durable journal written by the pre-page-subject application.
    old_payload = {key: value for key, value in staged.payload.items() if key not in {
        "confirmation_mode", "page_subject_overrides", "split_plan", "child_document_ids", "publish_state"
    }}
    old_payload["analysis"] = legacy
    repo.save_workflow_job(replace(analyzing, state="needs_subject_confirmation", payload=old_payload))
    repo.close()

    reopened = KnowledgeRepository(config)
    try:
        restarted = WorkflowController(config, CodexCliAnalyzer(runner), reopened)
        recovered = {o.job_id: o for o in restarted.recover_jobs()}[legacy["document_id"]]
        assert recovered.state == "needs_subject_confirmation"
        assert recovered.pending_page_subjects == () and recovered.child_document_ids == ()
        cached = reopened.get_job(recovered.job_id)
        assert cached.payload["confirmation_mode"] == "legacy_batch"
        assert cached.payload["analysis"] == legacy
        assert "page_subjects" not in cached.payload["analysis"]
        assert runner.calls == 0
        assert reopened.get_document(recovered.job_id) is None
        with pytest.raises(ValueError, match="页面科目确认"):
            restarted.confirm_page_subject(recovered.job_id, 1, "数学")

        completed = restarted.confirm_subject(recovered.job_id, "数学")

        assert completed.state == "completed" and completed.child_document_ids == ()
        assert completed.analysis_markdown.exists() and completed.dashboard_path.exists()
        document = reopened.get_document(completed.job_id)
        assert [p["page_number"] for p in document["pages"]] == [1, 2]
        assert all(sha256(Path(p["path"]).read_bytes()).hexdigest() == hashes[p["page_number"]]
                   for p in document["pages"])
        assert reopened.get_knowledge_stats("数学", "同分母分数加法").exposure_count == 2
        before = facts(reopened)
        restarted.retry_pending(completed.job_id)
        restarted.retry_pending(completed.job_id)
        assert facts(reopened) == before and runner.calls == 0
        assert PublicationCoordinator(reopened).current()
    finally:
        reopened.close()


@pytest.fixture
def confirmation_app(app_harness, tk_interpreter, monkeypatch):
    """Real controller, worker dispatch, Tk app/dialog; only external CLI and thread scheduling are replaced."""
    import tkinter
    config, repo = app_harness
    payload = json.loads((FIXTURES / "mixed_subject_analysis.json").read_text("utf-8"))
    payload["page_subjects"] = [payload["page_subjects"][0], dict(payload["page_subjects"][-1], page=2)]
    payload["questions"] = [payload["questions"][0], dict(payload["questions"][-1], page=2)]
    for assignment in payload["page_subjects"]:
        assignment.update(confidence=.4, needs_confirmation=True)
    runner = FixtureProcess(payload, 2)
    controller = WorkflowController(config, CodexCliAnalyzer(runner), repo)
    session = capture_fixture(config, payload, 2)
    first = controller.finish_and_analyze(session)
    worker = WorkflowWorker(config, lambda: controller)
    worker._controller = controller
    monkeypatch.setattr(worker, "start", lambda: None)
    root = tkinter.Toplevel(tk_interpreter)
    root.withdraw()
    app = LearningAssistantApp(root, config, worker, taskbar_notifier=TaskbarNotifier(platform="linux"))
    try:
        yield app, controller, runner, session, first
    finally:
        app._destroy()


def _process_ui_command(app):
    command = app.worker._commands.get_nowait()
    app.worker._process(command)
    app.poll_events()
    return command


def test_final_override_crash_recovers_through_ui_retry_without_legacy_dialog(confirmation_app, monkeypatch):
    app, controller, runner, _, first = confirmation_app
    controller.confirm_page_subject(first.job_id, 1, "数学")
    original = controller._page_split_plan

    class PowerLoss(BaseException):
        pass

    def interrupted(job):
        plan = original(job)
        if job.payload["page_subject_overrides"].get("2") == "英语":
            raise PowerLoss
        return plan

    with monkeypatch.context() as patch:
        patch.setattr(controller, "_page_split_plan", interrupted)
        with pytest.raises(PowerLoss):
            controller.confirm_page_subject(first.job_id, 2, "英语")
    app.worker._recover()
    app.poll_events()
    rows = list(app.pages.get(0, "end"))
    app.pages.selection_set(rows.index(first.job_id))
    app.retry_selected()

    assert app._dialogs == {}, "All page overrides are durable; do not offer legacy batch subject choice"
    command = _process_ui_command(app)
    assert command.kind == "retry"
    assert controller.repo.get_job(first.job_id).state == "completed"
    assert runner.calls == 1
    assert not app.vm.busy and app._page_subject_dialog is None


@pytest.mark.parametrize("reopen_dialog", [False, True])
def test_deferred_inflight_confirmation_finishes_own_operation_without_mutating_dialog(confirmation_app, reopen_dialog):
    app, controller, _, _, first = confirmation_app
    app.vm.on_workflow_outcome(first, active_capture=False)
    app._show_page_subject_dialog(first.job_id, "recovered_job")
    app._page_subject_dialog.subject_buttons[1].invoke()
    operation = app.vm.active_operation_id
    old_dialog_id = app.vm.page_subject_dialog_id
    app._page_subject_dialog.close()
    assert app.vm.busy and app.vm.active_operation_id == operation
    if reopen_dialog:
        app.vm.on_workflow_outcome(first, active_capture=False)
        app._show_page_subject_dialog(first.job_id, "recovered_job")
        reopened = app._page_subject_dialog
        new_dialog_id = app.vm.page_subject_dialog_id
        assert new_dialog_id != old_dialog_id
    _process_ui_command(app)

    assert not app.vm.busy and app.vm.active_operation_id is None
    assert controller.repo.get_job(first.job_id).payload["page_subject_overrides"] == {"1": "数学"}
    assert [p.page for p in app.vm.recovered_tasks[first.job_id].outcome.pending_page_subjects] == [2]
    if reopen_dialog:
        assert app._page_subject_dialog is reopened
        assert app.vm.page_subject_dialog_id == new_dialog_id
        assert app.vm.active_page_subject.page == 1
        assert all(button.cget("state") == "normal" for button in reopened.subject_buttons)
    else:
        assert app._page_subject_dialog is None and not app.vm.page_subject_dialog_needed


def test_real_page_confirmation_header_keeps_total_after_each_saved_choice(confirmation_app):
    app, _, runner, _, first = confirmation_app
    app.vm.on_workflow_outcome(first, active_capture=False)
    app._show_page_subject_dialog(first.job_id, "recovered_job")
    assert "第 1/2 张待确认页面" in app._page_subject_dialog.header_var.get()
    app._page_subject_dialog.subject_buttons[1].invoke()
    _process_ui_command(app)
    assert "第 2/2 张待确认页面" in app._page_subject_dialog.header_var.get()
    assert app.vm.active_page_subject.page == 2
    assert runner.calls == 1


@pytest.mark.parametrize("already_open", [False, True])
def test_startup_opens_page_confirmation_and_queues_other_jobs(confirmation_app, already_open):
    app, controller, runner, _, first = confirmation_app
    second_payload = dict(runner.payload, document_id="zz-second-mixed")
    second_runner = FixtureProcess(second_payload, 2)
    second_controller = WorkflowController(app.config, CodexCliAnalyzer(second_runner), controller.repo)
    second = second_controller.finish_and_analyze(capture_fixture(app.config, second_payload, 2))
    if already_open:
        app.vm.on_workflow_outcome(first, active_capture=False)
        app._show_page_subject_dialog(first.job_id, "recovered_job")
        active_dialog = app._page_subject_dialog
    app.worker._recover()
    app.poll_events()

    assert app._page_subject_dialog is not None
    assert app._page_subject_context[0] == first.job_id
    if already_open:
        assert app._page_subject_dialog is active_dialog
    app._page_subject_dialog.close()
    app.poll_events()
    assert app._page_subject_dialog is not None
    assert app._page_subject_context[0] == second.job_id
    assert app.vm.active_page_subject.page == 1
    assert not app.vm.sealed
    assert runner.calls == second_runner.calls == 1
