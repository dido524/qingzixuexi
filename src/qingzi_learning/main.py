"""Application composition entry point.

The factories below are passed to ``WorkflowWorker`` rather than called here so
SQLite and all controller work are created on the worker's one dedicated thread.
"""

from __future__ import annotations

import argparse
import tkinter as tk
import multiprocessing
from dataclasses import replace
from importlib.resources import files
from pathlib import Path
import shutil
import tempfile
import json
import sys
import os
from hashlib import sha256

from qingzi_learning.analysis.codex_cli import AnalysisError, CodexCliAnalyzer, resolve_codex_cli
from qingzi_learning.camera.devices import (
    enumerate_document_cameras,
    find_document_camera,
    open_document_camera,
)
from qingzi_learning.config import load_config
from qingzi_learning.camera.quality import check_image_quality
from qingzi_learning.export.safe_write import atomic_write, atomic_write_bytes, is_reparse_point
from qingzi_learning.ui.camera_process import CameraProcess
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.ui.app import LearningAssistantApp, WorkflowWorker
from qingzi_learning.workflow.controller import WorkflowController


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="晴子学习助手")
    parser.add_argument(
        "--smoke-check",
        action="store_true",
        help="只检查打包资源和 Codex CLI 可用性；不会打开摄像头、窗口或发起分析。",
    )
    parser.add_argument("--camera-smoke-check", type=Path, metavar="REPORT_JSON",
                        help="在内存读取一帧并写入设备/分辨率报告；默认不保存图片，永不上传或分析。")
    parser.add_argument("--camera-preview-output", type=Path, metavar="PREVIEW_JPG",
                        help="仅用于实机验收：显式保存一帧到 QINGZI_CAMERA_ACCEPTANCE_ROOT 内。")
    args = parser.parse_args(argv)
    if args.camera_preview_output and not args.camera_smoke_check:
        parser.error("--camera-preview-output requires --camera-smoke-check")
    return args


def _preview_root(output: Path, report: Path, knowledge_root: Path) -> Path:
    """Require an explicit isolated acceptance root before opening any camera."""
    configured = os.environ.get("QINGZI_CAMERA_ACCEPTANCE_ROOT", "")
    root = Path(configured)
    if not configured or not root.is_absolute() or not output.is_absolute():
        raise ValueError("invalid_preview_destination")
    if any(is_reparse_point(path) for path in (root, *root.parents, output, *output.parents)):
        raise ValueError("invalid_preview_destination")
    root, output, knowledge_root = root.resolve(), output.resolve(), knowledge_root.resolve()
    if root == Path(root.anchor) or root == Path.home().resolve():
        raise ValueError("invalid_preview_destination")
    if root.is_relative_to(knowledge_root) or knowledge_root.is_relative_to(root):
        raise ValueError("invalid_preview_destination")
    if not output.is_relative_to(root) or output.suffix.lower() != ".jpg" or output == report.resolve():
        raise ValueError("invalid_preview_destination")
    return root


def _camera_smoke_check(report_path: Path, preview_path: Path | None = None) -> int:
    """Exercise the exact production helper in the packaged interpreter.

    A black frame proves the software transport, not a readable physical scene.
    Default output is metadata only. Explicit isolated acceptance may save one
    frame; no database, UI or analyzer is ever constructed.
    """
    config = load_config()
    result = dict(frame_received=False, helper_closed=False, status="camera_unavailable",
                  python_version=sys.version.split()[0], frozen=bool(getattr(sys, "frozen", False)))
    camera = None
    code = 4
    if preview_path is not None:
        try:
            preview_root = _preview_root(preview_path, report_path, config.knowledge_root)
        except ValueError:
            result["status"] = "invalid_preview_destination"
            atomic_write(report_path.absolute(), json.dumps(result), report_path.absolute().parent)
            return 5
    try:
        camera = CameraProcess(config)
        ok, frame = camera.read()
        if not ok or frame is None:
            raise ValueError("no_frame")
        quality = check_image_quality(frame)
        height, width = frame.shape[:2]
        result.update(frame_received=True, device=camera.device_info, width=width, height=height,
                      quality_acceptable=quality.acceptable, quality_reasons=list(quality.reasons),
                      grayscale_mean=quality.grayscale_mean)
        expected = {"vid": f"{config.camera_vid:04X}", "pid": f"{config.camera_pid:04X}"}
        if all(result["device"].get(k) == v for k, v in expected.items()) and result["device"].get("backend") in ("MSMF", "DSHOW") and min(width, height) >= 1200:
            if preview_path is not None:
                import cv2
                encoded_ok, encoded = cv2.imencode(".jpg", frame)
                if not encoded_ok:
                    raise ValueError("preview_encoding_failed")
                payload = encoded.tobytes()
                atomic_write_bytes(preview_path, payload, preview_root)
                result["preview"] = {"path": str(preview_path.resolve()), "sha256": sha256(payload).hexdigest()}
            result["status"] = "frame_received"
            code = 0
        else:
            result["status"] = "unexpected_device_or_resolution"
        del frame
    except Exception:
        pass  # Never expose backend diagnostic text or any frame content.
    finally:
        if camera is not None:
            try:
                camera.release()
                result["helper_closed"] = camera.closed
            except Exception:
                result["status"] = "helper_cleanup_failed"
                code = 4
    report_path = report_path.absolute()
    atomic_write(report_path, json.dumps(result, ensure_ascii=False, indent=2), report_path.parent)
    return code


def _smoke_check() -> int:
    """Bounded diagnostic used by the delivery script, with no hardware I/O."""
    required = (
        "analysis-result.schema.json",
        "analysis-transport.schema.json",
        "report-narrative.schema.json",
    )
    schema_dir = files("qingzi_learning.schema")
    missing = [name for name in required if not (schema_dir / name).is_file()]
    if missing:
        print(f"打包检查失败：缺少 Schema：{', '.join(missing)}")
        return 2
    storage_resource = files("qingzi_learning.storage") / "schema.sql"
    if not storage_resource.is_file():
        print("打包检查失败：缺少资料库 Schema。")
        return 2
    # Prove the bundled SQL resource can initialize SQLite, but direct every
    # write to an automatically removed system-temp directory.  In particular,
    # this never creates the user's knowledge database or touches capture spool.
    with tempfile.TemporaryDirectory(prefix="qingzi-learning-smoke-") as directory:
        temporary_root = Path(directory)
        config = replace(
            load_config(),
            app_data_root=temporary_root,
            spool_root=temporary_root / "spool",
        )
        repository = KnowledgeRepository(config, temporary_root / "smoke.sqlite3")
        try:
            repository.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()
        finally:
            repository.close()
    try:
        resolve_codex_cli()
    except AnalysisError:
        print("Codex CLI 未找到：请先安装 Codex 并登录当前 ChatGPT 账号后再进行分析。")
        return 3
    print("打包检查通过：Schema 已包含，Codex CLI 可用；未打开摄像头，也未调用分析。")
    return 0


def main(argv: list[str] | None = None) -> int:
    multiprocessing.freeze_support()
    args = _parse_args(argv)
    if args.camera_smoke_check:
        return _camera_smoke_check(args.camera_smoke_check, args.camera_preview_output)
    if args.smoke_check:
        return _smoke_check()
    config = load_config()

    def controller_factory() -> WorkflowController:
        # Called inside WorkflowWorker.run, never in Tk's event thread.
        return WorkflowController(config, CodexCliAnalyzer(), KnowledgeRepository(config))

    def camera_factory():
        descriptor = find_document_camera(
            enumerate_document_cameras, config.camera_vid, config.camera_pid
        )
        return open_document_camera(descriptor)

    root = tk.Tk()
    # Production camera I/O runs in a killable spawn helper; the workflow worker
    # retains sole ownership of controller/repository/session state.
    worker = WorkflowWorker(config, controller_factory, camera_factory=camera_factory, isolated_camera=True)
    LearningAssistantApp(root, config, worker)
    root.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
