import json
import numpy as np
import pytest
from hashlib import sha256
from PIL import Image

from qingzi_learning import main
from qingzi_learning.camera.devices import CameraBusy


@pytest.mark.parametrize("failure", [False, True])
def test_camera_smoke_uses_production_factory_and_writes_metadata_only(tmp_path, monkeypatch, failure):
    monkeypatch.setenv("QINGZI_CAMERA_ACCEPTANCE_ROOT", str(tmp_path))
    instances = []
    class Camera:
        device_info = {"vid": "BC15", "pid": "2C1B", "backend": "MSMF"}
        closed = False
        def __init__(self, config):
            assert config.camera_vid == 0xBC15 and config.camera_pid == 0x2C1B
            self.reads = 0
            instances.append(self)
        def read(self):
            self.reads += 1
            if failure:
                raise CameraBusy("private error must not be recorded")
            return True, np.zeros((1200, 1600, 3), dtype=np.uint8)
        def release(self):
            self.closed = True
    monkeypatch.setattr(main, "CameraProcess", Camera, raising=False)
    def forbidden(*args, **kwargs):
        raise AssertionError("camera diagnostic must not initialize UI, repository or analyzer")
    for name in ("KnowledgeRepository", "CodexCliAnalyzer", "LearningAssistantApp"):
        monkeypatch.setattr(main, name, forbidden)
    path = tmp_path / "camera.json"
    assert main.main(["--camera-smoke-check", str(path)]) == (4 if failure else 0)
    report = json.loads(path.read_text("utf-8"))
    assert report["frame_received"] is not failure
    assert report["helper_closed"] and instances[0].closed and instances[0].reads == 1
    assert list(tmp_path.iterdir()) == [path]
    assert "private" not in path.read_text("utf-8") and "image" not in report
    if not failure:
        assert report["device"] == Camera.device_info
        assert report["width"] == 1600 and report["height"] == 1200
        assert report["quality_acceptable"] is False  # Black frames prove transport, not a readable scene.


def test_explicit_acceptance_preview_saves_one_decodable_frame_without_analysis(tmp_path, monkeypatch):
    class Camera:
        device_info = {"vid": "BC15", "pid": "2C1B", "backend": "MSMF"}
        closed = False
        def __init__(self, config): pass
        def read(self): return True, np.full((1200, 1600, 3), 125, dtype=np.uint8)
        def release(self): self.closed = True
    monkeypatch.setattr(main, "CameraProcess", Camera)
    def forbidden(*args, **kwargs):
        raise AssertionError("preview must not initialize UI, repository or analyzer")
    for name in ("KnowledgeRepository", "CodexCliAnalyzer", "LearningAssistantApp"):
        monkeypatch.setattr(main, name, forbidden)
    monkeypatch.setenv("QINGZI_CAMERA_ACCEPTANCE_ROOT", str(tmp_path))
    preview, report = tmp_path / "preview.jpg", tmp_path / "report.json"
    assert main.main(["--camera-smoke-check", str(report), "--camera-preview-output", str(preview)]) == 0
    data = json.loads(report.read_text("utf-8"))
    assert data["preview"] == {"path": str(preview), "sha256": sha256(preview.read_bytes()).hexdigest()}
    with Image.open(preview) as image:
        assert image.size == (1600, 1200)
        image.verify()
    assert set(tmp_path.iterdir()) == {preview, report}


@pytest.mark.parametrize("mode", ["unset", "relative_root", "relative_output", "escape", "knowledge_root", "root_ancestor", "same_as_report"])
def test_acceptance_preview_rejects_unsafe_destination_before_camera_open(tmp_path, monkeypatch, mode):
    root, destination = tmp_path / "acceptance", tmp_path / "acceptance" / "preview.jpg"
    if mode == "relative_root": root = main.Path("relative")
    if mode == "relative_output": destination = main.Path("preview.jpg")
    if mode == "escape": destination = tmp_path / "outside.jpg"
    if mode == "knowledge_root": root = main.load_config().knowledge_root; destination = root / "preview.jpg"
    if mode == "root_ancestor": root = main.load_config().knowledge_root.parent; destination = root / "preview.jpg"
    if mode == "unset": monkeypatch.delenv("QINGZI_CAMERA_ACCEPTANCE_ROOT", raising=False)
    else: monkeypatch.setenv("QINGZI_CAMERA_ACCEPTANCE_ROOT", str(root))
    def forbidden(*args, **kwargs): raise AssertionError("unsafe preview must not open camera")
    monkeypatch.setattr(main, "CameraProcess", forbidden)
    report = tmp_path / "report.json"
    if mode == "same_as_report": destination = report; monkeypatch.setenv("QINGZI_CAMERA_ACCEPTANCE_ROOT", str(tmp_path))
    assert main.main(["--camera-smoke-check", str(report), "--camera-preview-output", str(destination)]) == 5
    assert json.loads(report.read_text("utf-8"))["status"] == "invalid_preview_destination"
    assert not list(tmp_path.rglob("*.jpg"))


def test_preview_option_requires_camera_smoke(tmp_path):
    with pytest.raises(SystemExit) as error:
        main.main(["--camera-preview-output", str(tmp_path / "preview.jpg")])
    assert error.value.code == 2


def test_acceptance_preview_rejects_reparse_parent_before_camera_open(tmp_path, monkeypatch):
    root = tmp_path / "acceptance"
    monkeypatch.setenv("QINGZI_CAMERA_ACCEPTANCE_ROOT", str(root))
    monkeypatch.setattr(main, "is_reparse_point", lambda path: path == tmp_path)
    monkeypatch.setattr(main, "CameraProcess", lambda config: pytest.fail("unsafe camera open"))
    report = tmp_path / "report.json"
    assert main.main(["--camera-smoke-check", str(report), "--camera-preview-output", str(root / "preview.jpg")]) == 5
    assert not list(tmp_path.rglob("*.jpg"))
