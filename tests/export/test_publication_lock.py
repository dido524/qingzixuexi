"""Real OS coordination: a leftover lock file is not ownership."""
import os
import subprocess
import sys
import threading

import pytest

from qingzi_learning.export.publication import publication_lock


def test_exception_releases_root_lock_for_another_thread(tmp_path):
    with pytest.raises(RuntimeError):
        with publication_lock(tmp_path):
            raise RuntimeError("injected")
    acquired = []
    def run():
        with publication_lock(tmp_path, timeout=1):
            acquired.append(True)
    worker = threading.Thread(target=run)
    worker.start()
    worker.join(3)
    assert not worker.is_alive() and acquired == [True]


def test_process_crash_releases_lock_and_existing_file_does_not_block(tmp_path):
    script = """import sys
from pathlib import Path
from qingzi_learning.export.publication import publication_lock
with publication_lock(Path(sys.argv[1])):
    print('locked', flush=True)
    sys.stdin.read()
"""
    child = subprocess.Popen([sys.executable, "-u", "-c", script, str(tmp_path)], stdin=subprocess.PIPE,
                             stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    ready = []
    reader = threading.Thread(target=lambda: ready.append(child.stdout.readline()), daemon=True)
    reader.start()
    try:
        reader.join(10)
        assert ready == ["locked\n"], "child did not acquire lock"
        with pytest.raises(TimeoutError):
            with publication_lock(tmp_path, timeout=.1):
                pass
        child.terminate()
        child.wait(timeout=5)
        assert (tmp_path / ".publication.lock").exists()
        with publication_lock(tmp_path, timeout=1):
            pass
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
        child.stderr.close()


def test_root_lock_rejects_reparse_destination(tmp_path, monkeypatch):
    from qingzi_learning.export import safe_write
    original = safe_write.is_reparse_point
    monkeypatch.setattr(safe_write, "is_reparse_point", lambda path: path.name == ".publication.lock" or original(path))
    with pytest.raises(ValueError, match="链接"):
        with publication_lock(tmp_path):
            pass


def test_unlock_error_still_releases_local_lock(tmp_path, monkeypatch):
    if os.name == "nt":
        import msvcrt
        original = msvcrt.locking
        def fail_unlock(fd, mode, length):
            if mode == msvcrt.LK_UNLCK:
                raise OSError("injected unlock failure")
            return original(fd, mode, length)
        monkeypatch.setattr(msvcrt, "locking", fail_unlock)
    else:
        import fcntl
        original = fcntl.flock
        def fail_unlock(fd, operation):
            if operation == fcntl.LOCK_UN:
                raise OSError("injected unlock failure")
            return original(fd, operation)
        monkeypatch.setattr(fcntl, "flock", fail_unlock)
    with pytest.raises(OSError):
        with publication_lock(tmp_path):
            pass
    monkeypatch.undo()
    with publication_lock(tmp_path, timeout=.1):
        pass
