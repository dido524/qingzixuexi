import pytest

from qingzi_learning.camera import devices
from qingzi_learning.camera.devices import (
    CameraBusy,
    CameraDescriptor,
    CameraNotFound,
    find_document_camera,
    open_document_camera,
)


def test_does_not_fallback_to_logitech_when_target_missing() -> None:
    """Removing VID/PID matching must never silently choose another camera."""
    devices = [
        CameraDescriptor(index=0, backend=700, name="Logi C270", vid=0x046D, pid=0x0825)
    ]

    with pytest.raises(CameraNotFound):
        find_document_camera(lambda: devices, 0xBC15, 0x2C1B)


def test_selects_target_by_vid_pid_even_when_second() -> None:
    """Selection is based on the hardware ID, not enumeration order."""
    devices = [
        CameraDescriptor(0, 700, "Logi C270", 0x046D, 0x0825),
        CameraDescriptor(1, 1400, "USB Camera", 0xBC15, 0x2C1B),
    ]

    found = find_document_camera(lambda: devices, 0xBC15, 0x2C1B)

    assert found.index == 1


def test_rejects_target_when_only_dshow_descriptor_is_enumerated() -> None:
    """DSHOW cannot open the target unless it is paired with a target MSMF primary."""
    devices_found = [
        CameraDescriptor(0, 700, "Logi C270", 0x046D, 0x0825),
        CameraDescriptor(1, 700, "USB Camera", 0xBC15, 0x2C1B),
    ]

    with pytest.raises(CameraNotFound):
        find_document_camera(lambda: devices_found, 0xBC15, 0x2C1B)


def test_prefers_msmf_and_records_only_matching_dshow_as_fallback() -> None:
    """Opening a target must never be allowed to fall back to Logitech's descriptor."""
    target_dshow = CameraDescriptor(1, 700, "USB Camera", 0xBC15, 0x2C1B)
    devices_found = [
        CameraDescriptor(0, 700, "Logi C270", 0x046D, 0x0825),
        target_dshow,
        CameraDescriptor(2, 1400, "USB Camera", 0xBC15, 0x2C1B),
    ]

    found = find_document_camera(lambda: devices_found, 0xBC15, 0x2C1B)

    assert found.index == 2
    assert found.backend == 1400
    assert found.fallback == target_dshow


def test_open_warms_ten_frames(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skipping warm-up would expose unstable initial camera frames to capture."""
    capture = _Capture([True] * 10)
    monkeypatch.setattr(devices, "cv2", _OpenCV(capture))

    opened = open_document_camera(CameraDescriptor(2, 1400, "USB Camera", 0xBC15, 0x2C1B))

    assert opened is capture
    assert capture.read_count == 10
    assert capture.released is False


def test_document_camera_requests_readable_resolution_before_warmup(monkeypatch):
    capture = _Capture([True] * 10)
    capture.size = {3: 640, 4: 480}
    def resize(prop, value):
        assert capture.read_count == 0
        capture.size[prop] = value
        return True
    capture.set = resize
    capture.get = lambda prop: capture.size.get(prop, 0)
    monkeypatch.setattr(devices, "cv2", _OpenCV(capture))
    open_document_camera(CameraDescriptor(0, 1400, "USB Camera", 0xBC15, 0x2C1B))
    assert min(capture.size[3], capture.size[4]) >= 1200


def test_open_retries_only_target_dshow_after_msmf_open_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed target MSMF open may use only its paired DSHOW fallback."""
    primary = _Capture([], opened=False)
    fallback = _Capture([True] * 10)
    opencv = _OpenCV(primary, fallback)
    monkeypatch.setattr(devices, "cv2", opencv)
    descriptor = CameraDescriptor(
        2,
        1400,
        "USB Camera",
        0xBC15,
        0x2C1B,
        fallback=CameraDescriptor(1, 700, "USB Camera", 0xBC15, 0x2C1B),
    )

    opened = open_document_camera(descriptor)

    assert opened is fallback
    assert opencv.open_calls == [(2, 1400), (1, 700)]


def test_open_raises_busy_and_releases_when_warmup_frame_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bad warm-up frame must not leave a potentially busy capture open."""
    capture = _Capture([True] * 9 + [False])
    monkeypatch.setattr(devices, "cv2", _OpenCV(capture))

    with pytest.raises(CameraBusy):
        open_document_camera(CameraDescriptor(2, 1400, "USB Camera", 0xBC15, 0x2C1B))

    assert capture.released is True


def test_open_rejects_mismatched_hardware_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    """A caller-supplied Logitech fallback must never be opened for the target device."""
    primary = _Capture([], opened=False)
    logitech = _Capture([True] * 10)
    opencv = _OpenCV(primary, logitech)
    monkeypatch.setattr(devices, "cv2", opencv)
    descriptor = CameraDescriptor(
        2,
        1400,
        "USB Camera",
        0xBC15,
        0x2C1B,
        fallback=CameraDescriptor(0, 700, "Logi C270", 0x046D, 0x0825),
    )

    with pytest.raises(CameraBusy):
        open_document_camera(descriptor)

    assert opencv.open_calls == [(2, 1400)]


class _OpenCV:
    CAP_PROP_FRAME_WIDTH = 3
    CAP_PROP_FRAME_HEIGHT = 4
    def __init__(self, *captures: "_Capture") -> None:
        self.captures = list(captures)
        self.open_calls: list[tuple[int, int]] = []

    def VideoCapture(self, index: int, backend: int) -> "_Capture":
        self.open_calls.append((index, backend))
        return self.captures.pop(0)


class _Capture:
    def __init__(self, frames: list[bool], opened: bool = True) -> None:
        self.frames = iter(frames)
        self.opened = opened
        self.read_count = 0
        self.released = False
        self.size = {3: 1600, 4: 1200}

    def set(self, prop, value):
        self.size[prop] = value
        return True

    def get(self, prop):
        return self.size.get(prop, 0)

    def isOpened(self) -> bool:
        return self.opened

    def read(self) -> tuple[bool, None]:
        self.read_count += 1
        return next(self.frames), None

    def release(self) -> None:
        self.released = True
