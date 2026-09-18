from hashlib import sha256
import json
from pathlib import Path
import subprocess

import pytest

from qingzi_learning.domain import CapturedDocument, CapturedPage


@pytest.fixture
def document(tmp_path):
    session = tmp_path / "capture session 中文"
    session.mkdir()
    pages = []
    for number in (1, 2):
        path = session / f"page_{number:03d}.jpg"
        content = f"durable page {number}".encode()
        path.write_bytes(content)
        pages.append(CapturedPage(number, path, sha256(content).hexdigest()))
    return CapturedDocument("capture-001", tuple(pages))


@pytest.fixture
def payload(document):
    return {
        "document_id": document.document_id,
        "subject": "语文",
        "subject_confidence": 0.96,
        "document_type": "作业",
        "source_exam_id": None,
        "grading_mode": "mixed",
        "teacher_mark_evidence": ["第1页老师打勾"],
        "page_subjects": [
            {
                "page": 1,
                "suggested_subject": "语文",
                "confidence": 0.98,
                "needs_confirmation": False,
                "reason": "词语填空题",
            },
            {
                "page": 2,
                "suggested_subject": "语文",
                "confidence": 0.96,
                "needs_confirmation": False,
                "reason": "作文题",
            },
        ],
        "questions": [
            {
                "question_id": "1",
                "source_exam_question_id": None,
                "question_type": "fill_blank",
                "page": 1,
                "prompt_summary": "词语填空",
                "student_answer": "春天",
                "reference_answer": "春天",
                "status": "correct",
                "decision_source": "teacher",
                "knowledge_points": ["词语"],
                "error_categories": [],
                "confidence": 0.96,
                "reason": "清晰的老师勾号",
                "answer_bbox": {"x": 0.12, "y": 0.28, "width": 0.34, "height": 0.09},
            },
            {
                "question_id": "2",
                "source_exam_question_id": None,
                "question_type": "composition",
                "page": 2,
                "prompt_summary": "作文：记一次旅行",
                "student_answer": "我去了公园。",
                "reference_answer": "开放答案",
                "status": "partial",
                "decision_source": "model",
                "knowledge_points": ["作文"],
                "error_categories": ["内容"],
                "confidence": 0.89,
                "reason": "内容不够具体",
                "answer_bbox": {"x": 0.08, "y": 0.20, "width": 0.84, "height": 0.62},
            },
        ],
        "summary": "作文内容有待确认。",
    }


class FakeRunner:
    """Only the process boundary is replaced; parsing and disk I/O remain real."""

    def __init__(self, payload, failure=None):
        self.payload = payload
        self.failure = failure
        self.calls = []

    def run(self, args, **kwargs):
        self.last_args = args
        self.last_kwargs = kwargs
        self.calls.append((args, kwargs))
        if self.failure == "timeout":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"], stderr="SECRET")
        if self.failure == "missing_cli":
            raise FileNotFoundError("SECRET")
        if self.failure in ("not_logged_in", "quota", "network"):
            return subprocess.CompletedProcess(args, 1, "", f"{self.failure}: SECRET")
        if self.failure != "missing_response":
            output = Path(args[args.index("--output-last-message") + 1])
            text = "invalid SECRET json" if self.failure == "invalid_json" else json.dumps(self.payload, ensure_ascii=False)
            output.write_text(text, encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "irrelevant progress logs SECRET", "")


@pytest.fixture
def runner(payload):
    return FakeRunner(payload)


@pytest.fixture(autouse=True)
def prevent_real_codex(monkeypatch, request):
    def forbidden(*args, **kwargs):
        pytest.fail("A test attempted to launch a real subprocess")

    monkeypatch.setattr(subprocess, "run", forbidden)
    monkeypatch.setattr("shutil.which", lambda name: "C:/Program Files/Codex/codex.cmd")
    # Discovery has dedicated process-boundary tests; analyzer tests never probe
    # real desktop binaries or the user's authentication state.
    def resolve_fixture():
        import shutil
        from qingzi_learning.analysis.codex_cli import AnalysisError
        path = shutil.which("codex.cmd")
        if not path:
            raise AnalysisError("cli_unavailable")
        return path
    if request.node.path.name != "test_cli_discovery.py":
        monkeypatch.setattr("qingzi_learning.analysis.codex_cli.resolve_codex_cli", resolve_fixture)
