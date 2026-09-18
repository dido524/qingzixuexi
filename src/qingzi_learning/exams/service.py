"""Application service for safe targeted-exam creation and approval."""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Any
from uuid import uuid4

from qingzi_learning.exams.blueprint import BlueprintBuilder, ExamRequest
from qingzi_learning.exams.generator import ExamGenerator, ExamVerifier
from qingzi_learning.exams.render import ExamRenderer
from qingzi_learning.exams.validation import ExamValidationError, validate_exam
from qingzi_learning.export.publication import OutputBatch, publication_lock, write_output
from qingzi_learning.export.safe_write import replace_guarded
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import ExamRun, KnowledgeRepository


class ExamGenerationError(RuntimeError):
    pass


@dataclass(frozen=True)
class ExamArtifacts:
    student_path: Path
    answer_sheet_path: Path
    solutions_path: Path
    blueprint_path: Path
    manifest_path: Path


class TargetedExamService:
    def __init__(
        self,
        repo: KnowledgeRepository,
        blueprint_builder: BlueprintBuilder | None = None,
        generator: ExamGenerator | None = None,
        verifier: ExamVerifier | None = None,
    ) -> None:
        self.repo = repo
        self.paths = KnowledgePaths(repo.config)
        self.blueprint_builder = blueprint_builder or BlueprintBuilder(repo)
        self.generator = generator or ExamGenerator()
        self.verifier = verifier or ExamVerifier()

    def preview_blueprint(self, request: ExamRequest) -> dict[str, Any]:
        return self.blueprint_builder.build(request)

    def create_draft(
        self, request: ExamRequest, *, now: datetime | None = None
    ) -> ExamRun:
        timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        subject_code = {"语文": "CN", "数学": "MATH", "英语": "EN"}.get(request.subject, "SUBJ")
        exam_id = f"QZ-{subject_code}-{timestamp:%Y%m%d-%H%M%S}-{uuid4().hex[:6].upper()}"
        blueprint = self.blueprint_builder.build(request)
        run = self.repo.create_exam_run(exam_id, request.subject, request.__dict__, blueprint)
        last_error: Exception | None = None
        repair_issues: list[str] = []
        for attempt in range(2):
            try:
                repair = getattr(self.generator, "repair", None)
                if attempt and callable(repair):
                    generation = repair(exam_id, request, blueprint, repair_issues)
                else:
                    generation = self.generator.generate(exam_id, request, blueprint)
                questions = validate_exam(request, blueprint, generation)
                verification = self.verifier.verify(request, blueprint, generation)
                if not verification.get("approved"):
                    repair_issues = [str(value) for value in verification.get("issues", [])]
                    raise ExamValidationError("独立校验未通过")
                verdict_ids = {item.get("question_id") for item in verification.get("question_verdicts", [])}
                if any(not item.get("valid") for item in verification.get("question_verdicts", [])):
                    repair_issues = [
                        str(issue)
                        for item in verification.get("question_verdicts", [])
                        for issue in item.get("issues", [])
                    ]
                    raise ExamValidationError("独立校验发现问题")
                if verdict_ids != {item.question_id for item in questions}:
                    raise ExamValidationError("独立校验题目范围不完整")
                stored = [replace(item, exam_id=exam_id) for item in questions]
                return self.repo.save_exam_generation(exam_id, generation, verification, stored)
            except Exception as exc:
                last_error = exc
                if not repair_issues:
                    repair_issues = [str(exc)]
        self.repo.fail_exam(exam_id, "exam_validation_failed")
        raise ExamGenerationError("exam_validation_failed") from last_error

    def preview(self, exam_id: str) -> str:
        run = self.repo.get_exam_run(exam_id)
        if run is None or run.status != "needs_parent_approval":
            raise ValueError("模拟卷尚未通过校验，不能预览")
        created = datetime.fromisoformat(run.created_at.replace(" ", "T"))
        directory = self.paths.exam_directory(created.year, created.month, run.exam_id)
        return ExamRenderer(self.paths, directory).render_preview(
            run, self.repo.exam_questions(exam_id)
        )

    def approve(self, exam_id: str, *, expected_revision: int) -> ExamArtifacts:
        run = self.repo.get_exam_run(exam_id)
        if run is None:
            raise ValueError("模拟卷不存在")
        if run.status != "needs_parent_approval":
            raise ValueError("模拟卷尚未通过校验，不能由家长确认")
        if run.revision != expected_revision:
            raise ValueError("模拟卷已变化，请重新预览")
        questions = self.repo.exam_questions(exam_id)
        if not questions:
            raise ValueError("模拟卷尚未通过校验，不能由家长确认")

        created = datetime.fromisoformat(run.created_at.replace(" ", "T"))
        directory = self.paths.exam_directory(created.year, created.month, run.exam_id)
        renderer = ExamRenderer(self.paths, directory)
        rendered = renderer.render_approved(run, questions)
        artifacts = ExamArtifacts(
            student_path=directory / "学生试卷.html",
            answer_sheet_path=directory / "答题纸.html",
            solutions_path=directory / "答案与解析.html",
            blueprint_path=directory / "组卷说明.html",
            manifest_path=directory / "exam.json",
        )
        paths = {
            "student": artifacts.student_path,
            "answer_sheet": artifacts.answer_sheet_path,
            "solutions": artifacts.solutions_path,
            "blueprint": artifacts.blueprint_path,
        }
        digests = {
            name: sha256(rendered[name].encode("utf-8")).hexdigest()
            for name in rendered
        }
        output_files = {
            name: path.resolve().relative_to(self.paths.knowledge_root).as_posix()
            for name, path in paths.items()
        }
        output_files["manifest"] = artifacts.manifest_path.resolve().relative_to(
            self.paths.knowledge_root
        ).as_posix()
        manifest = json.dumps({
            "exam_id": exam_id,
            "subject": run.subject,
            "request": run.request,
            "blueprint": run.blueprint,
            "sha256": digests,
            "output_files": output_files,
        }, ensure_ascii=False, indent=2)
        self._publish(paths, artifacts.manifest_path, rendered, manifest)
        try:
            self.repo.approve_exam(
                exam_id, output_files, expected_revision=expected_revision
            )
        except Exception:
            # Files are immutable but unreferenced; the dashboard only exposes approved rows.
            raise
        return artifacts

    def history(self) -> tuple[ExamRun, ...]:
        return self.repo.list_exam_runs()

    def _publish(
        self,
        paths: dict[str, Path],
        manifest_path: Path,
        rendered: dict[str, str],
        manifest: str,
    ) -> None:
        root = self.paths.knowledge_root
        batch = OutputBatch(root)
        try:
            with batch:
                for name, path in paths.items():
                    write_output(path, rendered[name], root)
                write_output(manifest_path, manifest, root)
            with publication_lock(root):
                for relative, (temporary, digest) in batch.files.items():
                    payload = temporary.read_bytes()
                    if not payload or sha256(payload).hexdigest() != digest:
                        raise ValueError("模拟卷暂存文件已变化")
                    replace_guarded(temporary, root / relative, root)
        finally:
            batch.cleanup()
