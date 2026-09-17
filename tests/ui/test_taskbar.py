import ctypes
import sys

import pytest

from qingzi_learning.ui.taskbar import TaskbarNotifier
from qingzi_learning.ui.app import LearningAssistantApp


@pytest.mark.skipif(sys.platform != "win32", reason="Native Windows HWND contract")
def test_app_taskbar_handle_is_native_top_level_window():
    """FlashWindowEx needs the taskbar-owning wrapper, not Tk's inner child."""
    import tkinter as tk
    from ctypes import wintypes
    from types import SimpleNamespace

    root = tk.Tk()
    try:
        root.update()
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        user32.GetAncestor.argtypes = [wintypes.HWND, wintypes.UINT]
        user32.GetAncestor.restype = wintypes.HWND
        expected = user32.GetAncestor(root.winfo_id(), 2)  # GA_ROOT
        assert expected != root.winfo_id()  # Exercise the Windows Tk wrapper.
        assert LearningAssistantApp._root_window_handle(SimpleNamespace(root=root)) == expected
    finally:
        root.destroy()


class FakeUser32:
    def __init__(self):
        self.foreground = None
        self.calls = []

    def GetForegroundWindow(self):
        return self.foreground

    def FlashWindowEx(self, info):
        self.calls.append(info._obj)
        return True


@pytest.fixture
def fake_api():
    return FakeUser32()


def test_background_window_flashes_taskbar_only(fake_api):
    """A background confirmation signals the taskbar without activating its window."""
    fake_api.foreground = 99
    notifier = TaskbarNotifier(fake_api, platform="win32")

    assert notifier.flash_if_background(42)
    info = fake_api.calls[-1]
    assert info.hwnd == 42
    assert info.dwFlags == 0x0000000E
    assert info.uCount == 0
    assert info.dwTimeout == 0


def test_foreground_window_does_not_flash(fake_api):
    fake_api.foreground = 42
    notifier = TaskbarNotifier(fake_api, platform="win32")

    assert not notifier.flash_if_background(42)
    assert fake_api.calls == []


def test_stop_cancels_an_existing_taskbar_flash(fake_api):
    notifier = TaskbarNotifier(fake_api, platform="win32")

    notifier.stop(42)

    assert fake_api.calls[-1].dwFlags == 0


def test_non_windows_is_a_noop(fake_api):
    notifier = TaskbarNotifier(fake_api, platform="linux")

    assert not notifier.flash_if_background(42)
    notifier.stop(42)
    assert fake_api.calls == []


def test_unavailable_win32_api_degrades_to_a_noop(monkeypatch):
    """Packaging or a test host without user32 must still import the UI safely."""
    monkeypatch.delattr(ctypes, "WinDLL", raising=False)

    notifier = TaskbarNotifier(platform="win32")

    assert not notifier.flash_if_background(42)
