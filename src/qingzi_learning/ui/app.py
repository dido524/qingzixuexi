"""Tk UI: only frozen events cross from its worker-owned camera/database layer."""
from __future__ import annotations

from dataclasses import dataclass, replace
from io import BytesIO
from pathlib import Path
import os
import queue
import threading
import time
from typing import Callable, Literal, Protocol
from uuid import uuid4
import webbrowser

from PIL import Image, ImageTk
try:
    import tkinter as tk
except ImportError:  # pragma: no cover
    tk = None

from qingzi_learning.camera.devices import CameraBusy, CameraNotFound
from qingzi_learning.camera.quality import ImageQualityRejected
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig
from qingzi_learning.workflow.controller import WorkflowOutcome
from qingzi_learning.ui.camera_process import CameraProcess
from qingzi_learning.ui.page_subject_dialog import PageSubjectDialog
from qingzi_learning.ui.learning_center import LearningCenterDialog
from qingzi_learning.ui.taskbar import TaskbarNotifier
from qingzi_learning.review.service import ReviewItem
from qingzi_learning.ui.review_dialog import ReviewDialog
from qingzi_learning.reporting.service import ReportArtifacts
from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.exams.service import ExamArtifacts, ExamGenerationError
from qingzi_learning.storage.repository import ExamRun, ReportRun


class _Camera(Protocol):
    def read(self) -> tuple[bool, object]: ...
    def release(self) -> None: ...


@dataclass(frozen=True)
class CompletionSummary:
    saved_folder: Path | None = None
    question_count: int = 0
    error_count: int = 0
    weak_knowledge_points: tuple[str, ...] = ()
    review_count: int = 0
    analysis_details_path: Path | None = None
    library_pending_count: int = 0


@dataclass(frozen=True)
class UiEvent:
    kind: str
    operation_id: str | None = None
    message: str = ""
    page_number: int | None = None
    page_path: Path | None = None
    reasons: tuple[str, ...] = ()
    outcome: WorkflowOutcome | None = None
    completion: CompletionSummary | None = None
    preview_jpeg: bytes | None = None
    context: str = "active_capture"
    target_id: str | None = None
    page_paths: tuple[Path, ...] = ()
    selected_page_number: int | None = None
    session_generation: str | None = None
    session_id: str | None = None
    dialog_id: str | None = None
    review_items: tuple[ReviewItem, ...] = ()
    review_revision: int = 0
    report_runs: tuple[ReportRun, ...] = ()
    report_artifacts: ReportArtifacts | None = None
    exam_runs: tuple[ExamRun, ...] = ()
    exam_blueprint: dict | None = None
    exam_artifacts: ExamArtifacts | None = None


@dataclass(frozen=True)
class _Command:
    kind: Literal["capture", "retake", "retake_page", "next", "finish", "new_capture", "confirm_subject", "confirm_page_subject", "retry", "recover", "resume_capture", "list_reviews", "confirm_review", "list_reports", "generate_report", "list_exams", "preview_exam_blueprint", "generate_exam", "approve_exam", "stop"]
    operation_id: str
    job_id: str | None = None
    subject: str | None = None
    page_number: int | None = None
    context: str = "active_capture"
    session_generation: str | None = None
    session_id: str | None = None
    dialog_id: str | None = None
    question_id: str | None = None
    final_status: str | None = None
    corrected_answer: str = ""
    note: str = ""
    expected_version: str | None = None
    exam_request: ExamRequest | None = None
    exam_id: str | None = None
    expected_revision: int | None = None


class WorkflowWorker(threading.Thread):
    """One non-daemon worker owns controller/repository/camera/session exclusively."""
    def __init__(self, config: AppConfig, controller_factory, *, camera_factory=None, session_factory=CaptureSession,
                 events: queue.Queue[UiEvent] | None = None, isolated_camera=False, camera_process_factory=CameraProcess,
                 monotonic: Callable[[], float] = time.monotonic) -> None:
        super().__init__(name="晴子学习助手工作线程", daemon=False)
        self.config, self._controller_factory = config, controller_factory
        self._camera_factory, self._session_factory, self._isolated_camera, self._camera_process_factory = camera_factory, session_factory, isolated_camera, camera_process_factory
        self.events: queue.Queue[UiEvent] = events or queue.Queue()
        self.preview_events: queue.Queue[UiEvent] = queue.Queue(maxsize=1)
        self._commands: queue.Queue[_Command] = queue.Queue()
        self._stop_requested = threading.Event()
        self._controller = self._camera = self._session = None
        self._session_generation = self._session_id = None
        self._monotonic = monotonic
        self._last_preview = float("-inf")
        self._preview_retry_at = 0.0
        self._preview_retry_delay = .25

    def submit(self, kind, *, job_id=None, subject=None, page_number=None, context="active_capture",
               session_generation=None, session_id=None, dialog_id=None, question_id=None,
               final_status=None, corrected_answer="", note="", expected_version=None,
               exam_request=None, exam_id=None, expected_revision=None) -> str:
        operation_id = uuid4().hex
        if kind == "resume_capture":
            generation = session_generation or uuid4().hex
            identity = Path(job_id).name
        elif kind == "new_capture":
            generation = session_generation or uuid4().hex
            identity = None
        elif context in {"recovered_job", "review_job", "learning_center"}:
            generation, identity = session_generation, session_id
        else:
            generation = session_generation or self._session_generation or uuid4().hex
            identity = session_id or self._session_id
        self._commands.put(_Command(kind, operation_id, job_id, subject, page_number, context,
                                    generation, identity, dialog_id, question_id, final_status,
                                    corrected_answer, note, expected_version, exam_request,
                                    exam_id, expected_revision))
        return operation_id

    def request_shutdown(self) -> None:
        """Prompt, non-blocking request. A cancellable analyzer is asked to stop."""
        self._stop_requested.set()
        camera_cancel = getattr(self._camera, "cancel", None)
        if callable(camera_cancel):
            camera_cancel()
        cancel = getattr(self._controller, "cancel", None)
        if callable(cancel):
            cancel()
        self._commands.put(_Command("stop", uuid4().hex))

    stop = request_shutdown

    def run(self) -> None:
        try:
            self._controller = self._controller_factory()
            self._recover()
            while not self._stop_requested.is_set():
                try:
                    command = self._commands.get(timeout=.05)
                except queue.Empty:
                    self._preview()
                    continue
                if command.kind == "stop":
                    break
                self._process(command)
        except Exception:
            self._emit(UiEvent("worker_error", message="后台服务无法启动，请检查本机资料库后重试。"))
        finally:
            self._release()
            if self._stop_requested.is_set(): self._emit(UiEvent("shutdown_ack"))

    def _process(self, command: _Command) -> None:
        try:
            if command.kind in {"capture", "retake", "retake_page", "next", "finish"} and self._session is not None:
                if command.session_generation != self._session_generation or (
                        command.session_id is not None and command.session_id != self._session_id):
                    raise ValueError("capture session changed")
            if command.kind == "capture": self._capture(command, False)
            elif command.kind in {"retake", "retake_page"}: self._capture(command, True)
            elif command.kind == "next":
                if self._session is None: raise ValueError("no active capture")
                n = self._session.next_page(); self._emit(self._command_event(command, "next_page", page_number=n))
            elif command.kind == "finish":
                if self._session is None: raise ValueError("no active capture")
                outcome = self._controller.finish_and_analyze(self._session)
                self._emit(self._command_event(command, "workflow_outcome", outcome=outcome, completion=self._completion(outcome)))
            elif command.kind == "new_capture": self._new_capture(command)
            elif command.kind == "confirm_subject":
                outcome = self._controller.confirm_subject(command.job_id, command.subject)
                self._emit(self._command_event(command, "workflow_outcome", outcome=outcome, completion=self._completion(outcome)))
            elif command.kind == "confirm_page_subject":
                outcome = self._controller.confirm_page_subject(command.job_id, command.page_number, command.subject)
                self._emit(self._command_event(command, "workflow_outcome", outcome=outcome, completion=self._completion(outcome)))
            elif command.kind == "retry":
                outcome = self._controller.retry_pending(command.job_id)
                self._emit(self._command_event(command, "workflow_outcome", outcome=outcome, completion=self._completion(outcome), context="recovered_job"))
            elif command.kind == "recover": self._recover()
            elif command.kind == "resume_capture": self._resume(command)
            elif command.kind in {"list_reviews", "confirm_review"}: self._review(command)
            elif command.kind in {"list_reports", "generate_report"}: self._reports(command)
            elif command.kind in {"list_exams", "preview_exam_blueprint", "generate_exam", "approve_exam"}: self._exams(command)
        except CameraNotFound:
            self._camera_failed()
            self._emit(self._command_event(command, "camera_unavailable", message=f"未找到证件拍照机（硬件 ID {self.config.camera_vid:04X}:{self.config.camera_pid:04X}）。未切换到其他摄像头。"))
        except CameraBusy:
            self._camera_failed()
            self._emit(self._command_event(command, "camera_busy", message="证件拍照机暂时不可用，请检查是否被其他程序占用。"))
        except ImageQualityRejected as exc:
            self._emit(self._command_event(command, "quality_rejected", reasons=exc.result.reasons))
        except Exception:
            self._emit(self._command_event(command, "worker_error", message="本次操作未完成，已保留资料，可重试或关闭后恢复。"))

    def _command_event(self, command, kind, **values):
        values.setdefault("context", command.context)
        identity=command.session_id
        if identity is None and command.context == "active_capture": identity=self._session_id
        outcome = values.get("outcome")
        revision = getattr(self._controller.repo, "review_publication_revision", None)
        if outcome is not None and callable(revision):
            values["review_revision"] = revision(outcome.job_id)
        return UiEvent(kind, command.operation_id, target_id=command.job_id,
                       session_generation=command.session_generation,
                       session_id=identity, dialog_id=command.dialog_id, **values)

    def _review(self, command):
        service = self._controller.review
        if command.kind == "list_reviews":
            self._emit(self._command_event(command, "review_list", review_items=service.list_pending()))
            return
        try:
            service.confirm_question(command.job_id, command.question_id, command.final_status,
                                     command.corrected_answer, command.note, expected_version=command.expected_version)
        except ValueError:
            # Reload actual pending facts; arbitrary exception text is not shown.
            self._emit(self._command_event(command, "review_conflict", review_items=service.list_pending(),
                                          message="题目已变化或无法确认，已重新加载。请选择最终状态后再保存。"))
            return
        job = self._controller.repo.get_job(command.job_id)
        outcome = self._controller._outcome(job)
        message = "复核已保存，知识库已更新。" if not job.payload.get("export_pending") else "复核已保存，页面更新未完成，请在恢复任务中重试。"
        self._emit(self._command_event(command, "review_saved", outcome=outcome, completion=self._completion(outcome),
                                      review_items=service.list_pending(), message=message))

    def _reports(self, command):
        if command.kind == "list_reports":
            exam_history = getattr(self._controller, "exam_history", lambda: ())
            self._emit(self._command_event(
                command, "report_list", report_runs=self._controller.report_history(),
                exam_runs=exam_history(),
                message="报告记录已加载。",
            ))
            return
        artifacts = self._controller.generate_learning_report()
        self._emit(self._command_event(
            command, "report_generated", report_runs=self._controller.report_history(),
            report_artifacts=artifacts, message="新报告已生成，可以分别查看或打印。",
        ))

    def _exams(self, command):
        if command.kind == "list_exams":
            self._emit(self._command_event(
                command, "exam_list", exam_runs=self._controller.exam_history(),
                message="模拟卷记录已加载。",
            ))
            return
        if command.kind == "preview_exam_blueprint":
            try:
                blueprint = self._controller.preview_exam_blueprint(command.exam_request)
            except ValueError as exc:
                detail = str(exc)
                if "考试范围" in detail:
                    message = "当前考试范围没有可用的已确认题目，请调整范围或留空。"
                elif "该学科" in detail or "有效学习证据" in detail:
                    message = "该学科暂时没有可用于组卷的已确认学习记录。"
                else:
                    message = "暂时无法生成组卷依据，请检查条件后重试。"
                self._emit(self._command_event(
                    command, "exam_failed", exam_runs=self._controller.exam_history(),
                    message=message,
                ))
                return
            self._emit(self._command_event(
                command, "exam_blueprint", exam_runs=self._controller.exam_history(),
                exam_blueprint=blueprint, message="组卷依据已生成，请确认后再生成题目。",
            ))
            return
        if command.kind == "generate_exam":
            try:
                self._controller.generate_exam(command.exam_request)
            except ExamGenerationError:
                self._emit(self._command_event(
                    command, "exam_failed", exam_runs=self._controller.exam_history(),
                    message="这次题目未通过质量校验，请点击“生成模拟卷”再试一次。",
                ))
                return
            self._emit(self._command_event(
                command, "exam_generated", exam_runs=self._controller.exam_history(),
                message="模拟卷已通过校验，请家长预览答案后确认发布。",
            ))
            return
        artifacts = self._controller.approve_exam(command.exam_id, command.expected_revision)
        self._emit(self._command_event(
            command, "exam_approved", exam_runs=self._controller.exam_history(),
            exam_artifacts=artifacts, message="模拟卷已批准，四份材料可以分别打印。",
        ))

    def _capture(self, command, retake: bool) -> None:
        if self._session is None:
            self._session = self._session_factory(self.config)
            self._session_generation = command.session_generation
            self._session_id = Path(getattr(self._session, "session_dir", command.session_generation)).name
        ok, frame = self._camera_or_open().read()
        if not ok or frame is None: raise CameraBusy()
        self._camera_succeeded()
        if retake:
            target = command.page_number or self._session.current_page_number
            page = self._session.retake_page(target, frame)
        else: page = self._session.capture(frame)
        self._emit(self._command_event(command, "page_retaken" if retake else "page_captured",
                           page_number=page.page_number, page_path=Path(page.path), preview_jpeg=_page_preview(page.path)))

    def _new_capture(self, command) -> None:
        self._session = None
        self._session_generation = command.session_generation or uuid4().hex
        self._session_id = None
        self._last_preview = float("-inf")
        while not self.preview_events.empty():
            try:self.preview_events.get_nowait()
            except queue.Empty:break
        self._emit(self._command_event(command, "new_capture_ready"))

    def _camera_or_open(self):
        if self._camera is None:
            self._camera = self._camera_process_factory(self.config) if self._isolated_camera else self._camera_factory()
            # OpenCV's MSMF/DSHOW backends honor these where supported.  They keep
            # a device read from becoming an unbounded UI-shutdown dependency; an
            # unsupported backend simply ignores the best-effort settings.
            setter = getattr(self._camera, "set", None)
            if callable(setter):
                try:
                    setter(53, 1000)  # CAP_PROP_OPEN_TIMEOUT_MSEC
                    setter(54, 1000)  # CAP_PROP_READ_TIMEOUT_MSEC
                except Exception: pass
        return self._camera

    def _preview(self) -> None:
        if self._stop_requested.is_set(): return
        if self._session is not None and self._session.finished: return
        if self.preview_events.full(): return
        now = self._monotonic()
        if now < self._preview_retry_at or now - self._last_preview < .18: return
        try:
            ok, frame = self._camera_or_open().read()
            if not ok or frame is None: raise CameraBusy()
            self._camera_succeeded()
            self._last_preview = self._monotonic()
            image = _frame_preview(frame)
            if image: self.preview_events.put_nowait(UiEvent("preview", preview_jpeg=image,
                                         session_generation=self._session_generation, session_id=self._session_id))
        except queue.Full: pass
        except (CameraBusy, CameraNotFound, OSError, ValueError): self._camera_failed()

    def _release_camera(self) -> None:
        camera, self._camera = self._camera, None
        if camera is not None:
            try: camera.release()
            except Exception: pass

    def _camera_failed(self) -> None:
        # A failed/invalidated helper is never retained for the next user action.
        # Idle preview retries wait without sleeping the command-processing loop.
        self._release_camera()
        self._preview_retry_at = self._monotonic() + self._preview_retry_delay
        self._preview_retry_delay = min(self._preview_retry_delay * 2, 4.0)

    def _camera_succeeded(self) -> None:
        self._preview_retry_at = 0.0
        self._preview_retry_delay = .25

    def _recover(self) -> None:
        for outcome in self._controller.recover_jobs():
            self._emit(UiEvent("recovered_job", outcome=outcome, completion=self._completion(outcome)))
        root = self.config.spool_root
        if root.exists():
            for path in root.iterdir():
                try:
                    captured = CaptureSession.recover(path)
                    if not captured.finished: self._emit(UiEvent("recovered_capture", page_path=path))
                except (OSError, ValueError): pass

    def _resume(self, command) -> None:
        session = CaptureSession.recover(Path(command.job_id))
        if session.finished: raise ValueError("capture sealed")
        self._session = session
        self._session_generation = command.session_generation
        self._session_id = session.session_dir.name
        selected = self._session.pages[-1] if self._session.pages else None
        self._emit(self._command_event(command, "capture_resumed", page_path=self._session.session_dir,
                           page_number=self._session.current_page_number,
                           page_paths=tuple(page.path for page in self._session.pages),
                           selected_page_number=selected.page_number if selected else None,
                           preview_jpeg=_page_preview(selected.path) if selected else None))

    def _completion(self, outcome) -> CompletionSummary:
        folder = outcome.archived_pages[0].parent if outcome.archived_pages else outcome.recovery_path
        if outcome.error_code == "recovery_failed": return CompletionSummary(saved_folder=folder)
        try:
            if folder is None:
                job = self._controller.repo.get_job(outcome.job_id)
                if job and job.payload.get("session_dir"):
                    folder = Path(job.payload["session_dir"])
            doc = self._controller.repo.get_document(outcome.job_id)
            snapshot = self._controller.repo.dashboard_snapshot()
            document_ids = (outcome.job_id,) if doc else outcome.child_document_ids
            questions_by_identity = {}
            for document_id in document_ids:
                document = doc if document_id == outcome.job_id else self._controller.repo.get_document(document_id)
                for index, question in enumerate(document["questions"] if document else ()):
                    # Child documents may independently use the same question ID.
                    # Only suppress a duplicated row within one document.
                    questions_by_identity.setdefault((document_id, question.get("question_id", index)), question)
            questions = tuple(questions_by_identity.values())
            weak = tuple(dict.fromkeys(p for q in questions if q["status"] in {"incorrect", "partial"} for p in q["knowledge_points"]))[:3]
            return CompletionSummary(folder, len(questions), sum(q["status"] in {"incorrect", "partial"} for q in questions), weak,
                                     sum(q["status"] == "needs_review" for q in questions), outcome.analysis_markdown,
                                     int(snapshot["summary"]["pending_count"]))
        except (AttributeError, KeyError, TypeError, ValueError): return CompletionSummary(saved_folder=folder, analysis_details_path=outcome.analysis_markdown)

    def _release(self) -> None:
        self._release_camera()
        if self._controller is not None:
            try: self._controller.repo.close()
            except Exception: pass

    def _emit(self, event): self.events.put(event)


class CaptureViewModel:
    def __init__(self, session=None) -> None:
        self.session_generation = uuid4().hex
        self.session_id = session.session_dir.name if session else None
        self.page_number = session.current_page_number if session else 1
        self.page_paths = tuple(p.path for p in session.pages) if session else ()
        self.captured_page_count = len(self.page_paths)
        self._captured_current = bool(session and any(p.page_number == self.page_number for p in session.pages))
        self.busy = self.sealed = self.close_requested = self.worker_failed = False
        self.active_operation_id = None; self.status = "请将资料放在证件拍照机下。"; self.error_message = ""
        self.frozen_preview_jpeg = self.live_preview_jpeg = None
        self.selected_page_number = None; self.subject_confirmation_needed = False; self.pending_job_id = None
        self.pending_page_subjects = (); self.page_subject_dialog_needed = False; self.active_page_subject = None
        self.page_subject_dialog_id = None; self._page_subject_job_id = None
        self.page_subject_index = self.page_subject_total = 0
        self._page_subject_operations = {}; self.recovered_page_subject_queue = []
        self.recovered_tasks = {}; self.recovery_error_path = None; self.completion = CompletionSummary()
        self.review_dialog_id = None; self.review_items = (); self.review_message = ""
        self.review_revisions = {}
        self.learning_center_dialog_id = None; self.report_runs = (); self.report_artifacts = None; self.report_message = ""
        self.exam_runs = (); self.exam_blueprint = None; self.exam_artifacts = None; self.exam_message = ""

    @property
    def can_capture(self): return not self.worker_failed and not self.busy and not self.sealed and not self._captured_current
    @property
    def can_retake(self): return not self.worker_failed and not self.busy and not self.sealed and self.captured_page_count > 0
    @property
    def can_next(self): return not self.worker_failed and not self.busy and not self.sealed and self._captured_current
    @property
    def can_finish(self): return self.can_next
    @property
    def can_start_new(self): return not self.worker_failed and not self.busy and self.sealed

    def begin_work(self, message, operation_id=None): self.busy=True; self.active_operation_id=operation_id; self.status=message
    def finish_work(self, operation_id=None):
        if operation_id is None or operation_id == self.active_operation_id: self.busy=False; self.active_operation_id=None
    def on_page_captured(self, n=None, path=None):
        self.page_number=n or self.page_number; self._captured_current=True; self.captured_page_count=max(self.captured_page_count,self.page_number)
        if path and path not in self.page_paths: self.page_paths=(*self.page_paths,path)
        if path: self.completion=replace(self.completion,saved_folder=Path(path).parent)
        self.selected_page_number=self.page_number
    def on_page_retaken(self,n=None,path=None):
        # Retaking a thumbnail changes only that selected evidence page; the
        # capture cursor remains where the durable session reports it.
        if path and n and n <= len(self.page_paths):
            pages=list(self.page_paths); pages[n-1]=path; self.page_paths=tuple(pages)
        self.selected_page_number=n
    def on_next_page(self,n=None): self.page_number=n or self.page_number+1; self._captured_current=False; self.selected_page_number=None; self.frozen_preview_jpeg=None
    def on_new_capture_ready(self,generation):
        self.session_generation=generation or uuid4().hex; self.session_id=None
        self.page_number=1; self.page_paths=(); self.captured_page_count=0; self._captured_current=False
        self.sealed=False; self.selected_page_number=None; self.frozen_preview_jpeg=None; self.live_preview_jpeg=None
        self.subject_confirmation_needed=False; self.pending_job_id=None; self._clear_page_subject_confirmation(); self.recovery_error_path=None
        self.completion=CompletionSummary(); self.error_message=""; self.status="请放好下一份资料，然后拍下第一页。"
    def _clear_page_subject_confirmation(self):
        self.pending_page_subjects=(); self.page_subject_dialog_needed=False; self.active_page_subject=None
        self.page_subject_dialog_id=None; self._page_subject_job_id=None
        self.page_subject_index=self.page_subject_total=0
    def _apply_page_subject_outcome(self,outcome):
        pending=tuple(sorted(outcome.pending_page_subjects,key=lambda item:item.page))
        if outcome.state == "needs_subject_confirmation" and pending:
            if not self.page_subject_dialog_needed or self._page_subject_job_id != outcome.job_id:
                self.page_subject_dialog_id=uuid4().hex; self._page_subject_job_id=outcome.job_id
            self.pending_page_subjects=pending; self.page_subject_dialog_needed=True; self.active_page_subject=pending[0]
            self.page_subject_index=outcome.page_subject_index or 1
            self.page_subject_total=outcome.page_subject_total or len(pending)
            return True
        self._clear_page_subject_confirmation()
        return False
    def defer_page_subject_dialog(self,dialog_id):
        if dialog_id is None or dialog_id != self.page_subject_dialog_id: return False
        self.page_subject_dialog_needed=False; self.page_subject_dialog_id=None
        return True
    def on_workflow_outcome(self,outcome,completion=None,*,active_capture=True):
        """Reduce every durable outcome; recovery retries must not replace capture UI."""
        page_confirmation=self._apply_page_subject_outcome(outcome)
        if not active_capture: return page_confirmation
        self.completion=completion or self.completion; self.sealed=True
        self.subject_confirmation_needed=False; self.pending_job_id=None
        if outcome.error_code == "recovery_failed": self.recovery_error_path=outcome.recovery_path; self.status="恢复失败，原始资料位置可打开查看。"; return
        if page_confirmation:
            self.status=f"请确认第 {self.active_page_subject.page} 页的科目后继续。"
            self.recovered_tasks[outcome.job_id]=UiEvent("recovered_job",outcome=outcome,completion=completion,
                                                       session_generation=self.session_generation, session_id=self.session_id)
            return
        if outcome.state == "needs_subject_confirmation" and outcome.subject is None:
            self.pending_job_id=outcome.job_id; self.subject_confirmation_needed=True; self.status="请选择科目后继续。"
            self.recovered_tasks[outcome.job_id]=UiEvent("recovered_job",outcome=outcome,completion=completion,
                                                       session_generation=self.session_generation, session_id=self.session_id)
        elif outcome.state == "pending":
            messages = {
                "cli_unavailable": "未找到 Codex CLI：请先安装 Codex，并登录当前 ChatGPT 账号后在恢复任务中重试。",
                "cli_failed": "Codex CLI 无法完成分析：请确认已登录当前 ChatGPT 账号后在恢复任务中重试。",
            }
            self.status=messages.get(outcome.error_code, "资料已进入待处理，可在恢复任务中重试。")
            self.recovered_tasks[outcome.job_id]=UiEvent("recovered_job",outcome=outcome,completion=completion,
                                                       session_generation=self.session_generation, session_id=self.session_id)
        else: self.status="分析完成，已更新知识库。"
    def apply_event(self,event, *, force=False):
        # Operation ownership outlives a deferred window. Its durable completion
        # refreshes recovery and releases only its own busy operation; visible
        # dialog mutation still requires the current dialog identity.
        learning_events = {"report_list", "report_generated", "exam_list", "exam_blueprint", "exam_generated", "exam_approved", "exam_failed", "worker_error"}
        if event.context == "learning_center" and event.kind in learning_events:
            if event.operation_id != self.active_operation_id:
                return False
            self.finish_work(event.operation_id)
            if (self.learning_center_dialog_id is None
                    or event.dialog_id != self.learning_center_dialog_id):
                return False
            if event.kind in {"report_list", "report_generated"}:
                self.report_runs = event.report_runs
                self.report_artifacts = event.report_artifacts
                self.report_message = event.message
                self.status = event.message or "报告记录已更新。"
                if event.exam_runs:
                    self.exam_runs = event.exam_runs
            elif event.kind in {"exam_list", "exam_blueprint", "exam_generated", "exam_approved", "exam_failed"}:
                self.exam_runs = event.exam_runs
                self.exam_blueprint = event.exam_blueprint
                self.exam_artifacts = event.exam_artifacts
                self.exam_message = event.message
                self.status = event.message or "模拟卷记录已更新。"
            else:
                self.report_message = event.message
                self.status = event.message
            return True
        page_operation = self._page_subject_operations.get(event.operation_id)
        owns_page_operation = page_operation is not None and event.dialog_id == page_operation
        stale_page_dialog = False
        if owns_page_operation and event.kind in {"workflow_outcome", "worker_error"}:
            stale_page_dialog = event.dialog_id != self.page_subject_dialog_id
        if event.kind == "workflow_outcome" and event.outcome:
            # A finish/recovery outcome opens a page dialog only before one is
            # active.  Once a choice operation owns that dialog, its result must
            # echo the exact identity; a missing ID is as stale as a wrong one.
            if event.dialog_id is not None and event.dialog_id != self.page_subject_dialog_id:
                if not owns_page_operation: return False
            if (self.page_subject_dialog_id is not None
                    and event.operation_id == self.active_operation_id
                    and event.dialog_id != self.page_subject_dialog_id):
                if not owns_page_operation: return False
        if event.outcome and event.kind in {"review_saved", "workflow_outcome", "recovered_job"}:
            job_id = event.outcome.job_id
            if event.review_revision < self.review_revisions.get(job_id, 0):
                if event.operation_id == self.active_operation_id:
                    self.finish_work(event.operation_id)
                return False
            self.review_revisions[job_id] = event.review_revision
        if event.kind == "review_saved" and event.outcome:
            self.recovered_tasks[event.outcome.job_id] = event
            if event.outcome.job_id == self.session_id and self.matches_session(event) and event.completion:
                self.completion = event.completion
        if event.kind in {"review_list", "review_saved", "review_conflict"}:
            if event.operation_id != self.active_operation_id:
                return False
            self.finish_work(event.operation_id)
            if event.dialog_id != self.review_dialog_id or self.review_dialog_id is None:
                return False
            self.review_items = event.review_items; self.review_message = event.message
            self.status = event.message or "请对照原图确认题目。"
            return True
        if event.kind == "worker_error" and event.operation_id is None:
            self.worker_failed=True; self.error_message=self.status=event.message; return True
        if event.kind == "preview":
            if not self.matches_session(event): return False
            # Keep only the newest bounded live frame; rendering still prefers the
            # accepted-page freeze until Next/Retake clears it.
            self.live_preview_jpeg=event.preview_jpeg
            return True
        if event.kind in {"recovered_job","recovered_capture"}:
            key=(event.outcome.job_id or f"恢复失败：{event.outcome.recovery_path or '资料库根目录'}") if event.outcome else str(event.page_path)
            self.recovered_tasks[key]=event
            if (event.outcome and event.outcome.state == "needs_subject_confirmation"
                    and event.outcome.pending_page_subjects
                    and key != self._page_subject_job_id and key not in self.recovered_page_subject_queue):
                self.recovered_page_subject_queue.append(key)
            return True
        if event.kind == "workflow_outcome" and event.outcome:
            # Durable results always update their own recovery row, even if the
            # corresponding operation/dialog belongs to a previous activation.
            self.recovered_tasks[event.target_id or event.outcome.job_id]=event
        if stale_page_dialog:
            self.finish_work(event.operation_id)
            self._page_subject_operations.pop(event.operation_id, None)
            return False
        if not force and event.operation_id != self.active_operation_id: return False
        self.finish_work(event.operation_id)
        if owns_page_operation:
            self._page_subject_operations.pop(event.operation_id, None)
        if event.kind in {"worker_error","camera_unavailable","camera_busy"}:
            # A failed resume targets a new generation that was never adopted.
            # Its matching operation still owns the error/status, not the session.
            self.error_message=self.status=event.message
            return True
        if event.kind not in {"capture_resumed","new_capture_ready"} and not self.matches_session(event): return False
        if event.session_id is not None: self.session_id=event.session_id
        if event.kind == "page_captured": self.on_page_captured(event.page_number,event.page_path); self.frozen_preview_jpeg=event.preview_jpeg
        elif event.kind == "page_retaken": self.on_page_retaken(event.page_number,event.page_path); self.frozen_preview_jpeg=event.preview_jpeg
        elif event.kind == "next_page": self.on_next_page(event.page_number)
        elif event.kind == "workflow_outcome" and event.outcome:
            if event.context == "recovered_job":
                self.recovered_tasks[event.target_id or event.outcome.job_id]=event
                # A recovery retry and an active capture reduce the same durable
                # page-confirmation state, while only the latter owns capture UI.
                self.on_workflow_outcome(event.outcome,event.completion,active_capture=False)
            else: self.on_workflow_outcome(event.outcome,event.completion)
        elif event.kind == "capture_resumed":
            self.session_generation=event.session_generation or self.session_generation
            self.session_id=event.session_id
            self.sealed=False; self.page_number=event.page_number or 1; self.page_paths=event.page_paths; self.captured_page_count=len(event.page_paths)
            self.selected_page_number=event.selected_page_number; self.frozen_preview_jpeg=event.preview_jpeg; self._captured_current=self.page_number <= self.captured_page_count
            self.subject_confirmation_needed=False; self.pending_job_id=None; self._clear_page_subject_confirmation()
            self.completion=CompletionSummary(saved_folder=event.page_path)
            self.recovery_error_path=None; self.status="已恢复拍摄会话，可继续拍摄。"
        elif event.kind == "new_capture_ready": self.on_new_capture_ready(event.session_generation)
        elif event.kind == "quality_rejected":
            names={"too_small":"画面裁切或尺寸不足","too_dark":"画面过暗","too_bright":"反光或过亮","too_blurry":"画面模糊"}
            self.error_message=self.status="图片未保存："+"、".join(names.get(x,"质量不合格") for x in event.reasons)+"，请调整后重拍。"
        return True
    def matches_session(self,event):
        return (event.session_generation is None or event.session_generation == self.session_generation) and (
            event.session_id is None or self.session_id is None or event.session_id == self.session_id)
    def on_subject_chosen(self,subject):
        if subject not in {"语文","数学","英语"}: raise ValueError("非法科目")
        self.subject_confirmation_needed=False
    def on_page_subject_chosen(self,subject,operation_id=None):
        if subject not in {"语文","数学","英语"}: raise ValueError("非法科目")
        if self.active_page_subject is None or self.page_subject_dialog_id is None: raise ValueError("当前没有待确认页面")
        self.begin_work(f"正在确认第 {self.active_page_subject.page} 页科目…",operation_id)
        if operation_id is not None:
            self._page_subject_operations[operation_id]=self.page_subject_dialog_id
        return self.active_page_subject.page
    def on_close(self): self.close_requested=True; self.status="正在安全关闭：已保留会话，等待后台释放资源。"


def _window_dimensions(screen_width,screen_height):
    minimum_width=min(940,max(640,screen_width-60))
    minimum_height=min(660,max(480,screen_height-90))
    width=max(minimum_width,min(1380,int(screen_width*.92)))
    height=max(minimum_height,min(900,int(screen_height*.88)))
    return width,height,minimum_width,minimum_height


class LearningAssistantApp:
    def __init__(self,root,config,worker,*,open_path=None,print_path=None,taskbar_notifier=None):
        self.root,self.config,self.worker=root,config,worker; self.vm=CaptureViewModel(); self._open_path=open_path or _open_local_path; self._print_path=print_path or _print_local_path; self._closing=False; self._dialogs={}; self._dialog_context={}; self._poll_after_id=None; self._destroyed=False; self._review_dialog=None; self._learning_center=None; self._page_subject_dialog=None; self._page_subject_context=None
        self._taskbar_notifier=taskbar_notifier or TaskbarNotifier(); self._page_subject_flash_latches=set(); self._active_taskbar_flash_handle=None
        self._preview_source_data=None; self._preview_source_image=None; self._preview_render_size=None; self._preview_resize_after_id=None; self._preview_resample=Image.Resampling.BILINEAR
        self._build(); self._bind_taskbar_focus(self.root); self._refresh(); root.protocol("WM_DELETE_WINDOW",self.close); worker.start(); self._schedule_poll()
    def _build(self):
        colors={"bg":"#fff7fb","card":"#ffffff","ink":"#45384f","muted":"#7c6f84","line":"#f0dce7",
                "pink":"#b83a74","pink_dark":"#8f2c5c","pink_soft":"#fde8f1","blue":"#e4f5ff",
                "blue_dark":"#4d9fc8","lavender":"#eee9ff","disabled":"#dedde2",
                "disabled_text":"#5f5c63","button_border":"#c59bad"}
        self.colors=colors
        self.root.title("晴子学习助手 · 学习花园")
        self.root.configure(bg=colors["bg"])
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        screen_w=self.root.winfo_screenwidth(); screen_h=self.root.winfo_screenheight()
        width,height,minimum_width,minimum_height=_window_dimensions(screen_w,screen_h)
        self.root.geometry(f"{width}x{height}"); self.root.minsize(minimum_width,minimum_height)
        self.root.grid_columnconfigure(0,weight=1); self.root.grid_rowconfigure(1,weight=1)

        header=tk.Frame(self.root,bg=colors["pink_soft"],highlightbackground=colors["line"],highlightthickness=1)
        header.grid(row=0,column=0,sticky="ew"); header.grid_columnconfigure(0,weight=1)
        title_box=tk.Frame(header,bg=colors["pink_soft"]); title_box.grid(row=0,column=0,sticky="w",padx=22,pady=(9,8))
        tk.Label(title_box,text="晴子的学习花园",font=("Microsoft YaHei UI",18,"bold"),fg=colors["ink"],bg=colors["pink_soft"]).pack(anchor="w")
        tk.Label(title_box,text="把每天的认真，变成看得见的进步  ✦",font=("Microsoft YaHei UI",9),fg=colors["muted"],bg=colors["pink_soft"]).pack(anchor="w",pady=(1,0))
        self.page_var=tk.StringVar(master=self.root,value="准备拍摄第 1 页")
        tk.Label(header,textvariable=self.page_var,font=("Microsoft YaHei UI",10,"bold"),fg=colors["pink_dark"],bg=colors["card"],padx=12,pady=6,
                 highlightbackground=colors["line"],highlightthickness=1).grid(row=0,column=1,padx=22,pady=10,sticky="e")

        body=tk.Frame(self.root,bg=colors["bg"]); body.grid(row=1,column=0,sticky="nsew",padx=16,pady=8)
        body.grid_rowconfigure(0,weight=1); body.grid_columnconfigure(0,weight=1,minsize=500); body.grid_columnconfigure(1,weight=0,minsize=285)
        preview_card=tk.Frame(body,bg=colors["card"],highlightbackground=colors["line"],highlightthickness=1)
        self.preview_card=preview_card; preview_card.grid(row=0,column=0,sticky="nsew",padx=(0,12)); preview_card.grid_columnconfigure(0,weight=1); preview_card.grid_rowconfigure(1,weight=1); preview_card.grid_propagate(False)
        preview_title=tk.Frame(preview_card,bg=colors["card"]); preview_title.grid(row=0,column=0,sticky="ew",padx=14,pady=(6,4)); preview_title.grid_columnconfigure(1,weight=1)
        tk.Label(preview_title,text="●",font=("Segoe UI",11),fg=colors["pink"],bg=colors["card"]).grid(row=0,column=0,padx=(0,7))
        tk.Label(preview_title,text="作业预览",font=("Microsoft YaHei UI",12,"bold"),fg=colors["ink"],bg=colors["card"]).grid(row=0,column=1,sticky="w")
        tk.Label(preview_title,text="请让整张纸完整出现在画面中",font=("Microsoft YaHei UI",9),fg=colors["muted"],bg=colors["card"]).grid(row=0,column=2,sticky="e")
        self.preview_shell=tk.Frame(preview_card,bg=colors["blue"],highlightbackground="#cbe8f7",highlightthickness=1)
        self.preview_shell.grid(row=1,column=0,sticky="nsew",padx=10,pady=(0,8))
        self.preview=tk.Label(self.preview_shell,text="正在连接拍照机…\n请稍候",font=("Microsoft YaHei UI",13),fg=colors["blue_dark"],bg=colors["blue"],justify="center")
        self.preview.place(relx=.5,rely=.5,anchor="center")
        self.preview_shell.bind("<Configure>",self._on_preview_resize)

        side=tk.Frame(body,bg=colors["card"],highlightbackground=colors["line"],highlightthickness=1)
        self.side_panel=side; side.grid(row=0,column=1,sticky="nsew"); side.grid_columnconfigure(0,weight=1); side.grid_rowconfigure(3,weight=1)
        tk.Label(side,text="今天的学习记录",font=("Microsoft YaHei UI",13,"bold"),fg=colors["ink"],bg=colors["card"]).grid(row=0,column=0,sticky="w",padx=16,pady=(14,8))
        self.status_var=tk.StringVar(master=self.root,value=self.vm.status)
        tk.Label(side,textvariable=self.status_var,justify="left",anchor="w",wraplength=260,font=("Microsoft YaHei UI",10),fg=colors["pink_dark"],bg=colors["pink_soft"],padx=12,pady=9).grid(row=1,column=0,sticky="ew",padx=14,pady=(0,10))
        tk.Label(side,text="已拍页面与待处理任务",font=("Microsoft YaHei UI",10,"bold"),fg=colors["ink"],bg=colors["card"]).grid(row=2,column=0,sticky="w",padx=16)
        self.pages=tk.Listbox(side,height=6,borderwidth=0,highlightthickness=1,highlightbackground=colors["line"],selectbackground=colors["pink"],selectforeground="white",activestyle="none",font=("Microsoft YaHei UI",10))
        self.pages.bind("<<ListboxSelect>>",self._select); self.pages.grid(row=3,column=0,sticky="nsew",padx=14,pady=(6,10))
        self.detail_var=tk.StringVar(master=self.root,value="本次资料：尚未分析")
        tk.Label(side,textvariable=self.detail_var,justify="left",anchor="w",wraplength=260,font=("Microsoft YaHei UI",9),fg=colors["ink"],bg=colors["lavender"],padx=12,pady=9).grid(row=4,column=0,sticky="ew",padx=14,pady=(0,8))
        self.saved_path_var=tk.StringVar(master=self.root); self.selected_path_var=tk.StringVar(master=self.root)
        tk.Label(side,textvariable=self.saved_path_var,justify="left",anchor="w",wraplength=260,font=("Microsoft YaHei UI",8),fg=colors["muted"],bg=colors["card"]).grid(row=5,column=0,sticky="ew",padx=16)
        tk.Label(side,textvariable=self.selected_path_var,justify="left",anchor="w",wraplength=260,font=("Microsoft YaHei UI",8),fg=colors["muted"],bg=colors["card"]).grid(row=6,column=0,sticky="ew",padx=16,pady=(2,12))

        self.footer=tk.Frame(self.root,bg=colors["card"],highlightbackground=colors["line"],highlightthickness=1)
        self.footer.grid(row=2,column=0,sticky="ew"); self.buttons={}
        primary=tk.Frame(self.footer,bg=colors["card"]); primary.pack(fill="x",padx=16,pady=(8,4))
        secondary=tk.Frame(self.footer,bg=colors["card"]); secondary.pack(fill="x",padx=16,pady=(0,7))
        primary_items=(("拍下这一页",self.capture,"capture"),("重拍选中页",self.retake,"retake"),("下一页",self.next_page,"next"),("完成并分析",self.finish,"finish"))
        secondary_items=(("重试任务",self.retry_selected,"retry"),("待家长确认",self.open_reviews,"review"),("资料文件夹",self.open_folder,"folder"),("知识库总览",self.open_dashboard,"dashboard"),("学习与复习",self.open_learning_center,"learning"),("本次分析",self.open_details,"details"))
        for column,(text,fn,name) in enumerate(primary_items):
            primary.grid_columnconfigure(column,weight=1,uniform="primary")
            button=tk.Button(primary,text=text,command=fn,font=("Microsoft YaHei UI",9,"bold"),padx=10,pady=6)
            button.grid(row=0,column=column,sticky="ew",padx=4); self.buttons[name]=button
        for column,(text,fn,name) in enumerate(secondary_items):
            secondary.grid_columnconfigure(column,weight=1,uniform="secondary")
            button=tk.Button(secondary,text=text,command=fn,font=("Microsoft YaHei UI",9,"bold"),padx=10,pady=6)
            button.grid(row=0,column=column,sticky="ew",padx=3); self.buttons[name]=button

    def _style_button(self,button,enabled):
        if enabled:
            button.configure(state="normal",fg="white",bg=self.colors["pink"],
                             activeforeground="white",activebackground=self.colors["pink_dark"],
                             disabledforeground=self.colors["disabled_text"],cursor="hand2",
                             relief="solid",borderwidth=1,highlightbackground=self.colors["button_border"])
        else:
            button.configure(state="disabled",fg=self.colors["disabled_text"],bg=self.colors["disabled"],
                             activeforeground=self.colors["disabled_text"],activebackground=self.colors["disabled"],
                             disabledforeground=self.colors["disabled_text"],cursor="arrow",
                             relief="solid",borderwidth=1,highlightbackground=self.colors["disabled"])

    def _on_preview_resize(self,_event=None):
        if self._preview_resize_after_id is not None:
            try:self.root.after_cancel(self._preview_resize_after_id)
            except Exception:pass
        self._preview_resize_after_id=self.root.after_idle(self._render_preview)

    def _render_preview(self):
        self._preview_resize_after_id=None
        source=self._preview_source_image
        if source is None:return
        width=self.preview_shell.winfo_width()-12; height=self.preview_shell.winfo_height()-12
        if width < 2 or height < 2: width,height=source.size
        size=_fit_preview_size(source.size,(width,height))
        if size == self._preview_render_size:return
        rendered=source if size==source.size else source.resize(size,self._preview_resample)
        self._photo=ImageTk.PhotoImage(rendered,master=self.root)
        self.preview.configure(image=self._photo,text="",width=size[0],height=size[1],bg=self.colors["blue"])
        self._preview_render_size=size
    def _page_subject_blocks_submission(self,kind,kw):
        if not (self.vm.page_subject_dialog_needed or self._page_subject_dialog is not None):
            return False
        if kind != "confirm_page_subject":
            return True
        item=self.vm.active_page_subject; context=self._page_subject_context
        return (item is None or context is None or kw.get("dialog_id") != self.vm.page_subject_dialog_id
                or kw.get("job_id") != context[0] or kw.get("page_number") != item.page)

    def _submit(self,kind,message,allowed=True,**kw):
        if self._page_subject_blocks_submission(kind,kw):
            return False
        if self._closing or self.vm.busy or not allowed:return False
        kw.setdefault("session_generation", uuid4().hex if kind in {"resume_capture","new_capture"} else self.vm.session_generation)
        kw.setdefault("session_id", Path(kw["job_id"]).name if kind == "resume_capture" else None if kind == "new_capture" else self.vm.session_id)
        op=self.worker.submit(kind,**kw); self.vm.begin_work(message,op); self._refresh()
        return True
    def capture(self):
        if self.vm.can_start_new:self._submit("new_capture","正在准备下一份…",True)
        else:self._submit("capture","正在拍摄…",self.vm.can_capture)
    def retake(self): self._submit("retake_page","正在重拍…",self.vm.can_retake,page_number=self.vm.selected_page_number or self.vm.page_number)
    def next_page(self): self._submit("next","正在保存页序…",self.vm.can_next)
    def finish(self): self._submit("finish","正在分析资料…",self.vm.can_finish)
    def open_reviews(self):
        if (self.vm.page_subject_dialog_needed or self._page_subject_dialog is not None
                or self._closing or self.vm.busy or self.vm.worker_failed):
            return
        if self._review_dialog is not None:
            self._review_dialog.window.lift(); return
        identity = uuid4().hex
        self.vm.review_dialog_id = identity
        self._review_dialog = ReviewDialog(self.root, identity, self._save_review, self.close_reviews, self._open_path)
        self._submit("list_reviews", "正在读取待确认题目…", context="review_job", dialog_id=identity)

    def open_learning_center(self):
        if (self.vm.page_subject_dialog_needed or self._page_subject_dialog is not None
                or self._closing or self.vm.busy or self.vm.worker_failed):
            return
        if self._learning_center is not None:
            self._learning_center.window.lift(); return
        identity = uuid4().hex
        self.vm.learning_center_dialog_id = identity
        self._learning_center = LearningCenterDialog(
            self.root,
            identity,
            knowledge_root=self.config.knowledge_root,
            on_generate_report=self._generate_learning_report,
            on_open=self._open_path,
            on_print=self._print_path,
            on_close=self.close_learning_center,
            on_preview_exam=self._preview_exam_blueprint,
            on_generate_exam=self._generate_exam,
            on_approve_exam=self._approve_exam,
        )
        self._submit("list_reports", "正在读取学情报告和模拟卷…", context="learning_center", dialog_id=identity)

    def _preview_exam_blueprint(self, dialog_id, request):
        if self._learning_center is None or dialog_id != self.vm.learning_center_dialog_id or self._closing:
            return False
        return self._submit("preview_exam_blueprint", "正在计算组卷依据…", context="learning_center", dialog_id=dialog_id, exam_request=request)

    def _generate_exam(self, dialog_id, request):
        if self._learning_center is None or dialog_id != self.vm.learning_center_dialog_id or self._closing:
            return False
        return self._submit("generate_exam", "正在生成并校验模拟卷…", context="learning_center", dialog_id=dialog_id, exam_request=request)

    def _approve_exam(self, dialog_id, exam_id, revision):
        if self._learning_center is None or dialog_id != self.vm.learning_center_dialog_id or self._closing:
            return False
        return self._submit("approve_exam", "正在发布可打印材料…", context="learning_center", dialog_id=dialog_id, exam_id=exam_id, expected_revision=revision)

    def _generate_learning_report(self, dialog_id):
        if (self._learning_center is None or dialog_id != self.vm.learning_center_dialog_id
                or self._closing):
            return False
        return self._submit(
            "generate_report", "正在生成增量学情报告…", context="learning_center",
            dialog_id=dialog_id,
        )

    def close_learning_center(self, dialog_id):
        if dialog_id != self.vm.learning_center_dialog_id:
            return
        self.vm.learning_center_dialog_id = None
        self.vm.report_runs = (); self.vm.report_artifacts = None; self.vm.report_message = ""
        self.vm.exam_runs = (); self.vm.exam_blueprint = None; self.vm.exam_artifacts = None; self.vm.exam_message = ""
        dialog, self._learning_center = self._learning_center, None
        if dialog is not None:
            dialog.destroy()

    def _save_review(self, dialog_id, item, final_status, corrected_answer, note):
        if dialog_id != self.vm.review_dialog_id or self._review_dialog is None:
            return False
        return self._submit("confirm_review", "正在保存复核结果…", context="review_job", dialog_id=dialog_id,
                            job_id=item.document_id, question_id=item.question_id, final_status=final_status,
                            corrected_answer=corrected_answer, note=note, expected_version=item.version)

    def close_reviews(self, dialog_id):
        if self.vm.review_dialog_id != dialog_id:
            return
        self.vm.review_dialog_id = None; self.vm.review_items = ()
        dialog, self._review_dialog = self._review_dialog, None
        if dialog is not None: dialog.destroy()
    def retry_selected(self):
        if self.vm.page_subject_dialog_needed or self._page_subject_dialog is not None:
            return
        item=self.pages.curselection()
        if item:
            key=self.pages.get(item[0]); event=self.vm.recovered_tasks.get(key)
            if event and event.kind == "recovered_capture" and event.page_path:
                self._submit("resume_capture","正在恢复拍摄会话…",True,job_id=str(event.page_path))
            elif event and event.outcome:
                outcome=event.outcome
                if outcome.error_code == "recovery_failed":
                    if outcome.recovery_path:self._open_path(outcome.recovery_path)
                elif outcome.state == "needs_subject_confirmation":
                    if outcome.pending_page_subjects:
                        self.vm.on_workflow_outcome(outcome, active_capture=False)
                        self._show_page_subject_dialog(outcome.job_id, "recovered_job",
                                                       event.session_generation, event.session_id)
                    else:
                        context="active_capture" if self.vm.matches_session(event) and self.vm.pending_job_id == outcome.job_id else "recovered_job"
                        self._subject_dialog(outcome.job_id,context,event.session_generation,event.session_id)
                elif outcome.job_id: self._submit("retry","正在重试…",True,job_id=outcome.job_id,context="recovered_job",
                                                  session_generation=event.session_generation,session_id=event.session_id)
    def _select(self,_):
        item=self.pages.curselection()
        if item:
            value=self.pages.get(item[0])
            if value.startswith("第 "):
                number=int(value.split()[1]); self.vm.selected_page_number=number
                if number <= len(self.vm.page_paths): self.vm.frozen_preview_jpeg=_page_preview(self.vm.page_paths[number-1])
        self._refresh()
    def _schedule_poll(self):
        if not self._destroyed and self._poll_after_id is None:self._poll_after_id=self.root.after(50,self.poll_events)
    def poll_events(self):
        self._poll_after_id=None
        for source in (self.worker.preview_events,self.worker.events):
            while True:
                try:event=source.get_nowait()
                except queue.Empty:break
                if event.kind=="shutdown_ack": self._destroy(); return
                accepted=self.vm.apply_event(event)
                if accepted and event.kind in {"review_list", "review_saved", "review_conflict"} and self._review_dialog is not None:
                    self._review_dialog.show(self.vm.review_items, self.vm.review_message)
                elif accepted and event.kind == "worker_error" and event.dialog_id == self.vm.review_dialog_id and self._review_dialog is not None:
                    self._review_dialog.show(self.vm.review_items, event.message)
                if (accepted and event.context == "learning_center"
                        and event.kind in {"report_list", "report_generated", "exam_list", "exam_blueprint", "exam_generated", "exam_approved", "exam_failed", "worker_error"}
                        and self._learning_center is not None):
                    self._learning_center.show(
                        self.vm.report_runs, self.vm.report_message, self.vm.report_artifacts
                    )
                    self._learning_center.show_exams(
                        self.vm.exam_runs, self.vm.exam_message,
                        self.vm.exam_blueprint, self.vm.exam_artifacts,
                    )
                if accepted:
                    if event.kind=="workflow_outcome" and event.operation_id and event.outcome and self.vm.page_subject_dialog_needed:
                        self._flash_page_subject_confirmation_if_needed()
                        self._show_page_subject_dialog(event.outcome.job_id, event.context,
                                                       event.session_generation, event.session_id)
                    elif not self.vm.page_subject_dialog_needed:
                        self._stop_taskbar_flash()
                        self._close_page_subject_dialog()
                if accepted and event.kind=="workflow_outcome" and event.operation_id and event.outcome:
                    if (event.context=="active_capture" and self.vm.matches_session(event)
                            and self.vm.subject_confirmation_needed and not self.vm.pending_page_subjects):
                        self._subject_dialog(event.outcome.job_id,"active_capture",event.session_generation,event.session_id)
        self._show_next_recovered_page_subject()
        self._refresh()
        self._schedule_poll()

    def _show_next_recovered_page_subject(self):
        if not self.vm.recovered_page_subject_queue:
            return
        if (self._closing or self.vm.busy or self.vm.page_subject_dialog_needed
                or self._page_subject_dialog is not None or self._review_dialog is not None
                or self._learning_center is not None or self._dialogs):
            return
        while self.vm.recovered_page_subject_queue:
            key = self.vm.recovered_page_subject_queue.pop(0)
            event = self.vm.recovered_tasks.get(key)
            if (event is None or event.outcome is None
                    or event.outcome.state != "needs_subject_confirmation" or not event.outcome.pending_page_subjects):
                continue
            self.vm.on_workflow_outcome(event.outcome, active_capture=False)
            self._flash_page_subject_confirmation_if_needed()
            self._show_page_subject_dialog(key, "recovered_job", event.session_generation, event.session_id)
            break

    def _show_page_subject_dialog(self, job_id, context, session_generation=None, session_id=None):
        item=self.vm.active_page_subject; dialog_id=self.vm.page_subject_dialog_id
        if item is None or dialog_id is None:
            self._close_page_subject_dialog(); return
        wanted=(job_id,context,session_generation,session_id,dialog_id)
        if self._page_subject_dialog is not None and self._page_subject_context != wanted:
            self._close_page_subject_dialog(stop_flash=False)
        if self._page_subject_dialog is None:
            self._page_subject_dialog=PageSubjectDialog(
                self.root, on_choose=self._confirm_page_subject,
                on_defer=lambda identity=dialog_id:self._defer_page_subject_dialog(identity),
            )
            self._page_subject_context=wanted
            self._bind_taskbar_focus(getattr(self._page_subject_dialog,"window",None))
        self._page_subject_dialog.show(item,self.vm.page_subject_index,self.vm.page_subject_total)
        self._page_subject_dialog.set_busy(self.vm.busy)

    def _confirm_page_subject(self, page, subject):
        dialog=self._page_subject_dialog; context=self._page_subject_context
        item=self.vm.active_page_subject; dialog_id=self.vm.page_subject_dialog_id
        if (self._closing or dialog is None or context is None or item is None or page != item.page
                or dialog_id is None or subject not in {"语文","数学","英语"}):
            if dialog is not None: dialog.set_busy(self.vm.busy)
            return False
        job_id,job_context,generation,session_id,_identity=context
        dialog.set_busy(True)
        operation_id=self.worker.submit("confirm_page_subject",job_id=job_id,page_number=page,subject=subject,
                                        context=job_context,session_generation=generation,session_id=session_id,
                                        dialog_id=dialog_id)
        self.vm.on_page_subject_chosen(subject,operation_id)
        self._refresh()
        return True

    def _defer_page_subject_dialog(self, dialog_id):
        if not self.vm.defer_page_subject_dialog(dialog_id):
            return False
        self._stop_taskbar_flash()
        self._page_subject_dialog=None; self._page_subject_context=None
        self._refresh()
        return True

    def _close_page_subject_dialog(self, *, stop_flash=True):
        if stop_flash:
            self._stop_taskbar_flash()
        dialog,self._page_subject_dialog=self._page_subject_dialog,None
        self._page_subject_context=None
        if dialog is not None:
            dialog.destroy()

    def _bind_taskbar_focus(self, window):
        bind=getattr(window,"bind",None)
        if callable(bind):
            bind("<FocusIn>",self._on_taskbar_focus,add="+")

    def _on_taskbar_focus(self, _event=None):
        self._stop_taskbar_flash()

    def _root_window_handle(self):
        try:
            # On Windows winfo_id() names Tk's inner child. The window-manager
            # frame owns the taskbar button and is returned by GetForegroundWindow.
            frame=getattr(self.root,"wm_frame",None)
            if callable(frame):
                return int(frame(),0)
            return int(self.root.winfo_id())
        except (AttributeError, TypeError, ValueError):
            return None

    def _flash_page_subject_confirmation_if_needed(self):
        item=self.vm.active_page_subject
        job_id=self.vm._page_subject_job_id
        if item is None or job_id is None:
            self._stop_taskbar_flash()
            return
        latch=(job_id,tuple(page.page for page in self.vm.pending_page_subjects))
        if latch in self._page_subject_flash_latches:
            return
        self._page_subject_flash_latches.add(latch)
        self._stop_taskbar_flash()
        handle=self._root_window_handle()
        if handle is not None and self._taskbar_notifier.flash_if_background(handle):
            self._active_taskbar_flash_handle=handle

    def _stop_taskbar_flash(self):
        handle=getattr(self,"_active_taskbar_flash_handle",None)
        if handle is not None:
            self._taskbar_notifier.stop(handle)
            self._active_taskbar_flash_handle=None

    def _subject_dialog(self,job_id,context="active_capture",session_generation=None,session_id=None):
        if job_id in self._dialogs:return
        d=tk.Toplevel(self.root); self._dialogs[job_id]=d; self._dialog_context[job_id]=(context,session_generation,session_id)
        d.protocol("WM_DELETE_WINDOW",lambda j=job_id,w=d:(w.destroy(),self._dialogs.pop(j,None),self._dialog_context.pop(j,None)))
        for subject in ("语文","数学","英语"):
            tk.Button(d,text=subject,command=lambda s=subject,j=job_id,c=context,g=session_generation,i=session_id:self._confirm(d,j,s,c,g,i)).pack(fill="x")
    def _confirm(self,d,job_id,subject,context,session_generation=None,session_id=None):
        identity=UiEvent("dialog",session_generation=session_generation,session_id=session_id)
        if context=="active_capture" and not self.vm.matches_session(identity): context="recovered_job"
        if self._submit("confirm_subject","正在继续…",not self._closing,job_id=job_id,subject=subject,context=context,
                        session_generation=session_generation,session_id=session_id):
            if context=="active_capture": self.vm.on_subject_chosen(subject)
            d.destroy(); self._dialogs.pop(job_id,None); self._dialog_context.pop(job_id,None)
    def _refresh(self):
        if self._page_subject_dialog is not None:
            self._page_subject_dialog.set_busy(self.vm.busy)
        if self._learning_center is not None:
            self._learning_center.set_busy(self.vm.busy)
        self.status_var.set(self.vm.status)
        self.page_var.set(f"准备拍摄第 {self.vm.page_number} 页  ·  已拍 {self.vm.captured_page_count} 页")
        wanted=[*(f"第 {i} 页" for i,_ in enumerate(self.vm.page_paths,1)),*self.vm.recovered_tasks.keys()]
        current=list(self.pages.get(0,"end")); selected=self.pages.get(self.pages.curselection()[0]) if self.pages.curselection() else None
        if current != wanted:
            self.pages.delete(0,"end")
            for row in wanted:self.pages.insert("end",row)
            if selected in wanted:
                index=wanted.index(selected); self.pages.selection_set(index); self.pages.see(index)
        summary=self.vm.completion; weak="、".join(summary.weak_knowledge_points) or "尚无"
        self.detail_var.set(f"本次资料：{summary.question_count} 题，错题 {summary.error_count}，待确认 {summary.review_count}，薄弱点 {weak}\n知识库累计待处理：{summary.library_pending_count}")
        target=self._selected_completion()
        self.saved_path_var.set(f"本次保存位置：{summary.saved_folder or '尚未保存'}")
        self.selected_path_var.set(f"选中任务位置：{target.saved_folder or '暂无可打开的位置'}" if self._selected_recovery() else "")
        data=self.vm.frozen_preview_jpeg or self.vm.live_preview_jpeg
        resample=Image.Resampling.LANCZOS if self.vm.frozen_preview_jpeg else Image.Resampling.BILINEAR
        resample_changed=resample != self._preview_resample
        if resample_changed:self._preview_resample=resample; self._preview_render_size=None
        if data and data != self._preview_source_data:
            try:
                with Image.open(BytesIO(data)) as image:self._preview_source_image=image.convert("RGB")
                self._preview_source_data=data; self._preview_render_size=None; self._render_preview()
            except Exception:pass
        elif data and resample_changed:self._render_preview()
        page_modal=self.vm.page_subject_dialog_needed or self._page_subject_dialog is not None
        states={"capture":self.vm.can_capture and not page_modal,"retake":self.vm.can_retake and not page_modal,"next":self.vm.can_next and not page_modal,"finish":self.vm.can_finish and not page_modal,
                "review":not page_modal and not self.vm.busy and not self.vm.worker_failed,
                "retry":not page_modal and not self.vm.busy and not self.vm.worker_failed and self._selected_recovery() is not None,
                "folder":bool(target.saved_folder and target.saved_folder.exists()),
                "details":bool(target.analysis_details_path and target.analysis_details_path.exists()),
                "dashboard":(self.config.knowledge_root/"知识库首页.html").exists(),
                "learning":not page_modal and not self.vm.busy and not self.vm.worker_failed}
        self.buttons["capture"].configure(text="开始下一份" if self.vm.sealed else "拍下这一页")
        states["capture"]=(self.vm.can_capture or self.vm.can_start_new) and not page_modal
        for name,allowed in states.items(): self._style_button(self.buttons[name],allowed and not self._closing)
    def _selected_recovery(self):
        selected=self.pages.curselection()
        return self.vm.recovered_tasks.get(self.pages.get(selected[0])) if selected else None
    def _selected_completion(self):
        event=self._selected_recovery()
        if event is None:return self.vm.completion
        if event.completion is not None:return event.completion
        outcome=event.outcome
        folder=(outcome.archived_pages[0].parent if outcome.archived_pages else outcome.recovery_path) if outcome else event.page_path
        return CompletionSummary(saved_folder=folder,analysis_details_path=outcome.analysis_markdown if outcome else None)
    def open_folder(self):
        path=self._selected_completion().saved_folder
        if path and path.exists():self._open_path(path)
    def open_dashboard(self):
        path=self.config.knowledge_root/"知识库首页.html"
        if path.exists():self._open_path(path)
    def open_details(self):
        path=self._selected_completion().analysis_details_path
        if path and path.exists():self._open_path(path)
    def close(self):
        if self._closing:return
        self._closing=True; self._stop_taskbar_flash(); self.vm.on_close(); self.worker.request_shutdown(); self._refresh()
        if not self.worker.is_alive(): self._destroy()
        else: self._schedule_poll()
    def _destroy(self):
        self._destroyed=True
        self._stop_taskbar_flash()
        self._close_page_subject_dialog()
        if self._review_dialog is not None:
            self.close_reviews(self.vm.review_dialog_id)
        if self._learning_center is not None:
            self.close_learning_center(self.vm.learning_center_dialog_id)
        if self._poll_after_id is not None:
            try:self.root.after_cancel(self._poll_after_id)
            except Exception:pass
            self._poll_after_id=None
        if self._preview_resize_after_id is not None:
            try:self.root.after_cancel(self._preview_resize_after_id)
            except Exception:pass
            self._preview_resize_after_id=None
        self.root.destroy()


def _fit_preview_size(source_size, bounds):
    source_width,source_height=source_size; bound_width,bound_height=bounds
    if min(source_width,source_height,bound_width,bound_height) <= 0:return (1,1)
    scale=min(bound_width/source_width,bound_height/source_height,1.0)
    return max(1,round(source_width*scale)),max(1,round(source_height*scale))


def _frame_preview(frame):
    try:
        image=Image.fromarray(frame[...,::-1] if getattr(frame,"ndim",0)==3 else frame).convert("RGB"); image.thumbnail((960,720),Image.Resampling.BILINEAR); out=BytesIO(); image.save(out,"JPEG",quality=74); return out.getvalue()
    except Exception:return None
def _page_preview(path):
    try:
        image=Image.open(path); image.thumbnail((1280,960)); out=BytesIO(); image.convert("RGB").save(out,"JPEG",quality=84); return out.getvalue()
    except Exception:return None
def _open_local_path(path):
    path=Path(path).resolve()
    if os.name=="nt":os.startfile(str(path))
    else:webbrowser.open(path.as_uri())


def _print_local_path(path):
    """Open one explicit artifact and request the browser's print dialog."""
    path = Path(path).resolve()
    if path.is_file():
        webbrowser.open(path.as_uri() + "?print=1")
