"""Evidence-led Tk parent review dialog; persistence is delegated to the worker."""
import tkinter as tk
from PIL import Image, ImageTk


class ReviewDialog:
    def __init__(self, parent, dialog_id, on_save, on_close, open_path):
        self.dialog_id, self.on_save, self.open_path = dialog_id, on_save, open_path
        self.item = None
        self.window = tk.Toplevel(parent)
        self.window.title("待家长确认")
        self.window.geometry("1020x780")
        self.window.minsize(880, 650)
        self.window.protocol("WM_DELETE_WINDOW", lambda: on_close(dialog_id))
        self.preview = tk.Label(self.window, text="正在读取…")
        self.preview.pack(padx=12, pady=8)
        self.evidence_var = tk.StringVar(master=self.window)
        tk.Label(self.window, textvariable=self.evidence_var, wraplength=740, justify="left").pack(fill="x", padx=12)
        open_row = tk.Frame(self.window); open_row.pack()
        self.open_button = tk.Button(open_row, text="打开批改图", command=self.open_annotated)
        self.open_button.pack(side="left", padx=5)
        self.original_button = tk.Button(open_row, text="打开完整原图", command=self.open_source)
        self.original_button.pack(side="left", padx=5)
        self.status_var = tk.StringVar(master=self.window)
        choices = tk.Frame(self.window); choices.pack()
        for label, value in (("正确", "correct"), ("错误", "incorrect"), ("部分正确", "partial")):
            tk.Radiobutton(choices, text=label, value=value, variable=self.status_var).pack(side="left", padx=12)
        self.answer_var, self.note_var = tk.StringVar(master=self.window), tk.StringVar(master=self.window)
        tk.Label(self.window, text="修正后的参考答案（可选）").pack(anchor="w", padx=12)
        tk.Entry(self.window, textvariable=self.answer_var, width=95).pack(fill="x", padx=12)
        tk.Label(self.window, text="复核备注（可选）").pack(anchor="w", padx=12)
        tk.Entry(self.window, textvariable=self.note_var, width=95).pack(fill="x", padx=12)
        self.message_var = tk.StringVar(master=self.window, value="正在读取待确认题目…")
        tk.Label(self.window, textvariable=self.message_var, wraplength=740).pack(padx=12, pady=8)
        self.save_button = tk.Button(self.window, text="保存并查看下一题", command=self.save, state="disabled")
        self.save_button.pack(pady=8)

    def show(self, items, message=""):
        self.item = items[0] if items else None
        self.message_var.set(message or (f"还有 {len(items)} 道待确认题目。" if items else "所有待确认题目已处理。"))
        proposed = self.item.original_status if self.item and self.item.original_status in {"incorrect", "partial"} else ""
        self.status_var.set(proposed); self.answer_var.set(""); self.note_var.set("")
        self.save_button.configure(state="normal" if self.item else "disabled")
        self.open_button.configure(state="normal" if self.item and self.item.annotated_path.exists() else "disabled")
        self.original_button.configure(state="normal" if self.item and self.item.source_path.exists() else "disabled")
        if not self.item:
            self.evidence_var.set("暂无待家长确认的题目。"); self.preview.configure(image="", text="复核完成")
            return
        q = self.item
        self.evidence_var.set(
            f"{q.subject} · {q.document_id} · 第 {q.page} 页 · 第 {q.question_id} 题\n"
            f"题目：{q.prompt_summary}\n学生答案：{q.student_answer}\n参考答案：{q.reference_answer}\n"
            f"当前判断：{q.status}（{q.decision_source}）；原判断：{q.original_status}（{q.original_decision_source}）\n"
            f"系统理由：{q.reason}\n原置信度：{q.confidence:.2f}\n原图：{q.source_path}")
        try:
            preview_path = q.annotated_path if q.annotated_path.exists() else q.source_path
            with Image.open(preview_path) as source:
                preview = source.copy(); preview.thumbnail((900, 520))
            self._photo = ImageTk.PhotoImage(preview, master=self.window)
            self.preview.configure(image=self._photo, text="")
        except (OSError, ValueError):
            self.preview.configure(image="", text="原图预览不可用，可尝试打开完整原图。")

    def open_source(self):
        if self.item and self.item.source_path.exists(): self.open_path(self.item.source_path)

    def open_annotated(self):
        if self.item and self.item.annotated_path.exists(): self.open_path(self.item.annotated_path)

    def save(self):
        if self.item is None: return
        if self.status_var.get() not in {"correct", "incorrect", "partial"}:
            self.message_var.set("请先选择正确、错误或部分正确。")
            return
        if self.on_save(self.dialog_id, self.item, self.status_var.get(), self.answer_var.get(), self.note_var.get()):
            self.save_button.configure(state="disabled")
            self.message_var.set("正在保存复核结果…")

    def destroy(self):
        """Release Tcl-owned objects now, on Tk's thread, even with stale callbacks."""
        self.item = None
        self.window.destroy()
        self.on_save = self.open_path = None
        for name in ("evidence_var", "status_var", "answer_var", "note_var", "message_var", "_photo"):
            if hasattr(self, name):
                setattr(self, name, None)
