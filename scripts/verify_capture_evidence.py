"""Inspect an existing ten-page camera acceptance run; never capture or invent one."""
import argparse
from hashlib import sha256
import json
from pathlib import Path
from PIL import Image


def verify(session: Path, archive: Path):
    state = json.loads((session / "session.json").read_text("utf-8"))
    assert state["finished"] and len(state["pages"]) == 10, "session must contain 10 finished pages"
    names = [f"page_{i:03d}.jpg" for i in range(1, 11)]
    assert sorted(path.name for path in archive.glob("page_*.jpg")) == names
    for page, name in zip(state["pages"], names, strict=True):
        path = archive / name
        assert page["path"] == name
        assert sha256(path.read_bytes()).hexdigest() == page["sha256"]
        with Image.open(path) as image:
            image.verify()
    audits = list((archive / "discarded").rglob("page_*.jpg")) + list((session / "discarded").rglob("page_*.jpg"))
    assert audits, "a retake audit original must exist"
    assert any(path.name != "page_010.jpg" for path in audits), "retake must include an earlier page"
    for path in audits:
        with Image.open(path) as image:
            image.verify()
    sidecar = json.loads((session / "analysis_state.json").read_text("utf-8"))
    assert sidecar["state"] in ("completed", "needs_review")
    return dict(pages=10, audit_copies=len(audits), state=sidecar["state"])


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("session", type=Path)
    parser.add_argument("archive", type=Path)
    args = parser.parse_args()
    print(json.dumps(verify(args.session, args.archive)))
