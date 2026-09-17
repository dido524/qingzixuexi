"""Explicit local acceptance probes; never run by pytest or normal app startup."""
from dataclasses import asdict, replace
import argparse
import base64
import json
from pathlib import Path
from uuid import uuid4
from hashlib import sha256

import cv2
from PIL import Image, ImageDraw, ImageFont

from qingzi_learning.analysis.codex_cli import CodexCliAnalyzer, SubprocessRunner
from qingzi_learning.camera.quality import check_image_quality
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import load_config
from qingzi_learning.export.safe_write import atomic_write
from qingzi_learning.export.publication import PublicationCoordinator
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.ui.camera_process import CameraProcess
from qingzi_learning.workflow.controller import WorkflowController


def preview():
    """Return an in-memory preview only; never persist a camera frame."""
    camera = CameraProcess(load_config(), timeout_seconds=15)
    try:
        _, frame = camera.read()
        quality = asdict(check_image_quality(frame))
        scaled = cv2.resize(frame, (min(1000, frame.shape[1]), int(frame.shape[0] * min(1000, frame.shape[1]) / frame.shape[1])))
        ok, encoded = cv2.imencode(".jpg", scaled)
        if not ok:
            raise RuntimeError("preview_encoding_failed")
        return dict(quality=quality, shape=list(frame.shape), image=base64.b64encode(encoded).decode("ascii"))
    finally:
        camera.release()


def synthetic(root: Path, live: bool):
    """Generate only invented exercises in a separate acceptance vault."""
    config = replace(load_config(), knowledge_root=root / "knowledge", spool_root=root / "spool", app_data_root=root / "data")
    content = [
        ("语文", "chinese", ["语文五年级：反义词练习", "1. 静的反义词：动", "2. 大的反义词：小"], True),
        ("数学", "math", ["数学五年级：同分母分数加法", "1. 3/4 + 1/4 = 1", "2. 2/5 + 1/5 = 3/10"], False),
        ("英语", "english", ["English Grade 5", "1. She __ a book. (have)  Answer: have", "2. I __ happy. (be)  Answer: am"], True),
    ]
    root.mkdir(parents=True, exist_ok=True)
    font = ImageFont.truetype(r"C:\Windows\Fonts\msyh.ttc", 44)
    repo = KnowledgeRepository(config)
    results = []
    try:
        for subject, slug, lines, marked in content:
            image = Image.new("RGB", (1600, 1200), (220, 220, 210))
            draw = ImageDraw.Draw(image)
            draw.rectangle((30, 30, 1570, 1170), outline="black", width=3)
            draw.text((70, 65), "合成验收页 / 非学生资料", font=font, fill="black")
            for i, line in enumerate(lines):
                y = 200 + i * 190
                draw.text((70, y), line, font=font, fill="black")
                if marked and i == 1:
                    x = 1460
                    if subject == "英语":
                        draw.line((x, y, x+45, y+45), fill="red", width=7)
                        draw.line((x+45, y, x, y+45), fill="red", width=7)
                        draw.text((500, y+70), "has", font=font, fill="red")
                    else:
                        draw.line((x, y+20, x+15, y+40, x+50, y-10), fill="red", width=7)
                if marked and subject == "语文" and i == 2:
                    draw.line((1460, y+20, 1475, y+40, 1510, y-10), fill="red", width=7)
            session = CaptureSession(config, f"acceptance-{slug}-{uuid4().hex[:8]}")
            page = session.capture(image)
            item = dict(subject=subject, source=str(page.path), quality=asdict(check_image_quality(image)))
            if live:
                outcome = WorkflowController(config, CodexCliAnalyzer(), repo).finish_and_analyze(session)
                item.update(state=outcome.state, actual_subject=outcome.subject, job_id=outcome.job_id,
                            markdown=str(outcome.analysis_markdown), dashboard=str(outcome.dashboard_path))
                item["stored_document"] = repo.get_document(outcome.job_id)
            results.append(item)
            atomic_write(root / "synthetic-results.json", json.dumps(results, ensure_ascii=False, indent=2, default=str), root)
    finally:
        repo.close()
    return results


def retry_diagnostic(root: Path):
    class DiagnosticRunner(SubprocessRunner):
        def run(self, args, **kwargs):
            completed = super().run(args, **kwargs)
            # Only fixed categorical hints leave the child-process boundary.
            log = completed.stderr.lower()
            hints = [label for marker, label in (
                ("requires a newer version", "outdated_cli"), ("invalid schema", "invalid_schema"),
                ("sandbox: read-only", "read_only"), ("not logged in", "authentication_required"),
                ("stream disconnected", "network_or_protocol_failure"),
            ) if marker in log]
            print(json.dumps(dict(returncode=completed.returncode, hints=hints), ensure_ascii=True), flush=True)
            return completed
    config = replace(load_config(), knowledge_root=root / "knowledge", spool_root=root / "spool", app_data_root=root / "data")
    repo = KnowledgeRepository(config)
    try:
        jobs = repo.list_workflow_jobs()
        math = next(job for job in jobs if "math" in job.job_id)
        controller = WorkflowController(config, CodexCliAnalyzer(DiagnosticRunner()), repo)
        if math.state == "needs_subject_confirmation":
            controller.confirm_subject(math.job_id, "数学")
        outcome = controller.retry_pending(math.job_id)
        return dict(state=outcome.state, job_id=outcome.job_id)
    finally:
        repo.close()


def verify_synthetic(root: Path):
    class NoRemote:
        def analyze(self, document):
            raise AssertionError("completed jobs must never call the model again")
    config = replace(load_config(), knowledge_root=root / "knowledge", spool_root=root / "spool", app_data_root=root / "data")
    records = json.loads((root / "synthetic-results.json").read_text("utf-8"))
    assert len(records) == 3
    repo = KnowledgeRepository(config)
    try:
        controller = WorkflowController(config, NoRemote(), repo)
        for record in records:
            assert record["state"] == "completed" and record["actual_subject"] == record["subject"]
            assert Path(record["markdown"]).exists() and Path(record["dashboard"]).exists()
            for page in record["stored_document"]["pages"]:
                path = Path(page["path"])
                assert sha256(path.read_bytes()).hexdigest() == page["sha256"]
                with Image.open(path) as image:
                    image.verify()
            controller.retry_pending(record["job_id"])
            controller.retry_pending(record["job_id"])
        assert repo.connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 3
        assert repo.connection.execute("SELECT count(*) FROM questions").fetchone()[0] == 6
        assert PublicationCoordinator(repo).current()
        return dict(documents=3, questions=6, repeated_retries=6, extra_remote_calls=0, files_current=True)
    finally:
        repo.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["preview", "synthetic", "retry-diagnostic", "verify-synthetic"])
    parser.add_argument("--root", type=Path)
    parser.add_argument("--live", action="store_true", help="Explicitly call authenticated Codex for the three synthetic pages")
    args = parser.parse_args()
    if args.mode != "preview" and not args.root:
        parser.error("requires a separate --root")
    if args.root:
        args.root = args.root.resolve()
    result = preview() if args.mode == "preview" else retry_diagnostic(args.root) if args.mode == "retry-diagnostic" else verify_synthetic(args.root) if args.mode == "verify-synthetic" else synthetic(args.root, args.live)
    print(json.dumps(result, ensure_ascii=True, default=str))


if __name__ == "__main__":
    main()
