"""Stage outside SQLite; serialize visible publication and its durable manifest.

Lock order is always root publication lock -> short SQLite write transaction.
Renderers run with neither lock. No caller may acquire this lock inside a DB
transaction. OS byte/advisory locks are released by close, including process exit.
"""
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from uuid import uuid4

from qingzi_learning.export.safe_write import _guard, atomic_write, replace_guarded


_batch = ContextVar("reading_publication_batch", default=None)
_locks_guard = threading.Lock()
_locks = {}


@contextmanager
def publication_lock(root: Path, timeout: float = 10.0):
    root = root.absolute()
    _guard(root, root)
    root.mkdir(parents=True, exist_ok=True)
    path = root / ".publication.lock"
    _guard(path, root)
    key = os.path.normcase(str(path.resolve()))
    with _locks_guard:
        local = _locks.setdefault(key, threading.Lock())
    deadline = time.monotonic() + timeout
    if not local.acquire(timeout=timeout):
        raise TimeoutError("知识库发布忙，请重试")
    descriptor = None
    locked = False
    try:
        _guard(path, root)
        descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0), 0o600)
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        while True:
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("知识库发布忙，请重试") from None
                time.sleep(min(0.025, max(0, deadline - time.monotonic())))
        yield
    finally:
        try:
            if descriptor is not None:
                try:
                    if locked:
                        os.lseek(descriptor, 0, os.SEEK_SET)
                        if os.name == "nt":
                            import msvcrt
                            msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
                        else:
                            import fcntl
                            fcntl.flock(descriptor, fcntl.LOCK_UN)
                finally:
                    os.close(descriptor)
        finally:
            local.release()


def write_output(destination: Path, content: str, root: Path) -> None:
    """Exporters use this seam; standalone exports keep their atomic-write API."""
    batch = _batch.get()
    if batch is None:
        atomic_write(destination, content, root)
    else:
        batch.stage(destination, content, root)


class OutputBatch:
    def __init__(self, root):
        self.root = Path(root).absolute()
        self.files = {}
        self.temporary = []
        self.generation = uuid4().hex

    def stage(self, destination, content, root):
        if Path(root).resolve() != self.root.resolve():
            raise ValueError("发布根目录不一致")
        destination = Path(destination).absolute()
        _guard(destination, self.root)
        staging = destination.parent
        _guard(staging, self.root)
        staging.mkdir(parents=True, exist_ok=True)
        _guard(staging, self.root)
        descriptor, name = tempfile.mkstemp(prefix=self.generation + "-", suffix=".part", dir=staging)
        temporary = Path(name)
        self.temporary.append(temporary)
        data = content.encode("utf-8")
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        self.files[destination.relative_to(self.root).as_posix()] = (temporary, sha256(data).hexdigest())

    def __enter__(self):
        self.token = _batch.set(self)
        return self

    def __exit__(self, *exc):
        _batch.reset(self.token)

    def cleanup(self):
        for path in self.temporary:
            _guard(path, self.root)
            path.unlink(missing_ok=True)


class PublicationCoordinator:
    def __init__(self, repo):
        self.repo = repo
        self.root = repo.config.knowledge_root.absolute()

    def _expected_outputs(self) -> set[str]:
        # Local import: exporters route writes through this module. Destination
        # reconstruction uses their path rules but never invokes external renderers.
        from qingzi_learning.export.dashboard import DashboardExporter
        return {path.absolute().relative_to(self.root).as_posix()
                for path in DashboardExporter(self.repo).expected_paths()}

    def current(self) -> bool:
        if self.repo.connection.in_transaction:
            raise RuntimeError("发布锁不能在数据库事务内获取")
        with publication_lock(self.root), self.repo.connection:
            self.repo.connection.execute("BEGIN IMMEDIATE")
            row = self.repo.connection.execute("SELECT * FROM reading_publication WHERE singleton=1").fetchone()
            if row is None or row["pending"] or row["facts_hash"] != self.repo.reading_revision():
                return False
            try:
                files = json.loads(row["files_json"])
                if not isinstance(files, dict) or not files:
                    return False
                output_set = self.repo.connection.execute(
                    "SELECT generation, paths_json FROM reading_output_set WHERE singleton=1").fetchone()
                if output_set is None or output_set["generation"] != row["generation"]:
                    return False
                expected = json.loads(output_set["paths_json"])
                if (not isinstance(expected, list) or not expected
                        or not all(isinstance(path, str) for path in expected)
                        or len(set(expected)) != len(expected)
                        or set(files) != set(expected) or set(expected) != self._expected_outputs()):
                    return False
                for relative, digest in files.items():
                    path = self.root / relative
                    _guard(path, self.root)
                    if sha256(path.read_bytes()).hexdigest() != digest:
                        return False
                # Parent notes are linked by generated Markdown, but their
                # contents are never owned by the publication manifest.
                from qingzi_learning.export.dashboard import DashboardExporter
                if not all(path.is_file() for path in DashboardExporter(self.repo).parent_note_paths()):
                    return False
            except (OSError, ValueError, TypeError):
                return False
            return True

    def publish(self, expected, render, finalize, *, review_revision=None, ownership_check=None) -> bool:
        """Publish only while both the journal and optional captured batch owner match.

        ownership_check must be read-only: it runs under the publication lock
        and inside each write transaction, including before manifest creation,
        every visible replacement, and finalization. It covers owner changes
        that predate this coordinator call's connection-version baseline.
        """
        if self.repo.connection.in_transaction:
            raise RuntimeError("渲染不能在数据库事务内运行")
        # data_version observes other connections; total_changes observes this
        # connection. Together they reject every intervening write without doing
        # a whole-library hash for each file. The durable hash still repairs files
        # across restarts, when these connection-local stamps are not comparable.
        foreign_version = self.repo.connection.execute("PRAGMA data_version").fetchone()[0]
        local_changes = self.repo.connection.total_changes
        render_day = datetime.now(timezone.utc).date()
        facts = self.repo.reading_revision()
        batch = OutputBatch(self.root)
        try:
            with batch:
                paths = render()
            with publication_lock(self.root):
                def current():
                    return (self.repo.workflow_job_matches(expected)
                            and (review_revision is None or self.repo.review_publication_revision(expected.job_id) == review_revision)
                            and (ownership_check is None or ownership_check())
                            and self.repo.connection.total_changes == local_changes
                            and self.repo.connection.execute("PRAGMA data_version").fetchone()[0] == foreign_version
                            and datetime.now(timezone.utc).date() == render_day)

                with self.repo.connection:
                    self.repo.connection.execute("BEGIN IMMEDIATE")
                    if not current():
                        return False
                    if not batch.files or set(batch.files) != self._expected_outputs():
                        raise ValueError("发布结果不完整或含有非预期文件")
                    manifest = json.dumps({key: value[1] for key, value in batch.files.items()}, ensure_ascii=False)
                    self.repo.connection.execute(
                        """INSERT INTO reading_publication VALUES (1, ?, ?, ?, 1)
                        ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation,
                        facts_hash=excluded.facts_hash, files_json=excluded.files_json, pending=1""",
                        (batch.generation, facts, manifest))
                    self.repo.connection.execute(
                        """INSERT INTO reading_output_set VALUES (1, ?, ?)
                        ON CONFLICT(singleton) DO UPDATE SET generation=excluded.generation,
                        paths_json=excluded.paths_json""",
                        (batch.generation, json.dumps(sorted(batch.files), ensure_ascii=False)))
                    local_changes = self.repo.connection.total_changes
                for relative, (temporary, digest) in batch.files.items():
                    with self.repo.connection:
                        self.repo.connection.execute("BEGIN IMMEDIATE")
                        if not current():
                            return False
                        destination = self.root / relative
                        _guard(destination, self.root)
                        _guard(temporary, self.root)
                        if sha256(temporary.read_bytes()).hexdigest() != digest:
                            raise ValueError("暂存发布内容已变化")
                        # Only guarded local replacement occurs in this short DB
                        # transaction. External rendering never runs under either lock.
                        replace_guarded(temporary, destination, self.root)
                with self.repo.connection:
                    self.repo.connection.execute("BEGIN IMMEDIATE")
                    if not current():
                        return False
                    finalize(paths)
                    self.repo.connection.execute("UPDATE reading_publication SET pending=0 WHERE singleton=1")
                return True
        finally:
            batch.cleanup()
