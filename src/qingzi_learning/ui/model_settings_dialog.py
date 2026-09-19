from __future__ import annotations

from typing import Callable

try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    tk = None

from qingzi_learning.models.settings import (
    ModelSettings,
    ModelSettingsError,
    ModelSettingsManager,
)


_COLORS = {
    "bg": "#fff7fb",
    "card": "#ffffff",
    "ink": "#45384f",
    "muted": "#7c6f84",
    "line": "#f0dce7",
    "pink": "#b83a74",
    "pink_dark": "#8f2c5c",
    "pink_soft": "#fde8f1",
    "danger": "#a22e46",
}


class ModelSettingsDialog:
    """Modal two-provider selector; API keys are never read back into an entry."""

    def __init__(
        self,
        parent,
        settings: ModelSettingsManager,
        *,
        on_saved: Callable[[ModelSettings], None],
    ) -> None:
        if tk is None:  # pragma: no cover
            raise RuntimeError("Tk is unavailable")
        self.settings = settings
        self.on_saved = on_saved
        current = settings.snapshot()
        self.window = tk.Toplevel(parent, bg=_COLORS["bg"])
        self.window.title("模型设置 · 晴子学习助手")
        self.window.transient(parent)
        self.window.resizable(False, False)
        self.window.protocol("WM_DELETE_WINDOW", self.destroy)

        self.provider_var = tk.StringVar(master=self.window, value=current.provider)
        self.endpoint_var = tk.StringVar(master=self.window, value=current.deepseek_base_url)
        self.model_var = tk.StringVar(master=self.window, value=current.deepseek_model)
        self.api_key_var = tk.StringVar(master=self.window, value="")
        self.clear_key_var = tk.BooleanVar(master=self.window, value=False)
        try:
            key_status = (
                "API Key 已安全保存；留空不会更改。" if settings.has_deepseek_key()
                else "尚未保存 API Key。"
            )
        except ModelSettingsError:
            key_status = "已保存的 API Key 无法读取；请填写新 Key 或勾选清除。"
        self.key_status_var = tk.StringVar(master=self.window, value=key_status)
        self.error_var = tk.StringVar(master=self.window, value="")

        card = tk.Frame(
            self.window, bg=_COLORS["card"],
            highlightbackground=_COLORS["line"], highlightthickness=1,
        )
        card.pack(fill="both", expand=True, padx=18, pady=18)
        tk.Label(
            card, text="选择分析模型", font=("Microsoft YaHei UI", 15, "bold"),
            fg=_COLORS["ink"], bg=_COLORS["card"],
        ).grid(row=0, column=0, columnspan=2, sticky="w", padx=18, pady=(16, 4))
        tk.Label(
            card, text="设置保存后，从下一次分析、报告或组卷开始生效。",
            fg=_COLORS["muted"], bg=_COLORS["card"],
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=18, pady=(0, 10))

        choices = tk.Frame(card, bg=_COLORS["pink_soft"])
        choices.grid(row=2, column=0, columnspan=2, sticky="ew", padx=18, pady=(0, 14))
        for column, (value, text) in enumerate((
            ("codex", "原生 Codex"), ("deepseek", "DeepSeek"),
        )):
            choices.grid_columnconfigure(column, weight=1, uniform="providers")
            tk.Radiobutton(
                choices, text=text, value=value, variable=self.provider_var,
                font=("Microsoft YaHei UI", 11, "bold"),
                fg=_COLORS["ink"], bg=_COLORS["pink_soft"],
                activebackground=_COLORS["pink_soft"], selectcolor=_COLORS["card"],
                anchor="w", padx=10, pady=9,
            ).grid(row=0, column=column, sticky="ew")

        self._label(card, "接口地址", 3)
        self.endpoint_entry = tk.Entry(card, textvariable=self.endpoint_var, width=48)
        self.endpoint_entry.grid(row=3, column=1, sticky="ew", padx=(8, 18), pady=5)
        self._label(card, "模型名称", 4)
        self.model_entry = tk.Entry(card, textvariable=self.model_var)
        self.model_entry.grid(row=4, column=1, sticky="ew", padx=(8, 18), pady=5)
        self._label(card, "API Key", 5)
        self.api_key_entry = tk.Entry(card, textvariable=self.api_key_var, show="•")
        self.api_key_entry.grid(row=5, column=1, sticky="ew", padx=(8, 18), pady=5)
        tk.Label(
            card, textvariable=self.key_status_var, fg=_COLORS["muted"],
            bg=_COLORS["card"], anchor="w",
        ).grid(row=6, column=1, sticky="w", padx=(8, 18), pady=(0, 4))
        tk.Checkbutton(
            card, text="清除已保存的 API Key", variable=self.clear_key_var,
            fg=_COLORS["muted"], bg=_COLORS["card"],
            activebackground=_COLORS["card"], selectcolor=_COLORS["card"],
        ).grid(row=7, column=1, sticky="w", padx=(5, 18))
        tk.Label(
            card, textvariable=self.error_var, fg=_COLORS["danger"],
            bg=_COLORS["card"], anchor="w", justify="left", wraplength=430,
        ).grid(row=8, column=0, columnspan=2, sticky="ew", padx=18, pady=(8, 0))

        actions = tk.Frame(card, bg=_COLORS["card"])
        actions.grid(row=9, column=0, columnspan=2, sticky="e", padx=18, pady=16)
        tk.Button(
            actions, text="取消", command=self.destroy,
            fg=_COLORS["ink"], bg="#eee9ff", padx=18, pady=7,
        ).pack(side="left", padx=(0, 8))
        tk.Button(
            actions, text="保存设置", command=self.save,
            fg="white", bg=_COLORS["pink"], activeforeground="white",
            activebackground=_COLORS["pink_dark"], padx=18, pady=7,
        ).pack(side="left")
        card.grid_columnconfigure(1, weight=1)

        self.window.update_idletasks()
        width, height = 590, self.window.winfo_reqheight()
        x = max(0, parent.winfo_rootx() + (parent.winfo_width() - width) // 2)
        y = max(0, parent.winfo_rooty() + (parent.winfo_height() - height) // 2)
        self.window.geometry(f"{width}x{height}+{x}+{y}")
        self.window.lift()
        try:
            self.window.grab_set()
        except tk.TclError:
            pass

    @staticmethod
    def _label(parent, text: str, row: int) -> None:
        tk.Label(
            parent, text=text, font=("Microsoft YaHei UI", 10, "bold"),
            fg=_COLORS["ink"], bg=_COLORS["card"], anchor="e",
        ).grid(row=row, column=0, sticky="e", padx=(18, 0), pady=5)

    def save(self) -> bool:
        self.error_var.set("")
        try:
            result = self.settings.save(
                provider=self.provider_var.get(),
                deepseek_base_url=self.endpoint_var.get(),
                deepseek_model=self.model_var.get(),
                api_key=self.api_key_var.get(),
                clear_api_key=self.clear_key_var.get(),
            )
        except ModelSettingsError as exc:
            self.error_var.set(str(exc))
            return False
        self.on_saved(result)
        self.destroy()
        return True

    def destroy(self) -> None:
        try:
            if self.window.grab_current() == self.window:
                self.window.grab_release()
            self.window.destroy()
        except tk.TclError:
            pass
