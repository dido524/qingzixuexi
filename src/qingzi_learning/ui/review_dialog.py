"""Scrollable evidence cards and fixed parent-review actions."""
from dataclasses import dataclass
import tkinter as tk

from PIL import Image, ImageTk

from qingzi_learning.grading.question_number import display_question_number


BG, CARD, INK, MUTED, PINK = "#fff8fb", "#ffffff", "#3b3047", "#71677d", "#9e4074"


@dataclass
class QuestionReviewCard:
    item: object
    frame: tk.Frame
    section_titles: tuple[str, str, str]
    detail_vars: dict[str, tk.StringVar]
    status_var: tk.StringVar
    answer_var: tk.StringVar
    note_var: tk.StringVar
    save_button: tk.Button
    quick_confirm_var: tk.BooleanVar | None


def _correct_method_text(item) -> str:
    """Make older terse analyses useful without silently re-calling a model."""
    parts = []
    reference = (item.reference_answer or "").strip()
    reason = (item.reason or "").strip()
    if reference:
        parts.append(f"参考答案／示例：{reference}")
    if reason:
        label = "错误定位与改正思路" if item.original_status in {"incorrect", "partial"} else "关键方法与核对思路"
        parts.append(f"{label}：{reason}")
    if not parts:
        return "现有分析没有提供可靠解法，请结合原题和老师要求人工确认。"
    return "\n".join(parts)


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
        self.review_cards = {}
        self._current_key = None
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
        self.question_number_var = tk.StringVar(master=self.window)
        tk.Label(self.content, textvariable=self.question_number_var, bg="#fff0f5", fg="#9e275f",
                 font=("Microsoft YaHei UI", 17, "bold"), padx=14, pady=9,
                 anchor="w").pack(fill="x", pady=(0, 10))
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

        questions_header = tk.Frame(self.content, bg=BG)
        questions_header.pack(fill="x", pady=(0, 8))
        self.questions_title_var = tk.StringVar(master=self.window)
        tk.Label(questions_header, textvariable=self.questions_title_var, bg=BG, fg=INK,
                 font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        self.correct_title_var = tk.StringVar(master=self.window)
        tk.Label(questions_header, textvariable=self.correct_title_var, bg=BG, fg="#237a53",
                 font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w", pady=(2, 0))
        self.question_rows = tk.Frame(self.content, bg=BG)
        self.question_rows.pack(fill="x")
        self._correct_labels = []
        self.detail_vars = {}
        self._detail_labels = []
        self._preview_width = 0
        self.status_var = tk.StringVar(master=self.window)
        self.answer_var, self.note_var = tk.StringVar(master=self.window), tk.StringVar(master=self.window)

    def _detail_section(self, parent, title, fields, variables, *, background=CARD):
        section = tk.Frame(parent, bg=background, padx=12, pady=9,
                           highlightbackground="#eedee8", highlightthickness=1)
        section.pack(fill="x", padx=10, pady=(0, 8))
        tk.Label(section, text=title, bg=background, fg=PINK,
                 font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w", pady=(0, 8))
        for label, key in fields:
            row = tk.Frame(section, bg=background)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=background, fg=MUTED, width=11, anchor="nw").pack(side="left")
            detail = tk.Label(row, textvariable=variables[key], bg=background, fg=INK,
                              anchor="w", justify="left", wraplength=500)
            detail.pack(side="left", fill="x", expand=True)
            self._detail_labels.append(detail)
        return section

    def _rebuild_question_cards(self):
        for child in self.question_rows.winfo_children():
            child.destroy()
        self._release_card_variables()
        self._correct_labels = []
        self._detail_labels = []
        quick_ids = {(item.document_id, item.question_id) for item in self.correct_items}
        status_colors = {
            "correct": ("#f4fbf7", "#237a53"),
            "incorrect": ("#fff3f4", "#b93649"),
            "partial": ("#fff8ed", "#a45d12"),
            "needs_review": ("#f6f1ff", "#6745a4"),
        }
        for index, item in enumerate(self.items, 1):
            key = (item.document_id, item.question_id)
            tint, accent = status_colors.get(item.original_status, (CARD, PINK))
            outer = tk.Frame(self.question_rows, bg=tint, padx=4, pady=8,
                             highlightbackground="#e5d9e1", highlightthickness=1)
            outer.pack(fill="x", pady=(0, 12))
            header = tk.Frame(outer, bg=tint, padx=10, pady=4)
            header.pack(fill="x")
            title = (f"{index}. 卷面题号 {display_question_number(item.question_id)}"
                     f"　·　第 {item.page} 页　·　AI 判断：{self._status_text(item.original_status)}")
            tk.Label(header, text=title, bg=tint, fg=accent,
                     font=("Microsoft YaHei UI", 13, "bold"), anchor="w").pack(side="left", fill="x", expand=True)
            tk.Button(header, text="在上方查看本题", bg="#f3e5ef", fg=INK,
                      command=lambda k=key: self._activate_card(k, render_preview=True)).pack(side="right")

            source = {"model": "AI", "teacher": "老师", "mixed": "老师与 AI"}.get(
                item.original_decision_source, item.original_decision_source)
            variables = {
                "prompt_summary": tk.StringVar(master=self.window, value=item.prompt_summary or "未识别"),
                "student_answer": tk.StringVar(master=self.window, value=item.student_answer or "未识别"),
                "reference_answer": tk.StringVar(master=self.window, value=item.reference_answer or "暂无"),
                "status": tk.StringVar(master=self.window, value=(
                    f"{self._status_text(item.original_status)}　·　来自{source}")),
                "reason": tk.StringVar(master=self.window, value=item.reason or "未提供"),
                "confidence": tk.StringVar(master=self.window, value=f"{item.confidence:.0%}"),
                "correct_method": tk.StringVar(master=self.window, value=_correct_method_text(item)),
            }
            self._detail_section(outer, "题目与作答", (
                ("题目", "prompt_summary"), ("学生答案", "student_answer"),
                ("参考答案", "reference_answer")), variables, background=tint)
            self._detail_section(outer, "AI 判断依据", (
                ("AI 原判断", "status"), ("为什么", "reason"),
                ("正确做法", "correct_method"), ("置信度", "confidence")), variables, background=tint)

            final = tk.Frame(outer, bg=tint, padx=12, pady=9,
                             highlightbackground="#eedee8", highlightthickness=1)
            final.pack(fill="x", padx=10, pady=(0, 4))
            tk.Label(final, text="家长最终判断", bg=tint, fg=PINK,
                     font=("Microsoft YaHei UI", 11, "bold")).pack(anchor="w")
            status_var = tk.StringVar(master=self.window, value=(
                item.original_status if item.original_status in {"incorrect", "partial"} else ""))
            choices = tk.Frame(final, bg=tint)
            choices.pack(anchor="w", pady=(5, 7))
            for label, value in (("正确", "correct"), ("错误", "incorrect"), ("部分正确", "partial")):
                tk.Radiobutton(choices, text=label, value=value, variable=status_var,
                               command=lambda k=key: self._status_changed(k),
                               bg=tint, fg=INK, activebackground=tint).pack(side="left", padx=(0, 16))
            answer_var = tk.StringVar(master=self.window)
            note_var = tk.StringVar(master=self.window)
            for label, variable in (("修正后的参考答案（可选）", answer_var), ("复核备注（可选）", note_var)):
                tk.Label(final, text=label, bg=tint, fg=MUTED).pack(anchor="w", pady=(4, 3))
                entry = tk.Entry(final, textvariable=variable)
                entry.pack(fill="x")
                entry.bind("<FocusIn>", lambda _event, k=key: self._activate_card(k, render_preview=False))

            quick_var = None
            if key in quick_ids:
                quick_var = tk.BooleanVar(master=self.window, value=False)
                self.correct_vars[key] = quick_var
                check = tk.Checkbutton(
                    final, text="已查看，确认本题正确（可与其他正确题一起保存）",
                    variable=quick_var, command=lambda k=key: self._toggle_quick_confirm(k),
                    bg=tint, fg="#237a53", activebackground=tint, anchor="w", justify="left")
                check.pack(fill="x", pady=(8, 2))
                self._correct_labels.append(check)
            save_button = tk.Button(
                final, text=f"保存本题（题号 {display_question_number(item.question_id)}）",
                bg=PINK, fg="white", activebackground="#84325f", padx=14, pady=7,
                command=lambda k=key: self._save_card(k))
            save_button.pack(fill="x", pady=(8, 0))
            self.review_cards[key] = QuestionReviewCard(
                item, outer, ("题目与作答", "AI 判断依据", "家长最终判断"), variables,
                status_var, answer_var, note_var, save_button, quick_var)

    def _status_changed(self, key):
        card = self.review_cards.get(key)
        if card is None:
            return
        if card.quick_confirm_var is not None and card.status_var.get() != "correct":
            card.quick_confirm_var.set(False)
        self._activate_card(key, render_preview=False)
        self._update_correct_selection()

    def _toggle_quick_confirm(self, key):
        card = self.review_cards.get(key)
        if card is None:
            return
        if card.quick_confirm_var is not None and card.quick_confirm_var.get():
            card.status_var.set("correct")
        self._activate_card(key, render_preview=False)
        self._update_correct_selection()

    def _activate_card(self, key, *, render_preview):
        card = self.review_cards.get(key)
        if card is None:
            return
        for other_key, other in self.review_cards.items():
            other.frame.configure(highlightbackground=PINK if other_key == key else "#e5d9e1",
                                  highlightthickness=2 if other_key == key else 1)
        self._current_key = key
        self.item = card.item
        self.detail_vars = card.detail_vars
        self.status_var = card.status_var
        self.answer_var = card.answer_var
        self.note_var = card.note_var
        number = display_question_number(card.item.question_id)
        self.meta_var.set(f"{card.item.subject}  ·  第 {card.item.page} 页")
        self.question_number_var.set(f"卷面题号：{number}")
        self.save_button.configure(state="normal", text=f"保存当前题（题号 {number}）")
        self.open_button.configure(state="normal" if card.item.annotated_path.exists() else "disabled")
        self.original_button.configure(state="normal" if card.item.source_path.exists() else "disabled")
        if render_preview:
            self._render_preview()

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

    def _selected_correct_items(self):
        return tuple(item for item in self.correct_items
                     if self.correct_vars.get((item.document_id, item.question_id)) is not None
                     and self.correct_vars[(item.document_id, item.question_id)].get())

    def _update_correct_selection(self):
        selected = len(self._selected_correct_items())
        self.batch_button.configure(state="normal" if selected else "disabled",
                                    text=f"保存已勾选的正确题（{selected}/{len(self.correct_items)}）")

    def _release_card_variables(self):
        """Release Tcl variables on the UI thread before discarded cards can be GC'd elsewhere."""
        cards = tuple(self.review_cards.values())
        self.review_cards = {}
        self.correct_vars = {}
        self.detail_vars = {}
        self.status_var = self.answer_var = self.note_var = None
        for card in cards:
            card.detail_vars.clear()
            card.status_var = card.answer_var = card.note_var = None
            card.quick_confirm_var = None

    def show(self, items, message=""):
        self.items = tuple(items)
        self.correct_items = self._batch_items()
        correct_ids = {(item.document_id, item.question_id) for item in self.correct_items}
        detailed = tuple(item for item in self.items
                         if (item.document_id, item.question_id) not in correct_ids)
        self.item = detailed[0] if detailed else (self.correct_items[0] if self.correct_items else None)
        self.message_var.set(message or (f"还有 {len(items)} 道待确认题目。" if items else "所有待确认题目已处理。"))
        self.progress_var.set(f"共 {len(self.items)} 道待确认 · AI 判对 {len(self.correct_items)} 道 · 其他 {len(detailed)} 道")
        self.questions_title_var.set(f"逐题审核（{len(self.items)} 道）")
        self.correct_title_var.set(
            f"AI 判为正确的 {len(self.correct_items)} 道题也有完整依据；请逐项点击判断，可单独改判或勾选后批量保存。"
            if self.correct_items else "每道题都需要家长结合原图和 AI 依据作最终判断。")
        self._rebuild_question_cards()
        self._update_correct_selection()
        if not self.item:
            self.meta_var.set("复核完成")
            self.question_number_var.set("")
            self.detail_vars = {}
            self.save_button.configure(state="disabled", text="没有待保存的题目")
            self.open_button.configure(state="disabled")
            self.original_button.configure(state="disabled")
            self.preview.configure(image="", text="暂无待家长确认的题目。")
            return
        first_key = (self.item.document_id, self.item.question_id)
        self._activate_card(first_key, render_preview=True)
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
        if self._current_key is None:
            return
        self._save_card(self._current_key)

    def _save_card(self, key):
        card = self.review_cards.get(key)
        if card is None:
            return
        self._activate_card(key, render_preview=False)
        if card.status_var.get() not in {"correct", "incorrect", "partial"}:
            self.message_var.set("请先选择正确、错误或部分正确。")
            return
        if self.on_save(self.dialog_id, card.item, card.status_var.get(), card.answer_var.get(), card.note_var.get()):
            self.save_button.configure(state="disabled")
            self.batch_button.configure(state="disabled")
            for other in self.review_cards.values():
                other.save_button.configure(state="disabled")
            self.message_var.set("正在保存复核结果…")

    def confirm_correct_batch(self):
        selected = self._selected_correct_items()
        if not selected or self.on_batch is None:
            return
        if self.on_batch(self.dialog_id, selected):
            self.save_button.configure(state="disabled")
            self.batch_button.configure(state="disabled")
            for card in self.review_cards.values():
                card.save_button.configure(state="disabled")
            self.message_var.set(f"正在保存 {len(selected)} 道正确题…")

    def destroy(self):
        """Release Tcl objects on Tk's thread, even with stale callbacks."""
        self.item, self.items, self.correct_items = None, (), ()
        self._release_card_variables()
        self.window.destroy()
        self.on_save = self.on_batch = self.open_path = None
        self.detail_vars = {}
        for name in ("meta_var", "progress_var", "status_var", "answer_var", "note_var", "message_var",
                     "correct_title_var", "questions_title_var", "question_number_var", "_photo"):
            if hasattr(self, name): setattr(self, name, None)
