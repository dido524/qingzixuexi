"""Atomic, recoverable landing of captured camera pages before model analysis."""

from __future__ import annotations

from hashlib import sha256
import io
import json
from pathlib import Path
import re
import uuid

import numpy as np
from PIL import Image

from qingzi_learning.camera.quality import ImageQualityRejected, check_image_quality
from qingzi_learning.config import AppConfig
from qingzi_learning.domain import CapturedDocument, CapturedPage
from qingzi_learning.export.safe_write import atomic_write_bytes


_SESSION_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,127}$")
_STATE_FILE = "session.json"
_OPERATION_FILE = "operation.json"


class CaptureSession:
    """One local capture session whose accepted pages always exist on disk first."""

    def __init__(self, config: AppConfig, session_id: str | None = None) -> None:
        self._session_id = session_id or f"capture-{uuid.uuid4().hex}"
        if not _SESSION_ID.fullmatch(self._session_id):
            raise ValueError("非法拍摄会话标识")
        spool_root = config.spool_root.resolve()
        self.session_dir = (spool_root / self._session_id).resolve()
        try:
            self.session_dir.relative_to(spool_root)
        except ValueError as exc:
            raise ValueError("会话目录越界") from exc
        if self.session_dir.exists():
            raise FileExistsError("拍摄会话已存在，请使用 recover 恢复")

        self.session_dir.mkdir(parents=True)
        self._pages: list[CapturedPage] = []
        self._current_page_number = 1
        self._finished = False
        self._write_state()

    @property
    def pages(self) -> list[CapturedPage]:
        return list(self._pages)

    @property
    def current_page_number(self) -> int:
        return self._current_page_number

    @property
    def finished(self) -> bool:
        return self._finished

    def capture(self, frame: np.ndarray | Image.Image) -> CapturedPage:
        """Persist the current page atomically and only then return its page metadata."""
        self._assert_active()
        if self._page_for_current() is not None:
            raise ValueError("当前页已拍摄，请使用 retake 重拍")
        self._require_acceptable(frame)

        path = self._page_path(self._current_page_number)
        image_bytes = _jpeg_bytes(frame)
        page = CapturedPage(
            page_number=self._current_page_number,
            path=path,
            sha256=sha256(image_bytes).hexdigest(),
        )
        before = self._state_payload()
        after = self._state_payload(pages=[*self._pages, page])
        self._write_operation("capture", page, before, after)
        self._write_bytes_atomic(path, image_bytes)
        self._write_state(after)
        self._pages.append(page)
        self._clear_operation()
        return page

    def retake(self, frame: np.ndarray | Image.Image) -> CapturedPage:
        """Replace the current page only after preserving its old bytes for audit."""
        return self.retake_page(self._current_page_number, frame)

    def retake_page(self, page_number: int, frame: np.ndarray | Image.Image) -> CapturedPage:
        """Replace one already accepted page without changing the active page cursor.

        This lets the UI select an earlier thumbnail for a deliberate retake while
        retaining the same audit journal and crash recovery semantics as ``retake``.
        """
        self._assert_active()
        self._require_acceptable(frame)
        previous = next((page for page in self._pages if page.page_number == page_number), None)
        if previous is None:
            raise ValueError("选中页尚未拍摄，不能重拍")
        if _sha256_file(previous.path) != previous.sha256:
            raise ValueError("已拍摄页面哈希不匹配")

        audit_path = (
            self.session_dir / "discarded" / previous.sha256[:12] / previous.path.name
        )
        image_bytes = _jpeg_bytes(frame)
        replacement = CapturedPage(
            page_number=previous.page_number,
            path=previous.path,
            sha256=sha256(image_bytes).hexdigest(),
        )
        before = self._state_payload()
        replacement_pages = list(self._pages)
        replacement_pages[replacement_pages.index(previous)] = replacement
        after = self._state_payload(pages=replacement_pages)
        self._write_operation("retake", replacement, before, after, previous, audit_path)
        self._copy_atomic(previous.path, audit_path)
        self._write_bytes_atomic(previous.path, image_bytes)
        self._write_state(after)
        self._pages = replacement_pages
        self._clear_operation()
        return replacement

    def next_page(self) -> int:
        """Advance only after the current page is durably accepted."""
        self._assert_active()
        if self._page_for_current() is None:
            raise ValueError("请先拍摄当前页")
        next_page_number = self._current_page_number + 1
        self._write_state(self._state_payload(current_page_number=next_page_number))
        self._current_page_number = next_page_number
        return self._current_page_number

    def finish(self) -> CapturedDocument:
        """Mark the session complete and expose its already durable document pages."""
        self._assert_active()
        if not self._pages:
            raise ValueError("至少需要一张已拍摄页面")
        self._write_state(self._state_payload(finished=True))
        self._finished = True
        return CapturedDocument(document_id=self._session_id, pages=tuple(self._pages))

    @classmethod
    def recover(cls, session_dir: Path) -> CaptureSession:
        """Restore only fully written, hash-verified pages from an atomic state file."""
        session_dir = Path(session_dir).resolve()
        state_path = session_dir / _STATE_FILE
        state = cls._read_state(session_dir)
        operation = cls._read_operation(session_dir)
        if operation is not None:
            state = cls._reconcile_operation(session_dir, state, operation)
        session_id, current_page_number, finished, pages = cls._pages_from_state(session_dir, state)

        restored = cls.__new__(cls)
        restored._session_id = session_id
        restored.session_dir = session_dir
        restored._pages = pages
        restored._current_page_number = current_page_number
        restored._finished = finished
        return restored

    def _assert_active(self) -> None:
        if self._finished:
            raise ValueError("拍摄会话已完成")
        if (self.session_dir / _OPERATION_FILE).exists():
            raise RuntimeError("检测到未完成拍摄操作，请先恢复会话")

    def _page_for_current(self) -> CapturedPage | None:
        for page in self._pages:
            if page.page_number == self._current_page_number:
                return page
        return None

    def _require_acceptable(self, frame: np.ndarray | Image.Image) -> None:
        result = check_image_quality(frame)
        if not result.acceptable:
            raise ImageQualityRejected(result)

    def _page_path(self, page_number: int) -> Path:
        return self.session_dir / f"page_{page_number:03d}.jpg"

    def _state_payload(
        self,
        *,
        pages: list[CapturedPage] | None = None,
        current_page_number: int | None = None,
        finished: bool | None = None,
    ) -> dict[str, object]:
        return {
            "version": 1,
            "session_id": self._session_id,
            "current_page_number": (
                self._current_page_number if current_page_number is None else current_page_number
            ),
            "finished": self._finished if finished is None else finished,
            "pages": [
                {
                    "page_number": page.page_number,
                    "path": page.path.name,
                    "sha256": page.sha256,
                }
                for page in (self._pages if pages is None else pages)
            ],
        }

    def _write_state(self, state: dict[str, object] | None = None) -> None:
        self._write_json_atomic(self.session_dir / _STATE_FILE, state or self._state_payload())

    def _write_operation(
        self,
        kind: str,
        page: CapturedPage,
        before: dict[str, object],
        after: dict[str, object],
        previous: CapturedPage | None = None,
        audit_path: Path | None = None,
    ) -> None:
        operation: dict[str, object] = {
            "version": 1,
            "kind": kind,
            "page_number": page.page_number,
            "target_path": page.path.name,
            "new_sha256": page.sha256,
            "before": before,
            "after": after,
        }
        if previous is not None and audit_path is not None:
            operation["old_sha256"] = previous.sha256
            operation["audit_path"] = audit_path.relative_to(self.session_dir).as_posix()
        self._write_json_atomic(self.session_dir / _OPERATION_FILE, operation)

    def _clear_operation(self) -> None:
        (self.session_dir / _OPERATION_FILE).unlink(missing_ok=True)

    @classmethod
    def _read_state(cls, session_dir: Path) -> dict[str, object]:
        return cls._read_json(session_dir / _STATE_FILE)

    @classmethod
    def _read_operation(cls, session_dir: Path) -> dict[str, object] | None:
        path = session_dir / _OPERATION_FILE
        return None if not path.exists() else cls._read_json(path)

    @staticmethod
    def _read_json(path: Path) -> dict[str, object]:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("无法恢复拍摄会话状态") from exc
        if not isinstance(value, dict):
            raise ValueError("无法恢复拍摄会话状态")
        return value

    @classmethod
    def _reconcile_operation(
        cls, session_dir: Path, state: dict[str, object], operation: dict[str, object]
    ) -> dict[str, object]:
        before = operation.get("before")
        after = operation.get("after")
        if not isinstance(before, dict) or not isinstance(after, dict):
            raise ValueError("无法恢复拍摄会话状态")
        cls._validate_state_shape(session_dir, before)
        cls._validate_state_shape(session_dir, after)
        if state != before and state != after:
            raise ValueError("拍摄操作与会话状态不一致")
        kind = operation.get("kind")
        page_number = operation.get("page_number")
        target_path = operation.get("target_path")
        new_digest = operation.get("new_sha256")
        expected_name = f"page_{page_number:03d}.jpg" if type(page_number) is int else None
        if (
            kind not in ("capture", "retake")
            or target_path != expected_name
            or not _is_sha256(new_digest)
        ):
            raise ValueError("无法恢复拍摄会话状态")
        target = session_dir / target_path
        actual_digest = _sha256_file(target) if target.is_file() else None
        if kind == "capture":
            if state == after and actual_digest != new_digest:
                raise ValueError("已拍摄页面哈希不匹配")
            desired = after if actual_digest == new_digest else before if actual_digest is None else None
        else:
            old_digest = operation.get("old_sha256")
            audit_path = operation.get("audit_path")
            expected_audit = f"discarded/{old_digest[:12]}/{target_path}" if _is_sha256(old_digest) else None
            if not _is_sha256(old_digest) or audit_path != expected_audit:
                raise ValueError("无法恢复拍摄会话状态")
            if state == after and actual_digest != new_digest:
                raise ValueError("已拍摄页面哈希不匹配")
            if actual_digest == new_digest:
                audit = session_dir / audit_path
                if old_digest == new_digest and state == before and not audit.is_file():
                    desired = before
                elif not audit.is_file() or _sha256_file(audit) != old_digest:
                    raise ValueError("缺少重拍审计副本")
                else:
                    desired = after
            elif actual_digest == old_digest and state == before:
                desired = before
            else:
                desired = None
        if desired is None:
            raise ValueError("无法安全恢复未完成拍摄操作")
        if desired != state:
            cls._write_json_atomic(session_dir / _STATE_FILE, desired)
        (session_dir / _OPERATION_FILE).unlink(missing_ok=True)
        return desired

    @classmethod
    def _pages_from_state(
        cls, session_dir: Path, state: dict[str, object]
    ) -> tuple[str, int, bool, list[CapturedPage]]:
        session_id, current_page_number, finished, pages_data = cls._validate_state_shape(
            session_dir, state
        )
        pages: list[CapturedPage] = []
        for expected_number, item in enumerate(pages_data, start=1):
            path = session_dir / item["path"]
            digest = item["sha256"]
            if not path.is_file():
                raise ValueError("缺少已拍摄页面")
            if _sha256_file(path) != digest:
                raise ValueError("已拍摄页面哈希不匹配")
            pages.append(CapturedPage(expected_number, path, digest))
        return session_id, current_page_number, finished, pages

    @staticmethod
    def _validate_state_shape(
        session_dir: Path, state: dict[str, object]
    ) -> tuple[str, int, bool, list[dict[str, object]]]:
        if state.get("version") != 1:
            raise ValueError("无法恢复拍摄会话状态")
        session_id = state.get("session_id")
        current_page_number = state.get("current_page_number")
        finished = state.get("finished")
        pages_data = state.get("pages")
        if (
            not isinstance(session_id, str)
            or not _SESSION_ID.fullmatch(session_id)
            or session_id != session_dir.name
            or type(current_page_number) is not int
            or current_page_number < 1
            or not isinstance(finished, bool)
            or not isinstance(pages_data, list)
        ):
            raise ValueError("无法恢复拍摄会话状态")
        pages: list[dict[str, object]] = []
        for expected_number, item in enumerate(pages_data, start=1):
            if not isinstance(item, dict):
                raise ValueError("无法恢复拍摄会话状态")
            relative_path = item.get("path")
            digest = item.get("sha256")
            expected_name = f"page_{expected_number:03d}.jpg"
            if item.get("page_number") != expected_number or relative_path != expected_name or not _is_sha256(digest):
                raise ValueError("无法恢复拍摄会话状态")
            path = (session_dir / relative_path).resolve()
            try:
                path.relative_to(session_dir)
            except ValueError as exc:
                raise ValueError("拍摄页路径越界") from exc
            pages.append({"page_number": expected_number, "path": relative_path, "sha256": digest})
        if current_page_number < len(pages) or current_page_number > len(pages) + 1:
            raise ValueError("当前页序号不合法")
        return session_id, current_page_number, finished, pages

    @staticmethod
    def _copy_atomic(source: Path, destination: Path) -> None:
        CaptureSession._write_bytes_atomic(destination, source.read_bytes())

    @staticmethod
    def _write_json_atomic(path: Path, value: dict[str, object]) -> None:
        CaptureSession._write_bytes_atomic(
            path, json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
        )

    @staticmethod
    def _write_bytes_atomic(path: Path, data: bytes) -> None:
        atomic_write_bytes(path, data, path.parent)


def _as_rgb_image(frame: np.ndarray | Image.Image) -> Image.Image:
    if isinstance(frame, Image.Image):
        return frame.convert("RGB")
    array = np.asarray(frame)
    if array.ndim == 2:
        return Image.fromarray(array).convert("RGB")
    if array.ndim != 3 or array.shape[2] not in (1, 3, 4):
        raise ValueError("无效图片帧")
    if array.shape[2] == 1:
        return Image.fromarray(array[:, :, 0]).convert("RGB")
    if array.shape[2] == 3:
        return Image.fromarray(array[:, :, ::-1]).convert("RGB")
    return Image.fromarray(array[:, :, [2, 1, 0, 3]], mode="RGBA").convert("RGB")


def _jpeg_bytes(frame: np.ndarray | Image.Image) -> bytes:
    image = _as_rgb_image(frame)
    buffer = io.BytesIO()
    image.save(buffer, format="JPEG", quality=95)
    return buffer.getvalue()


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        char in "0123456789abcdef" for char in value
    )
