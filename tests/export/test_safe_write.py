from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
import os
import threading
import time

import pytest

from qingzi_learning.export import safe_write


def _windows_error(code):
    error = PermissionError(13, "private path must not be exposed")
    error.winerror = code
    return error


@pytest.mark.parametrize("code", [5, 32, 33])
def test_transient_windows_replace_retries_without_partial_content(tmp_path, monkeypatch, code):
    destination = tmp_path / "session.json"
    destination.write_text("old")
    original = os.replace
    attempts = []
    def occupied(source, target):
        attempts.append(source)
        assert destination.read_text() == "old"
        if len(attempts) < 3:
            raise _windows_error(code)
        original(source, target)
    monkeypatch.setattr(os, "replace", occupied)
    safe_write.atomic_write(destination, "new", tmp_path)
    assert destination.read_text() == "new"
    assert len(attempts) == 3 and len(set(attempts)) == 1
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("code", [2, 3, 87, 123])
def test_permanent_replace_error_is_not_retried_and_is_sanitized(tmp_path, monkeypatch, code):
    destination = tmp_path / "session.json"
    destination.write_text("old")
    attempts = []
    def fail(*args):
        attempts.append(1)
        raise _windows_error(code)
    monkeypatch.setattr(os, "replace", fail)
    with pytest.raises(OSError) as caught:
        safe_write.atomic_write(destination, "new", tmp_path)
    assert "private" not in str(caught.value)
    assert len(attempts) == 1 and destination.read_text() == "old"
    assert not list(tmp_path.glob("*.tmp"))


def test_access_denied_on_readonly_file_is_not_retried(tmp_path, monkeypatch):
    destination = tmp_path / "session.json"
    destination.write_text("old")
    destination.chmod(0o444)
    attempts = []
    def fail(*args):
        attempts.append(1)
        raise _windows_error(5)
    monkeypatch.setattr(os, "replace", fail)
    try:
        with pytest.raises(OSError):
            safe_write.atomic_write(destination, "new", tmp_path)
        assert len(attempts) == 1
    finally:
        destination.chmod(0o666)


def test_sharing_violation_has_a_deadline_and_cleans_temp(tmp_path, monkeypatch):
    destination = tmp_path / "state.json"
    destination.write_text("old")
    def fail(*args):
        raise _windows_error(32)
    monkeypatch.setattr(os, "replace", fail)
    start = time.monotonic()
    with pytest.raises(OSError):
        safe_write.atomic_write_bytes(destination, b"new", tmp_path, retry_timeout=.12)
    assert .1 <= time.monotonic() - start < 1
    assert destination.read_text() == "old"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows file sharing contract")
def test_real_windows_reader_without_delete_sharing_releases_then_write_succeeds(tmp_path):
    import ctypes
    from ctypes import wintypes
    destination = tmp_path / "session.json"
    destination.write_text("old")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                   wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    kernel.CreateFileW.restype = wintypes.HANDLE
    kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
    handle = kernel.CreateFileW(str(destination), 0x80000000, 3, None, 3, 0, None)
    assert handle != wintypes.HANDLE(-1).value
    # This OS handle reproducibly causes WinError 5 on os.replace until closed.
    timer = threading.Timer(.15, lambda: kernel.CloseHandle(handle))
    timer.start()
    try:
        safe_write.atomic_write(destination, "new", tmp_path)
    finally:
        timer.join()
    assert destination.read_text() == "new"


def test_retry_rechecks_reparse_target_before_second_attempt(tmp_path, monkeypatch):
    destination = tmp_path / "state.json"
    destination.write_text("old")
    attempts = []
    def fail(*args):
        attempts.append(1)
        raise _windows_error(32)
    monkeypatch.setattr(os, "replace", fail)
    monkeypatch.setattr(safe_write, "is_reparse_point", lambda path: bool(attempts) and path == destination)
    with pytest.raises(ValueError, match="链接"):
        safe_write.atomic_write(destination, "new", tmp_path)
    assert len(attempts) == 1 and destination.read_text() == "old"
    assert not list(tmp_path.glob("*.tmp"))


def test_repeated_concurrent_writers_publish_only_whole_files(tmp_path):
    destination = tmp_path / "session.json"
    payloads = {str(n) * 10000 for n in range(4)}
    safe_write.atomic_write(destination, "0" * 10000, tmp_path)
    barrier = threading.Barrier(4)
    def writer(n):
        barrier.wait()
        for _ in range(30):
            safe_write.atomic_write(destination, str(n) * 10000, tmp_path)
            # Windows can briefly deny new readers while an old version is being
            # unlinked. A successfully opened snapshot must always be whole.
            try:
                snapshot = destination.read_text()
            except PermissionError:
                continue
            assert snapshot in payloads
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(writer, range(4)))
    assert destination.read_text() in payloads
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_ignores_preexisting_predictable_part_name(tmp_path: Path) -> None:
    destination = tmp_path / "知识库首页.html"
    predictable = destination.with_suffix(".html.part")
    predictable.write_text("do not use", encoding="utf-8")

    safe_write.atomic_write(destination, "new content", tmp_path)

    assert destination.read_text("utf-8") == "new content"
    assert predictable.read_text("utf-8") == "do not use"


def test_atomic_write_rejects_destination_marked_as_reparse_point(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    destination = tmp_path / "report.md"
    outside = tmp_path.parent / "outside.md"

    monkeypatch.setattr(safe_write, "is_reparse_point", lambda path: path == destination)

    with pytest.raises(ValueError, match="链接"):
        safe_write.atomic_write(destination, "must not escape", tmp_path)

    assert not outside.exists()
    assert not destination.exists()


def test_atomic_write_rejects_real_destination_symlink_when_supported(tmp_path: Path) -> None:
    destination = tmp_path / "report.md"
    outside = tmp_path.parent / "outside.md"
    try:
        destination.symlink_to(outside)
    except OSError:
        pytest.skip("Windows symlink privilege is unavailable")

    with pytest.raises(ValueError, match="链接"):
        safe_write.atomic_write(destination, "must not escape", tmp_path)

    assert not outside.exists()
