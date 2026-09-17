"""Large, responsive per-page subject confirmation dialog."""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from PIL import Image, ImageTk

try:
    import tkinter as tk
except ImportError:  # pragma: no cover - packaging without Tk is supported.
    tk = None

from qingzi_learning.workflow.subject_split import PendingPageSubject


_COLORS = {
    "bg": "#fff7fb",
    "card": "#ffffff",
    "ink": "#45384f",
    "muted": "#7c6f84",
    "line": "#f0dce7",
    "pink": "#b83a74",
    "pink_dark": "#8f2c5c",
    "pink_soft": "#fde8f1",
    "blue": "#e4f5ff",
    "disabled": "#dedde2",
    "disabled_text": "#5f5c63",
}
_MINIMUM_SIZE = (720, 520)


def dialog_geometry(work_width: int, work_height: int) -> tuple[int, int]:
    """Return an 85% dialog size without demanding more than its work area."""
    width = max(_MINIMUM_SIZE[0] if work_width >= _MINIMUM_SIZE[0] else 1,
                int(work_width * .85))
    height = max(_MINIMUM_SIZE[1] if work_height >= _MINIMUM_SIZE[1] else 1,
                 int(work_height * .85))
    width = min(work_width, width)
    height = min(work_height, height)
    return max(1, width), max(1, height)


def _fit_image_size(source_size: tuple[int, int], bounds: tuple[int, int]) -> tuple[int, int]:
    source_width, source_height = source_size
    bound_width, bound_height = bounds
    if min(source_width, source_height, bound_width, bound_height) <= 0:
        return (1, 1)
    scale = min(bound_width / source_width, bound_height / source_height, 1.0)
    return max(1, round(source_width * scale)), max(1, round(source_height * scale))


def _work_area(parent) -> tuple[int, int, int, int]:
    """Use the Windows taskbar-aware work area when available, with Tk fallback."""
    width, height = parent.winfo_screenwidth(), parent.winfo_screenheight()
    try:  # Keep this module importable and usable on non-Windows test hosts.
        import ctypes
        from ctypes import wintypes

        class RECT(ctypes.Structure):
            _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                       ("right", wintypes.LONG), ("bottom", wintypes.LONG)]

        rect = RECT()
        if ctypes.windll.user32.SystemParametersInfoW(48, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except (AttributeError, OSError):
        pass
    return 0, 0, width, height


class PageSubjectDialog:
    """A single reusable dialog which shows one pending page at a time."""

    def __init__(self, parent, *, on_choose: Callable[[int, str], None],
                 on_defer: Callable[[], None]) -> None:
        if tk is None:  # pragma: no cover
            raise RuntimeError("Tk is unavailable")
        self._parent = parent
        self._on_choose = on_choose
        self._on_defer = on_defer
        self._item: PendingPageSubject | None = None
        self._source_image: Image.Image | None = None
        self._photo: ImageTk.PhotoImage | None = None
        self._render_after_id = None
        self._destroyed = False
        self._deferred = False
        self._busy = False

        self.window = tk.Toplevel(parent, bg=_COLORS["bg"])
        self.window.withdraw()
        self.window.title("确认页面科目 · 晴子学习助手")
        self.window.transient(parent)
        self.window.grid_columnconfigure(0, weight=1)
        self.window.grid_rowconfigure(0, weight=0)
        self.window.grid_rowconfigure(1, weight=1)
        self.window.grid_rowconfigure(2, weight=0)
        self.window.protocol("WM_DELETE_WINDOW", self.close)

        self.header = tk.Frame(self.window, bg=_COLORS["card"], highlightbackground=_COLORS["line"], highlightthickness=1)
        self.header.grid(row=0, column=0, sticky="ew", padx=16, pady=(16, 8))
        self.header.grid_columnconfigure(0, weight=1)
        self.header_var = tk.StringVar(master=self.window)
        tk.Label(self.header, text="请确认这一页的科目", anchor="w", font=("Microsoft YaHei UI", 14, "bold"),
                 fg=_COLORS["ink"], bg=_COLORS["card"]).grid(row=0, column=0, sticky="w", padx=16, pady=(12, 3))
        tk.Label(self.header, textvariable=self.header_var, anchor="w", justify="left", wraplength=900,
                 font=("Microsoft YaHei UI", 10), fg=_COLORS["muted"], bg=_COLORS["card"]).grid(
                     row=1, column=0, sticky="ew", padx=16, pady=(0, 12))

        self.image_frame = tk.Frame(self.window, bg=_COLORS["blue"], highlightbackground="#cbe8f7", highlightthickness=1)
        self.image_frame.grid(row=1, column=0, sticky="nsew", padx=16, pady=(0, 8))
        self.image_frame.grid_columnconfigure(0, weight=1)
        self.image_frame.grid_rowconfigure(0, weight=1)
        self.image_canvas = tk.Canvas(self.image_frame, bg=_COLORS["blue"], highlightthickness=0, borderwidth=0)
        self.image_canvas.grid(row=0, column=0, sticky="nsew")
        self.image_canvas.bind("<Configure>", self._on_configure)

        self.footer = tk.Frame(self.window, bg=_COLORS["card"], highlightbackground=_COLORS["line"], highlightthickness=1)
        self.footer.grid(row=2, column=0, sticky="ew", padx=16, pady=(0, 16))
        self.subject_buttons = []
        for column, subject in enumerate(("语文", "数学", "英语")):
            self.footer.grid_columnconfigure(column, weight=1, uniform="page-subject")
            button = tk.Button(self.footer, text=subject, command=lambda value=subject: self._choose(value),
                               font=("Microsoft YaHei UI", 12, "bold"), padx=12, pady=11,
                               relief="solid", borderwidth=1)
            button.grid(row=0, column=column, sticky="ew", padx=6, pady=10)
            self.subject_buttons.append(button)
        self.set_busy(False)

    def show(self, item: PendingPageSubject, index: int, total: int) -> None:
        """Populate and center the dialog for one durable pending page."""
        if self._destroyed:
            return
        self._item = item
        self._source_image = self._load_image(item.image_path)
        confidence = f"{item.confidence:.0%}"
        self.header_var.set(
            f"第 {index}/{total} 张待确认页面  ·  建议：{item.suggested_subject.value}"
            f"  ·  置信度：{confidence}\n判断依据：{item.reason or '未提供'}"
        )
        self._position()
        self.window.deiconify()
        self.window.lift()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass
        self._queue_render()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        state = "disabled" if busy else "normal"
        for button in self.subject_buttons:
            if busy:
                button.configure(state=state, fg=_COLORS["disabled_text"], bg=_COLORS["disabled"],
                                 activeforeground=_COLORS["disabled_text"], activebackground=_COLORS["disabled"],
                                 disabledforeground=_COLORS["disabled_text"], cursor="arrow")
            else:
                button.configure(state=state, fg="white", bg=_COLORS["pink"],
                                 activeforeground="white", activebackground=_COLORS["pink_dark"],
                                 disabledforeground=_COLORS["disabled_text"], cursor="hand2")

    def close(self) -> None:
        """Defer without confirming; controller state remains untouched."""
        if self._destroyed or self._deferred:
            return
        self._deferred = True
        self._release_grab()
        self._on_defer()
        self.destroy()

    def hide(self) -> None:
        """Make the dialog non-interactive without retaining a modal grab."""
        if self._destroyed:
            return
        self._release_grab()
        try:
            self.window.withdraw()
        except tk.TclError:
            pass

    def destroy(self) -> None:
        if self._destroyed:
            return
        self._destroyed = True
        self._release_grab()
        if self._render_after_id is not None:
            try:
                self.window.after_cancel(self._render_after_id)
            except tk.TclError:
                pass
            self._render_after_id = None
        self._photo = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass

    def _release_grab(self) -> None:
        try:
            if self.window.grab_current() == self.window:
                self.window.grab_release()
        except tk.TclError:
            pass

    def _position(self) -> None:
        left, top, work_width, work_height = _work_area(self.window)
        width, height = dialog_geometry(work_width, work_height)
        minimum_width = _MINIMUM_SIZE[0] if work_width >= _MINIMUM_SIZE[0] else work_width
        minimum_height = _MINIMUM_SIZE[1] if work_height >= _MINIMUM_SIZE[1] else work_height
        self.window.minsize(max(1, minimum_width), max(1, minimum_height))
        self.window.maxsize(max(1, work_width), max(1, work_height))
        x = left + max(0, (work_width - width) // 2)
        y = top + max(0, (work_height - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")

    def _choose(self, subject: str) -> None:
        if self._busy or self._item is None:
            return
        self.set_busy(True)
        self._on_choose(self._item.page, subject)

    def _on_configure(self, _event=None) -> None:
        self._queue_render()

    def _queue_render(self) -> None:
        if self._destroyed:
            return
        if self._render_after_id is not None:
            try:
                self.window.after_cancel(self._render_after_id)
            except tk.TclError:
                pass
        self._render_after_id = self.window.after(120, self._render_image)

    def _render_image(self) -> None:
        self._render_after_id = None
        if self._destroyed:
            return
        self.image_canvas.delete("all")
        source = self._source_image
        width, height = self.image_canvas.winfo_width() - 12, self.image_canvas.winfo_height() - 12
        if source is None:
            self.image_canvas.create_text(max(1, width) // 2, max(1, height) // 2, text="无法读取页面图片",
                                          fill=_COLORS["muted"], font=("Microsoft YaHei UI", 12))
            return
        size = _fit_image_size(source.size, (width, height))
        rendered = source if size == source.size else source.resize(size, Image.Resampling.LANCZOS)
        self._photo = ImageTk.PhotoImage(rendered, master=self.window)
        self.image_canvas.create_image(max(1, width + 12) // 2, max(1, height + 12) // 2, image=self._photo)

    @staticmethod
    def _load_image(path: Path) -> Image.Image | None:
        try:
            with Image.open(path) as image:
                return image.convert("RGB")
        except (OSError, ValueError):
            return None
