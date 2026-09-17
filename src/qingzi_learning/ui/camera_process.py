"""Killable, spawn-safe camera boundary.  This process never opens SQLite or files."""
from __future__ import annotations

import multiprocessing as mp
import queue
import time
import threading
from uuid import uuid4
from typing import Callable

from qingzi_learning.camera.devices import (
    CameraBusy, CameraNotFound, enumerate_document_cameras, find_document_camera,
    open_document_camera,
)
from qingzi_learning.config import AppConfig


def configured_camera_factory(config: AppConfig):
    """Top-level, picklable production opener used by Windows ``spawn``."""
    descriptor = find_document_camera(enumerate_document_cameras, config.camera_vid, config.camera_pid)
    return open_document_camera(descriptor)


def camera_helper_main(config: AppConfig, commands, replies) -> None:
    """Own only the capture handle and serialise frames over bounded IPC."""
    camera = None
    try:
        try:
            camera = configured_camera_factory(config)
        except CameraNotFound:
            replies.put((None, "not_found", False, None))
            return
        except Exception:
            replies.put((None, "failed", False, None))
            return
        backend_name = getattr(camera, "getBackendName", None)
        info = {"vid": f"{config.camera_vid:04X}", "pid": f"{config.camera_pid:04X}",
                "backend": backend_name() if callable(backend_name) else "unknown"}
        replies.put((None, "ready", True, info))
        while True:
            command = commands.get()
            if command == "stop": return
            if not isinstance(command, tuple) or command[0] != "read": continue
            request_id = command[1]
            try:
                ok, frame = camera.read()
                replies.put((request_id, "frame", ok, frame))
            except CameraNotFound: replies.put((request_id, "not_found", False, None))
            except Exception: replies.put((request_id, "busy", False, None))
    finally:
        if camera is not None:
            try: camera.release()
            except Exception: pass


class CameraProcess:
    """Bounded client. Termination touches only the isolated camera helper."""
    def __init__(self, config: AppConfig, *, timeout_seconds: float = 1.2,
                 startup_timeout_seconds: float = 10.0,
                 target: Callable[..., None] = camera_helper_main) -> None:
        self.context = mp.get_context("spawn")
        self.commands = self.context.Queue(maxsize=1)
        self.replies = self.context.Queue(maxsize=1)
        self.process = self.context.Process(target=target, args=(config, self.commands, self.replies), daemon=False)
        self.timeout_seconds = timeout_seconds
        self.startup_timeout_seconds = startup_timeout_seconds
        self.ready = False
        self.device_info = {}
        self._cancelled = threading.Event()
        self.invalidated = False
        self.closed = False
        self.process.start()

    def read(self):
        if self.invalidated or self.closed: raise CameraBusy("camera helper is unavailable")
        if not self.ready:
            self._wait_until_ready()
        request_id = uuid4().hex
        try: self.commands.put(("read", request_id), timeout=self.timeout_seconds)
        except queue.Full: raise CameraBusy("camera request busy") from None
        try: response_id, kind, ok, frame = self.replies.get(timeout=self.timeout_seconds)
        except queue.Empty:
            self.invalidated = True; self.release()
            raise CameraBusy("camera read timed out") from None
        # A late frame belongs to an invalid request and must never be reused.
        if response_id != request_id:
            self.invalidated = True; self.release()
            raise CameraBusy("stale camera response")
        if kind == "not_found": raise CameraNotFound("target camera unavailable")
        if kind != "frame" or not ok or frame is None: raise CameraBusy("camera unavailable")
        return True, frame

    def _wait_until_ready(self):
        deadline = time.monotonic() + self.startup_timeout_seconds
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0 or self._cancelled.is_set():
                break
            try:
                response = self.replies.get(timeout=min(.05, remaining))
            except queue.Empty:
                if not self.process.is_alive():
                    break
                continue
            if isinstance(response, tuple) and len(response) == 4 and response[:3] == (None, "ready", True):
                self.device_info = response[3] or {}
                self.ready = True
                return
            if response == (None, "not_found", False, None):
                self.invalidated = True
                self.release()
                raise CameraNotFound("target camera unavailable")
            break
        self.invalidated = True
        self.release()
        raise CameraBusy("camera startup failed or timed out")

    def cancel(self):
        """Thread-safe signal; helper release remains on its owner worker."""
        self._cancelled.set()

    def release(self) -> None:
        if self.closed: return
        try: self.commands.put_nowait("stop")
        except queue.Full: pass
        self.process.join(timeout=self.timeout_seconds)
        if self.process.is_alive(): self.process.terminate(); self.process.join(timeout=self.timeout_seconds)
        self.commands.close(); self.replies.close()
        self.commands.join_thread(); self.replies.join_thread(); self.process.close()
        self.closed = True
