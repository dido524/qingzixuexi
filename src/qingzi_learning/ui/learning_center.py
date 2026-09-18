"""Resizable learning/report center kept separate from the capture surface."""

from __future__ import annotations

from pathlib import Path
from typing import Callable
import tkinter as tk
from tkinter import ttk

from qingzi_learning.exams.blueprint import ExamRequest


class ExamForm:
    """Small validated form; it never starts background work by itself."""

    def __init__(self, parent, subjects: tuple[str, ...]) -> None:
        self.frame = tk.Frame(parent, bg="#ffffff")
        self.subject = tk.StringVar(master=parent, value=subjects[1] if len(subjects) > 1 else subjects[0])
        self.scope = tk.StringVar(master=parent, value="")
        self.duration = tk.StringVar(master=parent, value="40")
        self.difficulty = tk.StringVar(master=parent, value="适中")
        self.question_count = tk.StringVar(master=parent, value="10")
        self.include_composition = tk.BooleanVar(master=parent, value=False)
        self.include_reading = tk.BooleanVar(master=parent, value=False)
        self.error_text = tk.StringVar(master=parent, value="")
        fields = (
            ("学科", ttk.Combobox(self.frame, textvariable=self.subject, values=subjects, state="readonly")),
            ("考试范围（可留空）", tk.Entry(self.frame, textvariable=self.scope)),
            ("时长（分钟）", tk.Entry(self.frame, textvariable=self.duration)),
            ("难度", ttk.Combobox(self.frame, textvariable=self.difficulty, values=("基础", "适中", "提高"), state="readonly")),
            ("题量", tk.Entry(self.frame, textvariable=self.question_count)),
        )
        self.widgets = []
        for row, (label, widget) in enumerate(fields):
            tk.Label(self.frame, text=label, bg="#ffffff", fg="#243447").grid(row=row * 2, column=0, sticky="w", pady=(8, 2))
            widget.grid(row=row * 2 + 1, column=0, sticky="ew")
            self.widgets.append(widget)
        options = tk.Frame(self.frame, bg="#ffffff")
        options.grid(row=10, column=0, sticky="ew", pady=(10, 0))
        tk.Checkbutton(options, text="包含作文", variable=self.include_composition, bg="#ffffff").pack(side="left")
        tk.Checkbutton(options, text="包含阅读", variable=self.include_reading, bg="#ffffff").pack(side="left")
        tk.Label(self.frame, textvariable=self.error_text, bg="#ffffff", fg="#a13b3b", justify="left", wraplength=270).grid(row=11, column=0, sticky="ew", pady=(8, 0))
        self.frame.grid_columnconfigure(0, weight=1)

    def build_request(self) -> ExamRequest | None:
        errors = []
        try:
            duration = int(self.duration.get())
            if not 10 <= duration <= 180:
                raise ValueError
        except ValueError:
            duration = 0
            errors.append("时长应为10到180分钟")
        try:
            count = int(self.question_count.get())
            if not 5 <= count <= 50:
                raise ValueError
        except ValueError:
            count = 0
            errors.append("题量应为5到50题")
        if self.subject.get() not in {"语文", "数学", "英语"}:
            errors.append("请选择学科")
        if self.difficulty.get() not in {"基础", "适中", "提高"}:
            errors.append("请选择难度")
        self.error_text.set("；".join(errors))
        if errors:
            return None
        return ExamRequest(
            self.subject.get(), self.scope.get().strip(), duration,
            self.difficulty.get(), count, self.include_composition.get(),
            self.include_reading.get(),
        )


class LearningCenterDialog:
    def __init__(
        self,
        parent,
        dialog_id: str,
        *,
        knowledge_root: Path,
        on_generate_report: Callable[[str], object],
        on_open: Callable[[Path], object],
        on_print: Callable[[Path], object],
        on_close: Callable[[str], object],
        on_preview_exam: Callable[[str, ExamRequest], object] | None = None,
        on_generate_exam: Callable[[str, ExamRequest], object] | None = None,
        on_approve_exam: Callable[[str, str, int], object] | None = None,
    ) -> None:
        self.dialog_id = dialog_id
        self.knowledge_root = Path(knowledge_root).resolve()
        self.on_generate_report = on_generate_report
        self.on_open = on_open
        self.on_print = on_print
        self.on_close = on_close
        self.on_preview_exam = on_preview_exam or (lambda *_args: None)
        self.on_generate_exam = on_generate_exam or (lambda *_args: None)
        self.on_approve_exam = on_approve_exam or (lambda *_args: None)
        self.runs = ()
        self.selected_run = None
        self.exam_runs = ()
        self.selected_exam = None
        self.exam_blueprint = None
        self.blueprint_request = None
        self._exam_traces: list[tuple[tk.Variable, str]] = []

        self.window = tk.Toplevel(parent)
        self.window.title("晴子学习与复习中心")
        self.window.configure(bg="#f4f7f8")
        width = min(1120, max(760, int(self.window.winfo_screenwidth() * .82)))
        height = min(780, max(560, int(self.window.winfo_screenheight() * .78)))
        self.window.geometry(f"{width}x{height}")
        self.window.minsize(720, 520)
        self.window.resizable(True, True)
        self.window.protocol("WM_DELETE_WINDOW", lambda: self.on_close(self.dialog_id))

        notebook = ttk.Notebook(self.window)
        notebook.pack(fill="both", expand=True, padx=16, pady=16)
        reports = tk.Frame(notebook, bg="#ffffff")
        exams = tk.Frame(notebook, bg="#ffffff")
        notebook.add(reports, text="学情报告")
        notebook.add(exams, text="模拟试卷")
        self._build_exam_tab(exams)

        reports.grid_columnconfigure(0, weight=1)
        reports.grid_rowconfigure(2, weight=1)
        tk.Label(
            reports, text="增量学情报告", bg="#ffffff", fg="#243447",
            font=("Microsoft YaHei UI", 18, "bold"),
        ).grid(row=0, column=0, sticky="w", padx=22, pady=(20, 4))
        tk.Label(
            reports,
            text="每次生成都会保留历史版本，并与上一份报告比较。孩子版和家长版可分别查看、分别打印。",
            bg="#ffffff", fg="#65758b", justify="left", wraplength=800,
            font=("Microsoft YaHei UI", 10),
        ).grid(row=1, column=0, sticky="ew", padx=22, pady=(0, 12))

        center = tk.Frame(reports, bg="#ffffff")
        center.grid(row=2, column=0, sticky="nsew", padx=22)
        center.grid_columnconfigure(0, weight=1)
        center.grid_rowconfigure(1, weight=1)
        tk.Label(center, text="历史报告", bg="#ffffff", fg="#243447",
                 font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.history = tk.Listbox(
            center, borderwidth=0, highlightthickness=1, highlightbackground="#dce5ed",
            selectbackground="#2f6f89", selectforeground="#ffffff",
            font=("Microsoft YaHei UI", 10), activestyle="none",
        )
        self.history.grid(row=1, column=0, sticky="nsew", pady=(6, 10))
        self.history.bind("<<ListboxSelect>>", self._select)

        self.message = tk.StringVar(master=self.window, value="正在读取报告记录……")
        tk.Label(reports, textvariable=self.message, bg="#eef5f7", fg="#36586a",
                 anchor="w", justify="left", padx=12, pady=8,
                 font=("Microsoft YaHei UI", 9)).grid(row=3, column=0, sticky="ew", padx=22, pady=(0, 10))

        buttons = tk.Frame(reports, bg="#ffffff")
        buttons.grid(row=4, column=0, sticky="ew", padx=18, pady=(0, 18))
        actions = (
            ("生成新报告", lambda: self.on_generate_report(self.dialog_id), "generate"),
            ("查看孩子版", self.open_child, "open_child"),
            ("打印孩子版", self.print_child, "print_child"),
            ("查看家长版", self.open_parent, "open_parent"),
            ("打印家长版", self.print_parent, "print_parent"),
        )
        self.buttons = {}
        for column, (label, command, name) in enumerate(actions):
            buttons.grid_columnconfigure(column, weight=1, uniform="report-actions")
            button = tk.Button(
                buttons, text=label, command=command, padx=8, pady=8,
                font=("Microsoft YaHei UI", 9, "bold"), relief="solid", borderwidth=1,
            )
            button.grid(row=0, column=column, sticky="ew", padx=4)
            self.buttons[name] = button
        self.set_busy(False)

    def _build_exam_tab(self, exams) -> None:
        exams.grid_columnconfigure(1, weight=1)
        exams.grid_rowconfigure(0, weight=1)
        left = tk.Frame(exams, bg="#ffffff", padx=20, pady=16)
        left.grid(row=0, column=0, sticky="nsw")
        tk.Label(left, text="针对性模拟试卷", bg="#ffffff", fg="#243447", font=("Microsoft YaHei UI", 16, "bold")).pack(anchor="w")
        tk.Label(left, text="先查看组卷依据，再生成题目。生成后需家长确认才能打印。", bg="#ffffff", fg="#65758b", wraplength=280, justify="left").pack(anchor="w", pady=(4, 8))
        self.exam_form = ExamForm(left, ("语文", "数学", "英语"))
        self.exam_form.frame.pack(fill="x")
        actions = tk.Frame(left, bg="#ffffff")
        actions.pack(fill="x", pady=(12, 0))
        self.exam_buttons = {}
        for label, name, command in (
            ("预览组卷依据", "preview", self.preview_exam),
            ("生成模拟卷", "generate", self.generate_exam),
            ("家长确认并发布", "approve", self.approve_exam),
        ):
            button = tk.Button(actions, text=label, command=command, pady=7, relief="solid", borderwidth=1)
            button.pack(fill="x", pady=3)
            self.exam_buttons[name] = button

        right = tk.Frame(exams, bg="#f7f9fa", padx=16, pady=16)
        right.grid(row=0, column=1, sticky="nsew")
        right.grid_columnconfigure(0, weight=1)
        right.grid_rowconfigure(3, weight=1)
        tk.Label(right, text="历史模拟卷", bg="#f7f9fa", fg="#243447", font=("Microsoft YaHei UI", 11, "bold")).grid(row=0, column=0, sticky="w")
        self.exam_history = tk.Listbox(right, height=5, selectbackground="#2f6f89", activestyle="none")
        self.exam_history.grid(row=1, column=0, sticky="ew", pady=(5, 10))
        self.exam_history.bind("<<ListboxSelect>>", self._select_exam)
        tk.Label(right, text="家长预览 / 组卷依据", bg="#f7f9fa", fg="#243447", font=("Microsoft YaHei UI", 11, "bold")).grid(row=2, column=0, sticky="w")
        self.exam_preview = tk.Text(right, wrap="word", state="disabled", bg="#ffffff", relief="solid", borderwidth=1, padx=12, pady=10)
        self.exam_preview.grid(row=3, column=0, sticky="nsew", pady=(5, 10))
        self.exam_message = tk.StringVar(master=self.window, value="先填写条件并预览组卷依据。")
        tk.Label(right, textvariable=self.exam_message, bg="#e9f2f5", fg="#36586a", anchor="w", padx=10, pady=7).grid(row=4, column=0, sticky="ew")
        print_bar = tk.Frame(right, bg="#f7f9fa")
        print_bar.grid(row=5, column=0, sticky="ew", pady=(10, 0))
        for column, (label, key) in enumerate((
            ("打印学生试卷", "student"), ("打印答题纸", "answer_sheet"),
            ("打印答案解析", "solutions"), ("打印组卷说明", "blueprint"),
        )):
            print_bar.grid_columnconfigure(column, weight=1)
            button = tk.Button(print_bar, text=label, command=lambda k=key: self.print_exam(k), pady=6)
            button.grid(row=0, column=column, sticky="ew", padx=3)
            self.exam_buttons[f"print_{key}"] = button
        for variable in (
            self.exam_form.subject, self.exam_form.scope, self.exam_form.duration,
            self.exam_form.difficulty, self.exam_form.question_count,
            self.exam_form.include_composition, self.exam_form.include_reading,
        ):
            token = variable.trace_add("write", self._invalidate_blueprint)
            self._exam_traces.append((variable, token))
        self._refresh_exam_buttons(False)

    @property
    def child_print_path(self) -> Path | None:
        return self._artifact("child")

    @property
    def parent_print_path(self) -> Path | None:
        return self._artifact("parent")

    def show(self, runs, message: str, artifacts) -> None:
        selected_id = artifacts.report_id if artifacts is not None else (
            self.selected_run.report_id if self.selected_run is not None else None
        )
        self.runs = tuple(runs)
        self.history.delete(0, "end")
        for run in self.runs:
            status = {"completed": "已完成", "failed": "失败", "generating": "生成中"}.get(run.status, run.status)
            self.history.insert("end", f"{run.created_at}  ·  {status}  ·  {run.report_id}")
        index = next((i for i, run in enumerate(self.runs) if run.report_id == selected_id), 0)
        if self.runs:
            self.history.selection_set(index)
            self.history.see(index)
            self.selected_run = self.runs[index]
        else:
            self.selected_run = None
        self.message.set(message or ("报告已生成。" if artifacts else "尚未生成报告。"))
        self.set_busy(False)

    def set_busy(self, busy: bool) -> None:
        completed = self.selected_run is not None and self.selected_run.status == "completed"
        self.buttons["generate"].configure(state="disabled" if busy else "normal")
        for name in ("open_child", "print_child", "open_parent", "print_parent"):
            self.buttons[name].configure(state="normal" if completed and not busy else "disabled")
        self._refresh_exam_buttons(busy)

    def show_exams(self, runs, message: str, blueprint, artifacts, *, selected_exam_id: str | None = None) -> None:
        selected_id = selected_exam_id or getattr(self.selected_exam, "exam_id", None)
        self.exam_runs = tuple(runs)
        self.exam_history.delete(0, "end")
        labels = {"draft": "草稿", "needs_parent_approval": "待家长确认", "approved": "已批准", "failed": "失败"}
        for run in self.exam_runs:
            self.exam_history.insert("end", f"{run.created_at} · {labels.get(run.status, run.status)} · {run.subject} · {run.exam_id}")
        if artifacts is not None:
            approved = next((run for run in self.exam_runs if run.status == "approved"), None)
            selected_id = getattr(approved, "exam_id", selected_id)
        index = next((i for i, run in enumerate(self.exam_runs) if run.exam_id == selected_id), 0)
        if self.exam_runs:
            self.exam_history.selection_set(index)
            self.selected_exam = self.exam_runs[index]
        else:
            self.selected_exam = None
        if blueprint is not None:
            self.exam_blueprint = blueprint
            self.blueprint_request = self.exam_form.build_request()
            self._show_blueprint(blueprint)
        else:
            self._show_selected_exam()
        self.exam_message.set(message or "模拟卷记录已加载。")
        self._refresh_exam_buttons(False)

    @property
    def exam_print_paths(self) -> tuple[Path, ...]:
        return tuple(path for key in ("student", "answer_sheet", "solutions", "blueprint") if (path := self._exam_artifact(key)) is not None)

    def preview_exam(self) -> None:
        request = self.exam_form.build_request()
        if request is not None:
            if self.on_preview_exam(self.dialog_id, request):
                self.exam_message.set("正在计算组卷依据，请稍候……")
                self.set_busy(True)
            else:
                self.exam_message.set("当前还有任务正在处理，请稍候再试。")

    def generate_exam(self) -> None:
        request = self.exam_form.build_request()
        if request is None:
            return
        if self.exam_blueprint is None or request != self.blueprint_request:
            self.exam_message.set("条件已变化，请重新预览组卷依据。")
            return
        if self.on_generate_exam(self.dialog_id, request):
            self.exam_message.set("正在生成并校验模拟卷，请稍候……")
            self.set_busy(True)

    def approve_exam(self) -> None:
        if self.selected_exam is not None and self.selected_exam.status == "needs_parent_approval":
            self.on_approve_exam(self.dialog_id, self.selected_exam.exam_id, self.selected_exam.revision)

    def print_exam(self, key: str) -> None:
        self._act(self._exam_artifact(key), self.on_print)

    def _select_exam(self, _event=None) -> None:
        selected = self.exam_history.curselection()
        if selected and selected[0] < len(self.exam_runs):
            self.selected_exam = self.exam_runs[selected[0]]
        self._show_selected_exam()
        self._refresh_exam_buttons(False)

    def _show_selected_exam(self) -> None:
        run = self.selected_exam
        if run is None:
            self._set_exam_preview("尚无模拟卷。")
            return
        lines = [f"{run.generation.get('title', '模拟卷')}\n", f"状态：{run.status}\n", f"试卷编号：{run.exam_id}\n"]
        for item in run.generation.get("questions", []):
            lines.append(f"\n{item.get('question_id', '')}（{item.get('points', '')}分）\n{item.get('prompt', '')}\n")
            if run.status == "needs_parent_approval":
                lines.append(f"答案：{item.get('answer', '')}\n解析：{item.get('explanation', '')}\n")
        self._set_exam_preview("".join(lines))

    def _show_blueprint(self, blueprint) -> None:
        allocation = blueprint.get("allocation", {})
        lines = [
            "组卷依据\n",
            f"重点短板：{allocation.get('primary', 0)}题；关联巩固：{allocation.get('related', 0)}题；稳定保持：{allocation.get('stable', 0)}题。\n",
            str(blueprint.get("allocation_note", "")) + "\n",
        ]
        for target in blueprint.get("targets", []):
            lines.append(f"· {target.get('knowledge_point', '')}（{target.get('category', '')}）\n")
        self._set_exam_preview("".join(lines))

    def _set_exam_preview(self, text: str) -> None:
        self.exam_preview.configure(state="normal")
        self.exam_preview.delete("1.0", "end")
        self.exam_preview.insert("1.0", text)
        self.exam_preview.configure(state="disabled")

    def _invalidate_blueprint(self, *_args) -> None:
        if self.blueprint_request is not None:
            request = self.exam_form.build_request()
            if request != self.blueprint_request:
                self.exam_blueprint = None
                self.blueprint_request = None
                self.exam_message.set("条件已变化，请重新预览组卷依据。")
                self._refresh_exam_buttons(False)

    def _refresh_exam_buttons(self, busy: bool) -> None:
        if not hasattr(self, "exam_buttons"):
            return
        self.exam_buttons["preview"].configure(state="disabled" if busy else "normal")
        self.exam_buttons["generate"].configure(state="normal" if self.exam_blueprint is not None and not busy else "disabled")
        approvable = self.selected_exam is not None and self.selected_exam.status == "needs_parent_approval"
        self.exam_buttons["approve"].configure(state="normal" if approvable and not busy else "disabled")
        for key in ("student", "answer_sheet", "solutions", "blueprint"):
            enabled = self._exam_artifact(key) is not None and not busy
            self.exam_buttons[f"print_{key}"].configure(state="normal" if enabled else "disabled")

    def _exam_artifact(self, key: str) -> Path | None:
        if self.selected_exam is None or self.selected_exam.status != "approved":
            return None
        relative = self.selected_exam.output_files.get(key)
        if not relative:
            return None
        candidate = (self.knowledge_root / relative).resolve()
        try:
            candidate.relative_to(self.knowledge_root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    def open_child(self) -> None:
        self._act(self.child_print_path, self.on_open)

    def print_child(self) -> None:
        self._act(self.child_print_path, self.on_print)

    def open_parent(self) -> None:
        self._act(self.parent_print_path, self.on_open)

    def print_parent(self) -> None:
        self._act(self.parent_print_path, self.on_print)

    def _select(self, _event=None) -> None:
        selected = self.history.curselection()
        if selected and selected[0] < len(self.runs):
            self.selected_run = self.runs[selected[0]]
        self.set_busy(False)

    def _artifact(self, key: str) -> Path | None:
        if self.selected_run is None or self.selected_run.status != "completed":
            return None
        relative = self.selected_run.output_files.get(key)
        if not relative:
            return None
        candidate = (self.knowledge_root / relative).resolve()
        try:
            candidate.relative_to(self.knowledge_root)
        except ValueError:
            return None
        return candidate if candidate.is_file() else None

    @staticmethod
    def _act(path: Path | None, callback: Callable[[Path], object]) -> None:
        if path is not None:
            callback(path)

    def destroy(self) -> None:
        for variable, token in self._exam_traces:
            try:
                variable.trace_remove("write", token)
            except tk.TclError:
                pass
        self._exam_traces.clear()
        try:
            self.window.destroy()
        except tk.TclError:
            pass
