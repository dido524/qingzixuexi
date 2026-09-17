from hashlib import sha256
import json
from pathlib import Path

import numpy as np
import pytest

from qingzi_learning.camera.quality import ImageQualityRejected
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig


@pytest.fixture
def clear_frame() -> np.ndarray:
    frame = np.full((1200, 1600, 3), 200, dtype=np.uint8)
    frame[:, ::80] = 40
    frame[::60, :] = 40
    return frame


@pytest.fixture
def second_frame() -> np.ndarray:
    frame = np.full((1200, 1600, 3), 180, dtype=np.uint8)
    frame[:, ::70] = 20
    frame[::50, :] = 20
    return frame


@pytest.fixture
def blurry_dark_frame() -> np.ndarray:
    return np.full((1200, 1600, 3), 20, dtype=np.uint8)


@pytest.fixture
def make_session(tmp_path: Path):
    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "app-data",
    )

    def create(session_id: str = "capture-001") -> CaptureSession:
        return CaptureSession(config, session_id=session_id)

    return create


@pytest.fixture
def session(make_session) -> CaptureSession:
    return make_session()


def test_capture_writes_page_before_reporting_success(
    session: CaptureSession, clear_frame: np.ndarray
) -> None:
    """A returned page must already be durable model input evidence."""
    page = session.capture(clear_frame)

    assert page.path.exists()
    assert page.path.name == "page_001.jpg"
    assert page.sha256 == sha256(page.path.read_bytes()).hexdigest()


def test_retake_keeps_audit_copy(
    session: CaptureSession, clear_frame: np.ndarray, second_frame: np.ndarray
) -> None:
    """Replacing a page without its old bytes would make capture corrections unauditable."""
    first = session.capture(clear_frame)
    replacement = session.retake(second_frame)

    assert replacement.path.name == "page_001.jpg"
    assert replacement.sha256 != first.sha256
    assert (
        session.session_dir / "discarded" / first.sha256[:12] / "page_001.jpg"
    ).exists()


def test_retake_an_earlier_selected_page_keeps_current_page_and_audit(
    session: CaptureSession, clear_frame: np.ndarray, second_frame: np.ndarray
) -> None:
    first = session.capture(clear_frame)
    session.next_page()
    second = session.capture(clear_frame)
    replacement = session.retake_page(1, second_frame)

    assert replacement.page_number == 1
    assert session.current_page_number == 2
    assert session.pages[1] == second
    assert (session.session_dir / "discarded" / first.sha256[:12] / "page_001.jpg").exists()


def test_rejected_retake_preserves_previously_accepted_page(
    session: CaptureSession, clear_frame: np.ndarray, blurry_dark_frame: np.ndarray
) -> None:
    """Quality rejection must never destroy the page it was intended to replace."""
    first = session.capture(clear_frame)

    with pytest.raises(ImageQualityRejected):
        session.retake(blurry_dark_frame)

    assert first.path.read_bytes()
    assert sha256(first.path.read_bytes()).hexdigest() == first.sha256
    assert session.pages == [first]


def test_capture_writes_recoverable_state(
    session: CaptureSession, clear_frame: np.ndarray
) -> None:
    """Without a state record, an interrupted capture cannot safely resume."""
    page = session.capture(clear_frame)

    state = json.loads((session.session_dir / "session.json").read_text(encoding="utf-8"))
    assert state["pages"] == [
        {"page_number": page.page_number, "path": "page_001.jpg", "sha256": page.sha256}
    ]


def test_ten_page_session_recovers_in_order(make_session, clear_frame: np.ndarray) -> None:
    """Recovery must preserve every fully landed page in capture order."""
    session = make_session()
    for _ in range(10):
        session.capture(clear_frame)
        session.next_page()

    restored = CaptureSession.recover(session.session_dir)

    assert [page.page_number for page in restored.pages] == list(range(1, 11))


def test_recover_rejects_state_whose_page_hash_does_not_match(
    session: CaptureSession, clear_frame: np.ndarray
) -> None:
    """Trusting a changed image after restart would break evidence traceability."""
    page = session.capture(clear_frame)
    page.path.write_bytes(b"changed")

    with pytest.raises(ValueError, match="哈希"):
        CaptureSession.recover(session.session_dir)


def test_recover_rejects_boolean_current_page_number(
    session: CaptureSession, clear_frame: np.ndarray
) -> None:
    """A JSON boolean must not be interpreted as page number one during recovery."""
    session.capture(clear_frame)
    state_path = session.session_dir / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["current_page_number"] = True
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="无法恢复"):
        CaptureSession.recover(session.session_dir)


def test_recover_rejects_an_unknown_state_version(
    session: CaptureSession, clear_frame: np.ndarray
) -> None:
    """A future state format must not be guessed as the version this code understands."""
    session.capture(clear_frame)
    state_path = session.session_dir / "session.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["version"] = 2
    state_path.write_text(json.dumps(state), encoding="utf-8")

    with pytest.raises(ValueError, match="无法恢复"):
        CaptureSession.recover(session.session_dir)


def test_recover_rolls_forward_capture_when_state_write_fails_after_page_replace(
    session: CaptureSession, clear_frame: np.ndarray, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A post-replace state failure must not strand an unindexed page that a retry overwrites."""
    monkeypatch.setattr(session, "_write_state", _raise_state_write_failure)

    with pytest.raises(OSError, match="state write failed"):
        session.capture(clear_frame)

    with pytest.raises(RuntimeError, match="恢复"):
        session.capture(clear_frame)
    restored = CaptureSession.recover(session.session_dir)

    assert [page.page_number for page in restored.pages] == [1]
    assert restored.pages[0].sha256 == sha256(restored.pages[0].path.read_bytes()).hexdigest()


def test_recover_rolls_forward_retake_when_state_write_fails_after_page_replace(
    session: CaptureSession,
    clear_frame: np.ndarray,
    second_frame: np.ndarray,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A post-replace state failure must retain both the new page and prior audit bytes."""
    first = session.capture(clear_frame)
    monkeypatch.setattr(session, "_write_state", _raise_state_write_failure)

    with pytest.raises(OSError, match="state write failed"):
        session.retake(second_frame)

    restored = CaptureSession.recover(session.session_dir)

    replacement = restored.pages[0]
    assert replacement.sha256 == sha256(replacement.path.read_bytes()).hexdigest()
    assert replacement.sha256 != first.sha256
    audit_path = session.session_dir / "discarded" / first.sha256[:12] / "page_001.jpg"
    assert sha256(audit_path.read_bytes()).hexdigest() == first.sha256


def test_recover_keeps_original_page_when_identical_retake_stops_before_audit_copy(
    session: CaptureSession,
    clear_frame: np.ndarray,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Matching old/new hashes before audit creation means no replacement has to be inferred."""
    first = session.capture(clear_frame)
    monkeypatch.setattr(session, "_copy_atomic", _raise_audit_copy_failure)

    with pytest.raises(OSError, match="audit copy failed"):
        session.retake(clear_frame)

    restored = CaptureSession.recover(session.session_dir)

    assert restored.pages == [first]
    assert not (session.session_dir / "operation.json").exists()
    assert not (session.session_dir / "discarded" / first.sha256[:12] / "page_001.jpg").exists()


def _raise_state_write_failure(*_args, **_kwargs) -> None:
    raise OSError("state write failed")


def _raise_audit_copy_failure(*_args, **_kwargs) -> None:
    raise OSError("audit copy failed")
