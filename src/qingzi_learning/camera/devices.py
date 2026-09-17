"""Select and open only the configured document camera hardware."""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Any

import cv2
from cv2_enumerate_cameras import enumerate_cameras


@dataclass(frozen=True)
class CameraDescriptor:
    index: int
    backend: int
    name: str
    vid: int | None
    pid: int | None
    fallback: "CameraDescriptor | None" = None


class CameraNotFound(RuntimeError):
    """Raised when the configured VID/PID is absent from the enumerated cameras."""


class CameraBusy(RuntimeError):
    """Raised when the selected document camera cannot provide stable frames."""


def enumerate_document_cameras() -> list[CameraDescriptor]:
    """Enumerate MSMF first, retaining DSHOW entries only as same-device fallbacks."""
    cameras: list[CameraDescriptor] = []
    for backend in (cv2.CAP_MSMF, cv2.CAP_DSHOW):
        cameras.extend(_descriptor(info, backend) for info in enumerate_cameras(backend))
    return cameras


def find_document_camera(
    enumerator: Callable[[], Iterable[CameraDescriptor]], vid: int, pid: int
) -> CameraDescriptor:
    """Return the target camera, preferring MSMF without permitting other hardware."""
    matching = [device for device in enumerator() if device.vid == vid and device.pid == pid]
    if not matching:
        raise CameraNotFound(f"未找到证件拍照机 {vid:04X}:{pid:04X}")

    msmf = next((device for device in matching if device.backend == cv2.CAP_MSMF), None)
    if msmf is None:
        raise CameraNotFound(f"未找到可用的 MSMF 证件拍照机 {vid:04X}:{pid:04X}")

    dshow = next((device for device in matching if device.backend == cv2.CAP_DSHOW), None)
    return CameraDescriptor(
        index=msmf.index,
        backend=msmf.backend,
        name=msmf.name,
        vid=msmf.vid,
        pid=msmf.pid,
        fallback=dshow,
    )


def open_document_camera(descriptor: CameraDescriptor) -> Any:
    """Open and stabilize the selected camera, using only its paired fallback."""
    capture = _open(descriptor)
    if not capture.isOpened():
        capture.release()
        if not _is_same_hardware(descriptor, descriptor.fallback):
            raise CameraBusy(f"证件拍照机无法打开：{descriptor.name}")
        capture = _open(descriptor.fallback)
        if not capture.isOpened():
            capture.release()
            raise CameraBusy(f"证件拍照机无法打开：{descriptor.name}")

    # UVC devices commonly default to VGA, below the 1200px document gate.
    # Negotiate a readable mode before warm-up; quality checks still validate
    # actual returned pixels instead of trusting a driver's property report.
    capture.set(cv2.CAP_PROP_FRAME_WIDTH, 1600)
    capture.set(cv2.CAP_PROP_FRAME_HEIGHT, 1200)
    for _ in range(10):
        ok, _frame = capture.read()
        if not ok:
            capture.release()
            raise CameraBusy(f"证件拍照机预热失败：{descriptor.name}")
    return capture


def _open(descriptor: CameraDescriptor) -> Any:
    return cv2.VideoCapture(descriptor.index, descriptor.backend)


def _is_same_hardware(
    primary: CameraDescriptor, fallback: CameraDescriptor | None
) -> bool:
    return (
        fallback is not None
        and fallback.vid == primary.vid
        and fallback.pid == primary.pid
    )


def _descriptor(camera: Any, backend: int) -> CameraDescriptor:
    return CameraDescriptor(
        index=int(camera.index),
        backend=backend,
        name=str(camera.name),
        vid=_optional_int(camera.vid),
        pid=_optional_int(camera.pid),
    )


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)
