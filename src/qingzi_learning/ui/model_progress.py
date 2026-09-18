"""Friendly, honest progress feedback for long-running model operations."""

from __future__ import annotations

from dataclasses import dataclass
import math
import time
import tkinter as tk


@dataclass(frozen=True)
class ProgressSnapshot:
    title: str
    stage: str
    percent: int
    elapsed_text: str
    time_hint: str


@dataclass(frozen=True)
class _Plan:
    title: str
    expected_seconds: int
    stages: tuple[tuple[float, str], ...]


_PLANS = {
    "finish": _Plan("正在认真分析这份资料", 180, (
        (.25, "正在读取题目和书写内容"),
        (.65, "正在判断科目与答题情况"),
        (1.0, "正在整理知识点和错题"),
    )),
    "retry": _Plan("正在继续处理这份资料", 180, (
        (.25, "正在重新读取资料"),
        (.65, "正在核对分析结果"),
        (1.0, "正在更新知识库"),
    )),
    "generate_report": _Plan("正在整理新的学情报告", 90, (
        (.35, "正在汇总近期学习记录"),
        (.75, "正在把变化写成容易理解的说明"),
        (1.0, "正在排版孩子版和家长版"),
    )),
    "generate_exam": _Plan("正在认真准备模拟卷", 300, (
        (.25, "正在根据薄弱点设计题目"),
        (.70, "正在进行独立校验，检查答案与难度"),
        (1.0, "正在整理试卷和答案解析"),
    )),
}


def progress_snapshot(kind: str, elapsed_seconds: float) -> ProgressSnapshot:
    plan = _PLANS[kind]
    elapsed = max(0, int(elapsed_seconds))
    ratio = elapsed / plan.expected_seconds
    stage = plan.stages[-1][1]
    for limit, text in plan.stages:
        if ratio < limit:
            stage = text
            break
    percent = min(92, 8 + int(min(ratio, 1) * 84))
    minutes, seconds = divmod(elapsed, 60)
    elapsed_text = f"已用 {minutes}分{seconds:02d}秒" if minutes else f"已用 {seconds}秒"
    remaining = plan.expected_seconds - elapsed
    if remaining > 60:
        time_hint = f"预计还需约 {math.ceil(remaining / 60)} 分钟"
    elif remaining > 0:
        time_hint = "预计还需不到 1 分钟"
    else:
        time_hint = "正在完成最后检查，请再稍候"
    return ProgressSnapshot(plan.title, stage, percent, elapsed_text, time_hint)


class ModelProgressDialog:
    """Small non-modal card that keeps the parent responsive while work runs."""

    def __init__(self, parent, kind: str, *, now=time.monotonic) -> None:
        self.kind = kind
        self._now = now
        self._started = now()
        self._after_id = None
        self.window = tk.Toplevel(parent)
        self.window.title("晴子学习助手 · 正在努力")
        self.window.configure(bg="#fff7fb")
        self.window.resizable(False, False)
        self.window.transient(parent)
        self.window.protocol("WM_DELETE_WINDOW", lambda: None)

        card = tk.Frame(
            self.window, bg="#ffffff", padx=24, pady=20,
            highlightbackground="#efd6e5", highlightthickness=1,
        )
        card.pack(fill="both", expand=True, padx=10, pady=10)
        self.title_var = tk.StringVar(master=self.window)
        self.stage_var = tk.StringVar(master=self.window)
        self.elapsed_var = tk.StringVar(master=self.window)
        self.hint_var = tk.StringVar(master=self.window)
        tk.Label(card, text="✦", bg="#ffffff", fg="#b83a74",
                 font=("Segoe UI Symbol", 22, "bold")).pack()
        tk.Label(card, textvariable=self.title_var, bg="#ffffff", fg="#45384f",
                 font=("Microsoft YaHei UI", 14, "bold")).pack(pady=(2, 5))
        tk.Label(card, textvariable=self.stage_var, bg="#ffffff", fg="#7c6f84",
                 font=("Microsoft YaHei UI", 10)).pack(pady=(0, 12))
        self._progress_percent = 0
        self.progress_canvas = tk.Canvas(
            card, width=390, height=16, bg="#ffffff", highlightthickness=0,
        )
        self.progress_canvas.pack(fill="x")
        self.progress_canvas.bind("<Configure>", self._draw_progress)
        timing = tk.Frame(card, bg="#ffffff")
        timing.pack(fill="x", pady=(9, 0))
        tk.Label(timing, textvariable=self.elapsed_var, bg="#ffffff", fg="#7c6f84",
                 font=("Microsoft YaHei UI", 9)).pack(side="left")
        tk.Label(timing, textvariable=self.hint_var, bg="#ffffff", fg="#8f2c5c",
                 font=("Microsoft YaHei UI", 9, "bold")).pack(side="right")
        tk.Label(card, text="可以让它安静处理，完成后会自动关闭这个提示。",
                 bg="#ffffff", fg="#9a8f9f", font=("Microsoft YaHei UI", 8)).pack(pady=(10, 0))

        self.window.update_idletasks()
        width, height = self.window.winfo_reqwidth(), self.window.winfo_reqheight()
        try:
            x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
            y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 2)
            self.window.geometry(f"{width}x{height}+{x}+{y}")
        except tk.TclError:
            pass
        self.window.lift()
        self._tick()

    def _tick(self) -> None:
        snapshot = progress_snapshot(self.kind, self._now() - self._started)
        self.title_var.set(snapshot.title)
        self.stage_var.set(snapshot.stage)
        self.elapsed_var.set(snapshot.elapsed_text)
        self.hint_var.set(snapshot.time_hint)
        self._progress_percent = snapshot.percent
        self._draw_progress()
        try:
            self._after_id = self.window.after(1000, self._tick)
        except tk.TclError:
            self._after_id = None

    def _draw_progress(self, _event=None) -> None:
        canvas = self.progress_canvas
        width = max(2, canvas.winfo_width())
        height = max(2, canvas.winfo_height())
        canvas.delete("all")
        canvas.create_rectangle(0, 0, width, height, fill="#f4e9f1", outline="#ead7e3")
        fill_width = max(2, round(width * self._progress_percent / 100))
        canvas.create_rectangle(0, 0, fill_width, height, fill="#c75b8d", outline="#c75b8d")

    def close(self) -> None:
        if self._after_id is not None:
            try:
                self.window.after_cancel(self._after_id)
            except tk.TclError:
                pass
            self._after_id = None
        try:
            self.window.destroy()
        except tk.TclError:
            pass
