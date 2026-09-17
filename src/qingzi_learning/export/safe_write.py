"""Guarded atomic writes for reading-layer files beneath one fixed root."""

from __future__ import annotations

import os
from pathlib import Path
import stat
import tempfile
import time


def is_reparse_point(path: Path) -> bool:
    """Return whether an existing path is a symlink or Windows reparse point."""
    try:
        status = path.lstat()
    except FileNotFoundError:
        return False
    attributes = getattr(status, "st_file_attributes", 0)
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return path.is_symlink() or bool(attributes & reparse)


def atomic_write(destination: Path, content: str, root: Path) -> None:
    atomic_write_bytes(destination, content.encode("utf-8"), root)


def atomic_write_bytes(destination: Path, content: bytes, root: Path, *, retry_timeout: float = 1.0) -> None:
    """Atomically replace a guarded file using a fresh exclusive sibling temporary file."""
    if is_reparse_point(root):
        raise ValueError("知识库根目录不能是链接或重解析点")
    root = root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    _guard(destination, root)
    directory = destination.parent
    directory.mkdir(parents=True, exist_ok=True)
    _guard(directory, root)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{destination.stem}-", suffix=".tmp", dir=directory
    )
    temporary = Path(temporary_name)
    try:
        _guard(temporary, root)
        with os.fdopen(descriptor, "wb") as handle:
            descriptor = -1
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        replace_guarded(temporary, destination, root, retry_timeout=retry_timeout)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        if temporary.exists() and not is_reparse_point(temporary):
            temporary.unlink()


def _transient_access_denied(destination: Path) -> bool:
    """Distinguish a replace race from persistent ACL/read-only denial on Windows.

    MoveFileEx can report ACCESS_DENIED when readers deny delete sharing. A
    DELETE-access probe with all sharing enabled identifies that case without
    modifying the file. ACL/read-only/path failures must fail immediately.
    """
    if os.name != "nt" or not destination.is_file():
        return False
    attributes = destination.stat().st_file_attributes
    if attributes & stat.FILE_ATTRIBUTE_READONLY:
        return False
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    handle = create(str(destination), 0x10000, 7, None, 3, 0x00200000, None)
    if handle == wintypes.HANDLE(-1).value:
        return ctypes.get_last_error() in (32, 33)
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    close(handle)
    return True


def replace_guarded(temporary: Path, destination: Path, root: Path, *, retry_timeout: float = 1.0) -> None:
    """Bounded retry only for Windows transient sharing/delete-access races.

    Each attempt rechecks containment and reparse points. The writer has already
    flushed, fsynced and closed its unique temporary file before reaching here.
    """
    deadline = time.monotonic() + max(0, retry_timeout)
    delay = .01
    while True:
        _guard(destination, root)
        _guard(temporary, root)
        try:
            os.replace(temporary, destination)
            return
        except OSError as error:
            code = getattr(error, "winerror", None)
            transient = code in (32, 33) or (code == 5 and _transient_access_denied(destination))
            remaining = deadline - time.monotonic()
            if not transient or remaining <= 0:
                # Persisted/UI diagnostics never expose absolute paths or raw OS text.
                sanitized = OSError(error.errno, f"atomic_replace_failed (winerror={code})")
                if code is not None:
                    sanitized.winerror = code
                raise sanitized from None
            time.sleep(min(delay, remaining))
            delay = min(.1, delay * 2)


def _guard(candidate: Path, root: Path) -> None:
    """Reject lexical escapes plus all links/reparse points on a generated path."""
    try:
        candidate.absolute().relative_to(root.absolute())
    except ValueError as exc:
        raise ValueError("生成路径越界") from exc
    if is_reparse_point(root):
        raise ValueError("知识库根目录不能是链接或重解析点")
    relative = candidate.absolute().relative_to(root.absolute())
    current = root
    for part in relative.parts:
        current = current / part
        if is_reparse_point(current):
            raise ValueError("生成路径不能使用链接或重解析点")
    try:
        candidate.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError("生成路径越界") from exc
