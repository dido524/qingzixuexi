"""Real capture/storage/service/export chain with only the remote process replaced."""
import json
from hashlib import sha256
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from qingzi_learning.analysis.codex_cli import CodexCliAnalyzer
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig
from qingzi_learning.export.publication import PublicationCoordinator
from qingzi_learning.review.service import ReviewService
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.workflow.controller import WorkflowController


class FixtureProcess:
    def __init__(self, payload):
        self.payload = payload
        self.fail = False
        self.calls = 0

    def run(self, args, **kwargs):
        self.calls += 1
        assert args.count("--image") == 2 and kwargs["shell"] is False
        assert args[args.index("--sandbox") + 1] == "read-only"
        if self.fail:
            return subprocess.CompletedProcess(args, 1, "", "synthetic network unavailable")
        path = Path(args[args.index("--output-last-message") + 1])
        path.write_text(json.dumps(self.payload, ensure_ascii=False), encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")


@pytest.fixture
def harness(tmp_path, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _: "C:/offline-fixture/codex.cmd")
    monkeypatch.setattr("qingzi_learning.analysis.codex_cli.resolve_codex_cli", lambda: "C:/offline-fixture/codex.cmd")
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 0xBC15, 0x2C1B,
                       tmp_path / "spool", tmp_path / "data")
    repo = KnowledgeRepository(config)
    yield config, repo
    repo.close()


def prepare(harness, name):
    config, repo = harness
    payload = json.loads((Path(__file__).parents[1] / "fixtures" / "analysis" / name).read_text("utf-8"))
    session = CaptureSession(config, payload["document_id"])
    rng = np.random.default_rng(19)
    for page in range(2):
        if page:
            session.next_page()
        session.capture(rng.integers(40, 220, (1200, 1600, 3), dtype=np.uint8))
    runner = FixtureProcess(payload)
    controller = WorkflowController(config, CodexCliAnalyzer(runner), repo)
    return session, runner, controller


@pytest.mark.parametrize("name,subject,point,state", [
    ("teacher_marked_chinese.json", "语文", "反义词", "completed"),
    ("unmarked_math.json", "数学", "同分母分数加法", "needs_review"),
    ("mixed_english.json", "英语", "第三人称单数", "needs_review"),
])
def test_subject_fixture_updates_correct_tree_and_preserves_evidence(harness, name, subject, point, state):
    config, repo = harness
    session, runner, controller = prepare(harness, name)
    original_hashes = {page.page_number: page.sha256 for page in session.pages}
    outcome = controller.finish_and_analyze(session)
    assert outcome.subject == subject and outcome.state == state
    assert outcome.child_document_ids == () and outcome.pending_page_subjects == ()
    assert repo.get_document(outcome.job_id)["subject"] == subject
    assert outcome.dashboard_path.exists() and subject in outcome.analysis_markdown.parts
    assert [path.name for path in outcome.archived_pages] == ["page_001.jpg", "page_002.jpg"]
    if name == "unmarked_math.json":
        assert len(outcome.annotated_pages) == 2 and all(path.exists() for path in outcome.annotated_pages)
        assert outcome.grading_gallery_path.exists()
        assert "模型建议，待家长确认" in outcome.grading_gallery_path.read_text("utf-8")
    for number, path in enumerate(outcome.archived_pages, start=1):
        assert sha256(path.read_bytes()).hexdigest() == original_hashes[number]
        with Image.open(path) as image:
            image.verify()
    expected_errors = 0 if name == "unmarked_math.json" else 1
    assert repo.get_knowledge_stats(subject, point).incorrect_count == expected_errors
    assert PublicationCoordinator(repo).current()
    if subject == "英语":
        stats = repo.get_knowledge_stats(subject, "英语表达")
        assert stats.exposure_count == 0 and stats.needs_review == 1
        review = ReviewService(repo)
        item = review.get_question(outcome.job_id, "2")
        review.confirm_question(outcome.job_id, "2", "correct", "I play.", "合成样例人工确认", expected_version=item.version)
        assert repo.get_knowledge_stats(subject, "英语表达").correct_count == 1
        assert PublicationCoordinator(repo).current()
    if name == "unmarked_math.json":
        review = ReviewService(repo)
        item = review.get_question(outcome.job_id, "2")
        assert item.annotated_path.exists()
        review.confirm_question(outcome.job_id, "2", "incorrect", "3/5", "家长确认模型批改",
                                expected_version=item.version)
    controller.retry_pending(outcome.job_id)
    controller.retry_pending(outcome.job_id)
    assert runner.calls == 1
    assert repo.get_knowledge_stats(subject, point).incorrect_count == 1
    assert repo.connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 1


def test_injected_network_failure_recovers_twice_without_duplicate_facts(harness):
    config, repo = harness
    session, runner, controller = prepare(harness, "unmarked_math.json")
    runner.fail = True
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_subject_confirmation"
    pending = controller.confirm_subject(outcome.job_id, "数学")
    assert pending.state == "pending"
    assert all("待处理" in path.parts and path.exists() for path in pending.archived_pages)
    runner.fail = False
    first = controller.retry_pending(outcome.job_id)
    second = controller.retry_pending(outcome.job_id)
    assert first == second and first.state == "needs_review"
    assert runner.calls == 2
    assert repo.get_knowledge_stats("数学", "同分母分数加法").exposure_count == 1
    item = ReviewService(repo).get_question(outcome.job_id, "2")
    controller.review.confirm_question(outcome.job_id, "2", "incorrect", "3/5", "家长确认",
                                       expected_version=item.version)
    assert repo.get_knowledge_stats("数学", "同分母分数加法").exposure_count == 2
    assert repo.connection.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    assert repo.connection.execute("SELECT count(*) FROM questions").fetchone()[0] == 2
