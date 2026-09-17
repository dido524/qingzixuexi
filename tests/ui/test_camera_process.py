"""Actual spawn-process probes; all device behavior is synthetic and test-only."""
from functools import partial
import multiprocessing as mp
import queue
import threading
import time
from io import BytesIO
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from qingzi_learning.camera.devices import CameraBusy
from qingzi_learning.config import AppConfig
from qingzi_learning.ui.app import WorkflowWorker
from qingzi_learning.ui.camera_process import CameraProcess


@pytest.fixture
def config(tmp_path):
    return AppConfig(knowledge_root=tmp_path / "knowledge", subjects=("语文", "数学", "英语"),
                     camera_vid=0xBC15, camera_pid=0x2C1B, spool_root=tmp_path / "spool",
                     app_data_root=tmp_path / "data")


def _block_at_camera_boundary(phase, entered, config, commands, replies):
    # This runs the real helper request/reply loop with only the hardware opener
    # replaced. Entering the selected device call is acknowledged explicitly.
    import qingzi_learning.ui.camera_process as camera_module

    class SyntheticCamera:
        def read(self):
            entered.set()
            threading.Event().wait()

        def release(self):
            pass

    def opener(_config):
        if phase == "open":
            entered.set()
            threading.Event().wait()
        return SyntheticCamera()

    camera_module.configured_camera_factory = opener
    camera_module.camera_helper_main(config, commands, replies)


def _stale_reply(entered, config, commands, replies):
    replies.put((None, "ready", True, None))
    command = commands.get()
    assert command[0] == "read"
    entered.set()
    replies.put(("previous-request", "frame", True, np.full((3, 4, 3), 17, dtype=np.uint8)))
    while commands.get() != "stop":
        pass


def _fresh_reply(config, commands, replies):
    replies.put((None, "ready", True, None))
    while True:
        command = commands.get()
        if command == "stop":
            return
        replies.put((command[1], "frame", True, np.full((3, 4, 3), 91, dtype=np.uint8)))


def _assert_released(helper):
    assert helper.closed
    # Process.close cannot succeed on a live process and closes its OS handle.
    assert helper.process._closed
    with pytest.raises(ValueError, match="closed"):
        helper.process.is_alive()
    for channel in (helper.commands, helper.replies):
        assert channel._closed
        if channel._thread is not None:
            assert not channel._thread.is_alive()
        if channel._jointhread is not None:
            assert not channel._jointhread.still_active()


@pytest.mark.parametrize("phase,command", [("open", None), ("preview", None), ("capture", "capture")])
def test_entered_blocked_camera_is_terminated_before_worker_shutdown_ack(config, phase, command):
    entered = mp.get_context("spawn").Event()
    helpers = []
    repo_closed = threading.Event()

    class Controller:
        repo = type("Repo", (), {"close": lambda self: repo_closed.set()})()

        def recover_jobs(self):
            return []

    def factory(cfg):
        helper = CameraProcess(cfg, timeout_seconds=2,
                               target=partial(_block_at_camera_boundary, phase, entered))
        helpers.append(helper)
        return helper

    worker = WorkflowWorker(config, Controller, isolated_camera=True,
                            camera_process_factory=factory, session_factory=lambda _: object())
    if command:
        worker.submit(command)  # Queue before start, so idle preview cannot win.
    worker.start()
    try:
        assert entered.wait(5), f"{phase} did not enter the actual blocking call"
        assert len(helpers) == 1 and helpers[0].process.is_alive()
        worker.request_shutdown()
        worker.join(timeout=8)
        assert not worker.is_alive()
        events = []
        while not worker.events.empty():
            events.append(worker.events.get_nowait())
        assert any(event.kind == "shutdown_ack" for event in events)
        assert repo_closed.is_set()
        _assert_released(helpers[0])
    finally:
        worker.request_shutdown()
        worker.join(timeout=8)
        for helper in helpers:
            helper.release()


def test_stale_camera_reply_cannot_become_next_capture(config):
    entered = mp.get_context("spawn").Event()
    helper = CameraProcess(config, timeout_seconds=2, target=partial(_stale_reply, entered))
    try:
        with pytest.raises(CameraBusy, match="stale"):
            helper.read()
        assert entered.is_set()
        with pytest.raises(CameraBusy):
            helper.read()
        _assert_released(helper)
    finally:
        helper.release()
    # A subsequent camera owner receives only its own newly requested frame.
    fresh = CameraProcess(config, timeout_seconds=2, target=_fresh_reply)
    try:
        ok, frame = fresh.read()
        assert ok and frame.tolist() == [[[91, 91, 91]] * 4] * 3
    finally:
        fresh.release()
    _assert_released(fresh)


def test_released_camera_rejects_read_without_using_closed_ipc(config):
    helper = CameraProcess(config, timeout_seconds=2, target=_fresh_reply)
    helper.read()
    helper.release()
    _assert_released(helper)
    with pytest.raises(CameraBusy):
        helper.read()


def _numbered_fresh_reply(config, commands, replies):
    replies.put((None, "ready", True, None))
    number = 0
    while True:
        command = commands.get()
        if command == "stop":
            return
        number += 1
        replies.put((command[1], "frame", True, np.full((3, 4, 3), number, dtype=np.uint8)))


@pytest.mark.parametrize("resume_via", ["preview", "capture"])
def test_worker_replaces_timed_out_preview_helper_and_captures_fresh_frame(config, resume_via):
    entered = mp.get_context("spawn").Event()
    helpers = []
    captured = []
    repo_closed = threading.Event()

    class Controller:
        repo = type("Repo", (), {"close": lambda self: repo_closed.set()})()

        def recover_jobs(self):
            return []

    class Session:
        finished = False
        session_dir = config.spool_root / "capture-fresh"

        def capture(self, frame):
            captured.append(frame.copy())
            return SimpleNamespace(page_number=1, path=self.session_dir / "page_001.jpg")

    def factory(cfg):
        target = partial(_block_at_camera_boundary, "preview", entered) if not helpers else _numbered_fresh_reply
        helper = CameraProcess(cfg, timeout_seconds=1, target=target)
        helpers.append(helper)
        return helper

    worker = WorkflowWorker(config, Controller, isolated_camera=True,
                            camera_process_factory=factory, session_factory=lambda _: Session())
    worker.start()
    try:
        assert entered.wait(5)
        deadline = time.monotonic() + 5
        while not helpers[0].closed and time.monotonic() < deadline:
            time.sleep(.01)
        assert helpers[0].closed
        previous_frame_number = 0
        if resume_via == "preview":
            try:
                preview = worker.preview_events.get(timeout=5)
            except queue.Empty:
                pytest.fail("worker kept the closed helper instead of rebuilding preview")
            previous_frame_number = Image.open(BytesIO(preview.preview_jpeg)).getpixel((0, 0))[0]
            assert previous_frame_number > 0
        operation_id = worker.submit("capture")
        event = worker.events.get(timeout=5)
        assert event.kind == "page_captured", "first capture must not reuse the closed preview helper"
        assert event.operation_id == operation_id
        assert event.session_id == "capture-fresh"
        assert len(helpers) == 2
        assert int(captured[0][0, 0, 0]) > previous_frame_number
    finally:
        worker.request_shutdown(); worker.join(timeout=8)
        for helper in helpers:
            helper.release()
    assert not worker.is_alive() and repo_closed.is_set()
    assert any(event.kind == "shutdown_ack" for event in list(worker.events.queue))
    for helper in helpers:
        _assert_released(helper)


def _slow_start(config, commands, replies):
    time.sleep(.6)
    _fresh_reply(config, commands, replies)


def _slow_camera_open(config, commands, replies):
    import qingzi_learning.ui.camera_process as camera_module
    class Camera:
        def read(self):
            return True, np.full((3, 4, 3), 45, dtype=np.uint8)
        def release(self):
            pass
    def opener(config):
        time.sleep(.6)
        return Camera()
    camera_module.configured_camera_factory = opener
    camera_module.camera_helper_main(config, commands, replies)


def test_camera_open_and_warmup_use_startup_budget_not_frame_budget(config):
    helper = CameraProcess(config, timeout_seconds=.2, startup_timeout_seconds=10, target=_slow_camera_open)
    try:
        ok, frame = helper.read()
        assert ok and int(frame[0, 0, 0]) == 45
    finally:
        helper.release()
    _assert_released(helper)


def _failed_start(config, commands, replies):
    replies.put((None, "failed", False, None))


def _never_ready(config, commands, replies):
    threading.Event().wait()


def test_spawn_readiness_has_independent_budget_from_frame_read(config):
    helper = CameraProcess(config, timeout_seconds=.2, startup_timeout_seconds=10, target=_slow_start)
    try:
        ok, frame = helper.read()
        assert ok and int(frame[0, 0, 0]) == 91
    finally:
        helper.release()
    _assert_released(helper)


@pytest.mark.parametrize("target", [_failed_start, _never_ready])
def test_startup_failure_is_bounded_and_releases_all_resources(config, target):
    helper = CameraProcess(config, timeout_seconds=.2, startup_timeout_seconds=1, target=target)
    try:
        with pytest.raises(CameraBusy, match="startup"):
            helper.read()
    finally:
        helper.release()
    _assert_released(helper)


def test_repeated_preview_failures_back_off_with_a_cap_and_shutdown_without_retry(config):
    clock = [100.0]
    attempts = []
    released = []

    class FailingCamera:
        def read(self):
            raise CameraBusy("temporary device failure")

        def release(self):
            released.append(self)

    class Controller:
        repo = type("Repo", (), {"close": lambda self: None})()

        def recover_jobs(self):
            return []

    def factory():
        attempts.append(clock[0])
        return FailingCamera()

    worker = WorkflowWorker(config, Controller, camera_factory=factory, monotonic=lambda: clock[0])
    worker._preview()
    assert worker._camera is None
    for before, due in [(100.24, 100.25), (100.74, 100.75), (101.74, 101.75),
                        (103.74, 103.75), (107.74, 107.75), (111.74, 111.75)]:
        count = len(attempts)
        clock[0] = before
        for _ in range(10):
            worker._preview()
        assert len(attempts) == count
        clock[0] = due
        worker._preview()
        assert len(attempts) == count + 1
        assert worker._camera is None
    assert attempts == [100.0, 100.25, 100.75, 101.75, 103.75, 107.75, 111.75]
    assert len(released) == 7
    worker.request_shutdown()
    clock[0] = 1000
    worker._preview()
    worker.start(); worker.join(timeout=2)
    assert not worker.is_alive() and len(attempts) == 7
    assert worker.events.get(timeout=1).kind == "shutdown_ack"
