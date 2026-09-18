"""Orchestrate immutable profile, narrative, rendering, and publication."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
from pathlib import Path
from typing import Callable
from uuid import uuid4

from qingzi_learning.export.publication import OutputBatch, publication_lock, write_output
from qingzi_learning.export.safe_write import replace_guarded
from qingzi_learning.reporting.narrative import (
    CodexNarrativeProvider,
    LocalNarrativeProvider,
    NarrativeService,
)
from qingzi_learning.reporting.profile import LearningProfileBuilder
from qingzi_learning.reporting.render import ReportRenderer
from qingzi_learning.storage.paths import KnowledgePaths
from qingzi_learning.storage.repository import KnowledgeRepository, ReportRun


@dataclass(frozen=True)
class ReportArtifacts:
    report_id: str
    child_path: Path
    parent_path: Path
    latest_path: Path
    manifest_path: Path


class LearningReportService:
    def __init__(
        self,
        repo: KnowledgeRepository,
        *,
        narrative_service=None,
        id_factory: Callable[[datetime], str] | None = None,
    ) -> None:
        self.repo = repo
        self.paths = KnowledgePaths(repo.config)
        self.profile_builder = LearningProfileBuilder(repo)
        self.narrative_service = narrative_service or NarrativeService(
            CodexNarrativeProvider(), LocalNarrativeProvider()
        )
        self.id_factory = id_factory or self._default_id

    def generate(self, *, now: datetime | None = None) -> ReportArtifacts:
        generated_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        previous = self.repo.latest_completed_report()
        profile = self.profile_builder.build(
            previous.snapshot if previous else None,
            cutoff_at=generated_at,
        )
        report_id = self.id_factory(generated_at)
        run = self.repo.create_report_run(
            report_id,
            previous.report_id if previous else None,
            profile["cutoff_at"],
            profile,
        )
        try:
            narrative = self.narrative_service.generate(profile)
            directory = self.paths.report_directory(
                generated_at.year, generated_at.month, report_id
            )
            renderer = ReportRenderer(self.paths, directory)
            child = renderer.render_child(profile, narrative)
            parent = renderer.render_parent(profile, narrative)
            artifacts = ReportArtifacts(
                report_id=report_id,
                child_path=directory / "孩子版.html",
                parent_path=directory / "家长版.html",
                latest_path=self.paths.latest_report_path(),
                manifest_path=directory / "report.json",
            )
            output_files = {
                "child": self._relative(artifacts.child_path),
                "parent": self._relative(artifacts.parent_path),
                "latest": self._relative(artifacts.latest_path),
                "manifest": self._relative(artifacts.manifest_path),
            }
            manifest = json.dumps(
                {
                    "report_id": report_id,
                    "previous_report_id": run.previous_report_id,
                    "profile": profile,
                    "narrative": narrative,
                    "output_files": output_files,
                },
                ensure_ascii=False,
                indent=2,
            )
            self._publish(artifacts, child, parent, manifest)
            self.repo.complete_report_run(report_id, narrative, output_files)
            return artifacts
        except Exception:
            current = self.repo.get_report_run(report_id)
            if current is not None and current.status == "generating":
                self.repo.fail_report_run(report_id, "report_generation_failed")
            raise

    def history(self) -> tuple[ReportRun, ...]:
        return self.repo.list_report_runs()

    def _publish(
        self,
        artifacts: ReportArtifacts,
        child: str,
        parent: str,
        manifest: str,
    ) -> None:
        root = self.paths.knowledge_root
        batch = OutputBatch(root)
        try:
            with batch:
                write_output(artifacts.child_path, child, root)
                write_output(artifacts.parent_path, parent, root)
                write_output(artifacts.manifest_path, manifest, root)
                # The latest alias is replaced last, after the immutable version is complete.
                write_output(artifacts.latest_path, child, root)
            with publication_lock(root):
                for relative, (temporary, digest) in batch.files.items():
                    destination = root / relative
                    payload = temporary.read_bytes()
                    if not payload or sha256(payload).hexdigest() != digest:
                        raise ValueError("报告暂存文件已变化")
                    replace_guarded(temporary, destination, root)
        finally:
            batch.cleanup()

    def _relative(self, path: Path) -> str:
        return path.resolve().relative_to(self.paths.knowledge_root).as_posix()

    @staticmethod
    def _default_id(now: datetime) -> str:
        return f"report-{now:%Y%m%d-%H%M%S}-{uuid4().hex[:6]}"
