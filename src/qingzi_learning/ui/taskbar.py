"""Small, injectable Win32 taskbar-flash wrapper for background confirmations."""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import sys


_FLASHW_TRAY_TIMER_NO_FOREGROUND = 0x0000000E


class FLASHWINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.UINT),
        ("hwnd", wintypes.HWND),
        ("dwFlags", wintypes.DWORD),
        ("uCount", wintypes.UINT),
        ("dwTimeout", wintypes.DWORD),
    ]


class TaskbarNotifier:
    """Flash only the taskbar button when an application window is in back."""

    def __init__(self, api=None, platform: str = sys.platform) -> None:
        self.platform = platform
        self.api = api if api is not None else self._load_api()

    def _load_api(self):
        if self.platform != "win32":
            return None
        try:
            user32 = ctypes.WinDLL("user32", use_last_error=True)
        except (AttributeError, OSError):
            return None
        user32.GetForegroundWindow.argtypes = []
        user32.GetForegroundWindow.restype = wintypes.HWND
        user32.FlashWindowEx.argtypes = [ctypes.POINTER(FLASHWINFO)]
        user32.FlashWindowEx.restype = wintypes.BOOL
        return user32

    def flash_if_background(self, window_handle: int) -> bool:
        if (self.platform != "win32" or self.api is None
                or self.api.GetForegroundWindow() == window_handle):
            return False
        self.api.FlashWindowEx(ctypes.byref(self._flash_info(window_handle, _FLASHW_TRAY_TIMER_NO_FOREGROUND)))
        return True

    def stop(self, window_handle: int) -> None:
        if self.platform == "win32" and self.api is not None:
            self.api.FlashWindowEx(ctypes.byref(self._flash_info(window_handle, 0)))

    @staticmethod
    def _flash_info(window_handle: int, flags: int) -> FLASHWINFO:
        return FLASHWINFO(ctypes.sizeof(FLASHWINFO), window_handle, flags, 0, 0)
