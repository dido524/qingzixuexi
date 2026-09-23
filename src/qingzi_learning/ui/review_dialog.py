"""Scrollable evidence cards and fixed parent-review actions."""
import tkinter as tk

from PIL import Image, ImageTk


BG, CARD, INK, MUTED, PINK = "#fff8fb", "#ffffff", "#3b3047", "#71677d", "#9e4074"


def _preview_target(canvas_width, screen_height):
    """Readable in-dialog preview bounds; the body remains scrollable."""
    return (min(1410, max(420, canvas_width - 40)),
            min(760, max(520, screen_height - 300)))


class ReviewDialog:
    def __init__(self, parent, dialog_id, on_save, on_close, open_path,
                 *, on_batch=None, allow_batch=False):
        self.dialog_id, self.on_save, self.open_path = dialog_id, on_save, open_path
        self.on_batch, self.allow_batch = on_batch, allow_batch
        self.item, self.items, self.correct_items = None, (), ()
        self.correct_vars = {}
        self.window = tk.Toplevel(parent, bg=BG)
        self.window.title("待家长确认")
        width = max(760, min(1500, self.window.winfo_screenwidth() - 48))
        height = max(560, min(940, self.window.winfo_screenheight() - 70))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(min(width, 680), min(height, 480))
        self.window.protocol("WM_DELETE_WINDOW", lambda: on_close(dialog_id))

        header = tk.Frame(self.window, bg="#f9e3ef", padx=20, pady=12)
        header.pack(fill="x")
        tk.Label(header, text="逐题看清楚，再决定", bg="#f9e3ef", fg=INK,
                 font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w")
        self.progress_var = tk.StringVar(master=self.window)
        tk.Label(header, textvariable=self.progress_var, bg="#f9e3ef", fg=MUTED).pack(anchor="w")

        # Pack the footer before the scrollable body: short viewports cannot hide it.
        self.action_bar = tk.Frame(self.window, bg=CARD, padx=16, pady=10,
                                   highlightbackground="#ead7e3", highlightthickness=1)
        self.action_bar.pack(side="bottom", fill="x")
        self.message_var = tk.StringVar(master=self.window, value="正在读取待确认题目…")
        tk.Label(self.action_bar, textvariable=self.message_var, bg=CARD, fg=MUTED,
                 wraplength=850, justify="left", anchor="w").pack(fill="x", pady=(0, 8))
        actions = tk.Frame(self.action_bar, bg=CARD)
        actions.pack(fill="x")
        self.save_button = tk.Button(actions, text="保存并查看下一题", command=self.save, state="disabled",
                                     bg=PINK, fg="white", activebackground="#84325f",
                                     disabledforeground="#77717c", padx=18, pady=8, wraplength=420)
        self.save_button.pack(fill="x")
        self.batch_button = tk.Button(actions, text="保存已勾选的正确题（0）", command=self.confirm_correct_batch,
                                      state="disabled", bg="#e8d8f2", fg="#503c6f",
                                      disabledforeground="#817b87", padx=14, pady=8, wraplength=420)
        self.batch_button.pack(fill="x", pady=(6, 0))
        tk.Label(self.action_bar, text="正确题需逐项勾选；错误、部分正确和不确定题仍逐题核对", bg=CARD, fg=MUTED,
                 anchor="w").pack(fill="x", pady=(6, 0))

        body = tk.Frame(self.window, bg=BG)
        body.pack(fill="both", expand=True)
        self.canvas = tk.Canvas(body, bg=BG, highlightthickness=0)
        scrollbar = tk.Scrollbar(body, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        self.canvas.pack(side="left", fill="both", expand=True)
        self.window.bind("<MouseWheel>", self._scroll_wheel)
        self.content = tk.Frame(self.canvas, bg=BG, padx=16, pady=12)
        self._content_id = self.canvas.create_window((0, 0), window=self.content, anchor="nw")
        self.content.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", self._resize_content)

        self.meta_var = tk.StringVar(master=self.window)
        tk.Label(self.content, textvariable=self.meta_var, bg=BG, fg=MUTED,
                 font=("Microsoft YaHei UI", 10, "bold")).pack(fill="x", anchor="w", pady=(0, 8))
        self.preview = tk.Label(self.content, text="正在读取…", bg=CARD, fg=MUTED, pady=8)
        self.preview.pack(fill="x")
        image_actions = tk.Frame(self.content, bg=BG)
        image_actions.pack(fill="x", pady=(7, 12))
        self.open_button = tk.Button(image_actions, text="打开批改图", command=self.open_annotated,
                                     bg="#f3e5ef", fg=INK)
        self.open_button.pack(side="left")
        self.original_button = tk.Button(image_actions, text="打开完整原图", command=self.open_source,
                                         bg="#f3e5ef", fg=INK)
        self.original_button.pack(side="left", padx=8)

        self.correct_card = tk.Frame(self.content, bg="#f4fbf7", padx=14, pady=10,
                                     highlightbackground="#cce8d8", highlightthickness=1)
        self.correct_card.pack(fill="x", pady=(0, 10))
        self.correct_title_var = tk.StringVar(master=self.window)
        tk.Label(self.correct_card, textvariable=self.correct_title_var, bg="#f4fbf7", fg="#237a53",
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        tk.Label(self.correct_card, text="看过题目后点一下方框；可以勾选几道就先保存几道。",
                 bg="#f4fbf7", fg=MUTED).pack(anchor="w", pady=(2, 7))
        self.correct_rows = tk.Frame(self.correct_card, bg="#f4fbf7")
        self.correct_rows.pack(fill="x")
        self._correct_labels = []

        self.detail_vars = {name: tk.StringVar(master=self.window) for name in (
            "prompt_summary", "student_answer", "reference_answer", "status", "reason", "confidence")}
        self._detail_labels = []
        self._preview_width = 0
        self._card("题目与作答", (("题目", "prompt_summary"), ("学生答案", "student_answer"),
                          ("参考答案", "reference_answer")))
        self._card("AI 判断依据", (("原判断", "status"), ("判断理由", "reason"),
                          ("原置信度", "confidence")))
        self.choice_card = tk.Frame(self.content, bg=CARD, padx=14, pady=10,
                                    highlightbackground="#eedee8", highlightthickness=1)
        self.choice_card.pack(fill="x", pady=(0, 10))
        tk.Label(self.choice_card, text="家长最终判断", bg=CARD, fg=PINK,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
        self.status_var = tk.StringVar(master=self.window)
        choices = tk.Frame(self.choice_card, bg=CARD)
        choices.pack(anchor="w", pady=(5, 8))
        for label, value in (("正确", "correct"), ("错误", "incorrect"), ("部分正确", "partial")):
            tk.Radiobutton(choices, text=label, value=value, variable=self.status_var,
                           bg=CARD, fg=INK, activebackground=CARD).pack(side="left", padx=(0, 16))
        self.answer_var, self.note_var = tk.StringVar(master=self.window), tk.StringVar(master=self.window)
        for label, variable in (("修正后的参考答案（可选）", self.answer_var), ("复核备注（可选）", self.note_var)):
            tk.Label(self.choice_card, text=label, bg=CARD, fg=MUTED).pack(anchor="w", pady=(5, 3))
            tk.Entry(self.choice_card, textvariable=variable).pack(fill="x")

    def _card(self, title, fields):
        card = tk.Frame(self.content, bg=CARD, padx=14, pady=10,
                        highlightbackground="#eedee8", highlightthickness=1)
        card.pack(fill="x", pady=(0, 10))
        tk.Label(card, text=title, bg=CARD, fg=PINK,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        for label, key in fields:
            row = tk.Frame(card, bg=CARD)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=CARD, fg=MUTED, width=9, anchor="nw").pack(side="left")
            detail = tk.Label(row, textvariable=self.detail_vars[key], bg=CARD, fg=INK,
                              anchor="w", justify="left", wraplength=500)
            detail.pack(side="left", fill="x", expand=True)
            self._detail_labels.append(detail)

    def _resize_content(self, event):
        self.canvas.itemconfigure(self._content_id, width=event.width)
        for label in self._detail_labels:
            label.configure(wraplength=max(220, event.width - 165))
        for label in self._correct_labels:
            label.configure(wraplength=max(300, event.width - 115))
        if self.item and abs(self._preview_width - event.width) > 50:
            self._render_preview()

    def _scroll_wheel(self, event):
        if event.delta:
            self.canvas.yview_scroll(-1 if event.delta > 0 else 1, "units")

    @staticmethod
    def _status_text(value):
        return {"correct": "正确", "incorrect": "错误", "partial": "部分正确",
                "needs_review": "不确定"}.get(value, value)

    def _batch_items(self):
        if not self.allow_batch:
            return ()
        return tuple(item for item in self.items if item.original_status == "correct"
                     and item.original_decision_source == "model"
                     and item.status in {"correct", "needs_review"})

    def _rebuild_correct_list(self):
        for child in self.correct_rows.winfo_children():
            child.destroy()
        self.correct_vars = {}
        self._correct_labels = []
        self.correct_title_var.set(
            f"AI 判为正确（{len(self.correct_items)} 道）· 请逐项点击确认"
            if self.correct_items else "本次没有待快速确认的正确题"
        )
        for item in self.correct_items:
            key = (item.document_id, item.question_id)
            variable = tk.BooleanVar(master=self.window, value=False)
            self.correct_vars[key] = variable
            row = tk.Frame(self.correct_rows, bg="#f4fbf7")
            row.pack(fill="x", pady=2)
            text = (f"第 {item.page} 页 · 第 {item.question_id} 题  |  "
                    f"{item.prompt_summary or '题目未识别'}  |  "
                    f"学生答案：{item.student_answer or '未识别'}  |  置信度 {item.confidence:.0%}")
            check = tk.Checkbutton(row, text=text, variable=variable, command=self._update_correct_selection,
                                   bg="#f4fbf7", fg=INK, activebackground="#f4fbf7",
                                   anchor="w", justify="left", wraplength=900)
            check.pack(fill="x")
            self._correct_labels.append(check)

    def _selected_correct_items(self):
        return tuple(item for item in self.correct_items
                     if self.correct_vars.get((item.document_id, item.question_id)) is not None
                     and self.correct_vars[(item.document_id, item.question_id)].get())

    def _update_correct_selection(self):
        selected = len(self._selected_correct_items())
        self.batch_button.configure(state="normal" if selected else "disabled",
                                    text=f"保存已勾选的正确题（{selected}/{len(self.correct_items)}）")

    def show(self, items, message=""):
        self.items = tuple(items)
        self.correct_items = self._batch_items()
        correct_ids = {(item.document_id, item.question_id) for item in self.correct_items}
        detailed = tuple(item for item in self.items
                         if (item.document_id, item.question_id) not in correct_ids)
        self.item = detailed[0] if detailed else (self.correct_items[0] if self.correct_items else None)
        self.message_var.set(message or (f"还有 {len(items)} 道待确认题目。" if items else "所有待确认题目已处理。"))
        self.progress_var.set(f"需逐题核对 {len(detailed)} 道 · AI 判对待勾选 {len(self.correct_items)} 道")
        proposed = self.item.original_status if self.item and self.item.original_status in {"incorrect", "partial"} else ""
        self.status_var.set(proposed); self.answer_var.set(""); self.note_var.set("")
        self.save_button.configure(state="normal" if detailed else "disabled",
                                   text="保存并查看下一题" if detailed else "错误／不确定题已逐题处理完")
        self._rebuild_correct_list()
        self._update_correct_selection()
        self.open_button.configure(state="normal" if self.item and self.item.annotated_path.exists() else "disabled")
        self.original_button.configure(state="normal" if self.item and self.item.source_path.exists() else "disabled")
        if not self.item:
            self.meta_var.set("复核完成")
            for variable in self.detail_vars.values(): variable.set("")
            self.preview.configure(image="", text="暂无待家长确认的题目。")
            return
        q = self.item
        self.meta_var.set(f"{q.subject}  ·  第 {q.page} 页  ·  第 {q.question_id} 题")
        self.detail_vars["prompt_summary"].set(q.prompt_summary or "未识别")
        self.detail_vars["student_answer"].set(q.student_answer or "未识别")
        self.detail_vars["reference_answer"].set(q.reference_answer or "暂无")
        source = {"model": "AI", "teacher": "老师", "mixed": "老师与 AI"}.get(q.original_decision_source, q.original_decision_source)
        self.detail_vars["status"].set(f"{self._status_text(q.original_status)}  ·  来自{source}")
        self.detail_vars["reason"].set(q.reason or "未提供")
        self.detail_vars["confidence"].set(f"{q.confidence:.0%}")
        self._render_preview()
        self.canvas.yview_moveto(0)

    def _render_preview(self):
        if self.item is None:
            return
        self._preview_width = self.canvas.winfo_width()
        try:
            q = self.item
            preview_path = q.annotated_path if q.annotated_path.exists() else q.source_path
            with Image.open(preview_path) as source_image:
                preview = source_image.copy()
                preview.thumbnail(_preview_target(max(460, self._preview_width),
                                                  self.window.winfo_screenheight()))
            self._photo = ImageTk.PhotoImage(preview, master=self.window)
            self.preview.configure(image=self._photo, text="")
        except (OSError, ValueError):
            self.preview.configure(image="", text="原图预览不可用，可打开完整原图查看。")

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
            self.batch_button.configure(state="disabled")
            self.message_var.set("正在保存复核结果…")

    def confirm_correct_batch(self):
        selected = self._selected_correct_items()
        if not selected or self.on_batch is None:
            return
        if self.on_batch(self.dialog_id, selected):
            self.save_button.configure(state="disabled")
            self.batch_button.configure(state="disabled")
            self.message_var.set(f"正在保存 {len(selected)} 道正确题…")

    def destroy(self):
        """Release Tcl objects on Tk's thread, even with stale callbacks."""
        self.item, self.items, self.correct_items = None, (), ()
        self.correct_vars.clear()
        self.window.destroy()
        self.on_save = self.on_batch = self.open_path = None
        self.detail_vars = {}
        for name in ("meta_var", "progress_var", "status_var", "answer_var", "note_var", "message_var",
                     "correct_title_var", "_photo"):
            if hasattr(self, name): setattr(self, name, None)
