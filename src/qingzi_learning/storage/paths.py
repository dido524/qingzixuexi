"""Guarded paths below the configured, per-subject knowledge folders."""

from dataclasses import dataclass
from pathlib import Path
import re

from qingzi_learning.config import AppConfig


_FILE_NAME = re.compile(r"^[\u4e00-\u9fffA-Za-z0-9_-]+(?:\.[A-Za-z0-9]+)?$")


@dataclass(frozen=True)
class SubjectTree:
    """The fixed folders that make up one subject's knowledge tree."""

    subject_root: Path
    raw: Path
    analysis: Path
    mistakes: Path
    knowledge_points: Path
    pending: Path


class KnowledgePaths:
    """Creates only known subject folders and never resolves a path outside them."""

    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self._knowledge_root = config.knowledge_root.resolve()

    def ensure_subject_tree(self, subject: str) -> SubjectTree:
        """Return the subject folders, creating missing fixed directories safely."""
        self._validate_subject(subject)
        subject_root = self._contained(self._knowledge_root / subject)
        subject_root.mkdir(parents=True, exist_ok=True)
        subject_root = self._contained(subject_root)

        folders = {
            "raw": "原始资料",
            "analysis": "分析记录",
            "mistakes": "错题",
            "knowledge_points": "知识点",
            "pending": "待处理",
        }
        resolved: dict[str, Path] = {}
        for key, name in folders.items():
            candidate = self._contained(subject_root / name)
            candidate.mkdir(parents=True, exist_ok=True)
            resolved[key] = self._contained(candidate)
        return SubjectTree(subject_root=subject_root, **resolved)

    @property
    def knowledge_root(self) -> Path:
        """The resolved fixed root used for every generated reading-layer file."""
        return self._knowledge_root

    def safe_root_file(self, filename: str) -> Path:
        """Return a validated, direct child of the fixed knowledge root."""
        if not _FILE_NAME.fullmatch(filename):
            raise ValueError("非法文件名")
        return self._contained(self._knowledge_root / filename)

    def report_directory(
        self, year: str | int, month: str | int, report_id: str
    ) -> Path:
        """Return one immutable report-run directory below the fixed report root."""
        if not str(year).isdigit() or not str(month).isdigit():
            raise ValueError("非法报告日期")
        if not _FILE_NAME.fullmatch(report_id):
            raise ValueError("非法报告编号")
        directory = self._contained(
            self._knowledge_root / "学习报告" / str(year) / str(month).zfill(2) / report_id
        )
        directory.mkdir(parents=True, exist_ok=True)
        return self._contained(directory)

    def latest_report_path(self) -> Path:
        directory = self._contained(self._knowledge_root / "学习报告")
        directory.mkdir(parents=True, exist_ok=True)
        return self._contained(directory / "最新学情报告.html")

    def exam_directory(
        self, year: str | int, month: str | int, exam_id: str
    ) -> Path:
        """Return one immutable approved-exam directory below the fixed root."""
        if not str(year).isdigit() or not str(month).isdigit():
            raise ValueError("非法模拟卷日期")
        if not _FILE_NAME.fullmatch(exam_id):
            raise ValueError("非法模拟卷编号")
        directory = self._contained(
            self._knowledge_root / "模拟试卷" / str(year) / str(month).zfill(2) / exam_id
        )
        directory.mkdir(parents=True, exist_ok=True)
        return self._contained(directory)

    def safe_file_path(self, subject: str, folder: str, filename: str) -> Path:
        """Resolve a validated filename in one fixed subject subdirectory."""
        tree = self.ensure_subject_tree(subject)
        fixed_folders = {
            "原始资料": tree.raw,
            "分析记录": tree.analysis,
            "错题": tree.mistakes,
            "知识点": tree.knowledge_points,
            "待处理": tree.pending,
        }
        try:
            directory = fixed_folders[folder]
        except KeyError as exc:
            raise ValueError("非法资料目录") from exc
        if not _FILE_NAME.fullmatch(filename):
            raise ValueError("非法文件名")
        return self._contained(directory / filename, tree.subject_root)

    def document_directory(
        self, subject: str, year: str | int, month: str | int, document_id: str
    ) -> Path:
        """Return a guarded raw-document directory using validated path components."""
        if not str(year).isdigit() or not str(month).isdigit():
            raise ValueError("非法归档日期")
        if not _FILE_NAME.fullmatch(document_id):
            raise ValueError("非法文件名")
        tree = self.ensure_subject_tree(subject)
        directory = self._contained(
            tree.raw / str(year) / str(month).zfill(2) / document_id,
            tree.subject_root,
        )
        directory.mkdir(parents=True, exist_ok=True)
        return self._contained(directory, tree.subject_root)

    def _validate_subject(self, subject: str) -> None:
        if subject not in self.config.subjects:
            raise ValueError("非法科目")

    def _contained(self, candidate: Path, root: Path | None = None) -> Path:
        resolved_root = (root or self._knowledge_root).resolve()
        resolved_candidate = candidate.resolve()
        try:
            resolved_candidate.relative_to(resolved_root)
        except ValueError as exc:
            raise ValueError("路径越界") from exc
        return resolved_candidate
