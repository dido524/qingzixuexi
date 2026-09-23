from dataclasses import asdict, replace
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import subprocess

import numpy as np
import pytest
from PIL import Image

from qingzi_learning.analysis.codex_cli import AnalysisError
from qingzi_learning.capture.session import CaptureSession
from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult, GradingMode, PageSubjectAssignment, QuestionAnalysis,
    QuestionStatus, QuestionType, Subject,
)
from qingzi_learning.grading.annotation import ANNOTATION_LAYOUT_VERSION
from qingzi_learning.storage.repository import KnowledgeRepository


class FakeAnalyzer:
    def __init__(self):
        self.calls = 0
        self.error = None
        self.confidence = 0.97
        self.status = QuestionStatus.INCORRECT

    def analyze(self, document):
        self.calls += 1
        assert all(sha256(p.path.read_bytes()).hexdigest() == p.sha256 for p in document.pages)
        if self.error:
            raise self.error
        return AnalysisResult(
            document.document_id, Subject.MATH, self.confidence, "试卷",
            GradingMode.AUTO_GRADE, (),
            (QuestionAnalysis("1", QuestionType.CALCULATION, 1, "3/4 + 1/4", "3/4", "1",
                              self.status, "model", ("分数加法",), ("计算",), 0.95, "分子相加"),),
            "复习分数加法",
            (PageSubjectAssignment(1, Subject.MATH, self.confidence,
                                   self.confidence < 0.85, "分数计算题"),),
        )


class MixedPageAnalyzer:
    def __init__(self):
        self.calls = 0

    def analyze(self, document):
        self.calls += 1
        assert all(sha256(page.path.read_bytes()).hexdigest() == page.sha256 for page in document.pages)
        return AnalysisResult(
            document.document_id, Subject.MATH, 0.97, "试卷", GradingMode.AUTO_GRADE, (),
            (QuestionAnalysis("1", QuestionType.CALCULATION, 1, "1 + 1", "2", "2",
                              QuestionStatus.CORRECT, "model", (), (), 0.95, "正确"),),
            "混合科目资料",
            (
                PageSubjectAssignment(1, Subject.MATH, 0.97, False, "计算题"),
                PageSubjectAssignment(2, Subject.CHINESE, 0.50, True, "题材不清晰"),
                PageSubjectAssignment(3, Subject.MATH, 0.97, False, "计算题"),
                PageSubjectAssignment(4, Subject.ENGLISH, 0.40, True, "题材不清晰"),
            ),
        )


@pytest.fixture
def setup(tmp_path):
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    repo = KnowledgeRepository(config)
    analyzer = FakeAnalyzer()
    session = CaptureSession(config, "capture-test")
    rng = np.random.default_rng(10)
    session.capture(rng.integers(40, 220, (1200, 1600, 3), dtype=np.uint8))
    yield config, repo, analyzer, session
    repo.close()


@pytest.fixture
def mixed_setup(tmp_path):
    config = AppConfig(tmp_path / "knowledge", ("语文", "数学", "英语"), 1, 2,
                       tmp_path / "spool", tmp_path / "data")
    repo = KnowledgeRepository(config)
    analyzer = MixedPageAnalyzer()
    session = CaptureSession(config, "mixed-pages")
    rng = np.random.default_rng(11)
    for page in range(4):
        session.capture(rng.integers(40, 220, (1200, 1600, 3), dtype=np.uint8))
        if page < 3:
            session.next_page()
    yield config, repo, analyzer, session
    repo.close()


def controller_for(setup):
    from qingzi_learning.workflow.controller import WorkflowController
    config, repo, analyzer, _ = setup
    return WorkflowController(config, analyzer, repo,
                              now=lambda: datetime(2026, 9, 15, tzinfo=timezone.utc))


def test_opening_review_migrates_legacy_annotation_to_non_covering_layout(setup):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyze = analyzer.analyze
    analyzer.analyze = lambda document: replace(
        analyze(document), questions=tuple(
            replace(question, answer_bbox=(.2, .3, .35, .08))
            for question in analyze(document).questions
        ),
    )
    outcome = controller.finish_and_analyze(session)
    job = repo.get_job(outcome.job_id)
    source = Path(job.payload["pages"][0]["path"])
    annotated = Path(job.payload["annotated_pages"][0])
    with Image.open(source) as old_image:
        old_image.convert("RGB").save(annotated, format="PNG")
        old_width = old_image.width
    legacy_payload = dict(job.payload)
    legacy_payload.pop("annotation_layout_version", None)
    repo.save_workflow_job(replace(job, payload=legacy_payload))

    controller.refresh_review_annotations((outcome.job_id,))

    migrated = repo.get_job(outcome.job_id)
    assert migrated.payload["annotation_layout_version"] == ANNOTATION_LAYOUT_VERSION
    with Image.open(annotated) as result:
        assert result.width > old_width


def test_unclear_page_pauses_before_any_document_is_written(mixed_setup):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_subject_confirmation"
    assert [item.page for item in outcome.pending_page_subjects] == [2]
    assert repo.get_document(outcome.job_id) is None


def test_page_confirmation_is_persisted_before_next_page(mixed_setup):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    first = controller.finish_and_analyze(session)
    second = controller.confirm_page_subject(first.job_id, 2, "数学")
    saved = repo.get_job(first.job_id)
    assert saved.payload["page_subject_overrides"] == {"2": "数学"}
    assert [item.page for item in second.pending_page_subjects] == [4]


def test_restarted_controller_uses_cached_analysis_and_remaining_confirmation(mixed_setup):
    controller = controller_for(mixed_setup)
    _, _, analyzer, session = mixed_setup
    first = controller.finish_and_analyze(session)
    controller.confirm_page_subject(first.job_id, 2, "数学")
    recovered = {item.job_id: item for item in controller_for(mixed_setup).recover_jobs()}[first.job_id]
    assert [item.page for item in recovered.pending_page_subjects] == [4]
    assert analyzer.calls == 1


@pytest.mark.parametrize("boundary", ["before_override", "after_override"])
def test_stale_page_confirmation_returns_newer_completed_batch(mixed_setup, monkeypatch, boundary):
    controller = controller_for(mixed_setup)
    config, repo, analyzer, session = mixed_setup
    first = controller.finish_and_analyze(session)
    controller.confirm_page_subject(first.job_id, 2, "数学")
    newer_repo = KnowledgeRepository(config)
    newer = controller_for((config, newer_repo, analyzer, session))
    original = controller._page_split_plan
    calls = 0
    completed_jobs = None

    def complete_from_other_connection(job):
        nonlocal calls, completed_jobs
        plan = original(job)
        calls += 1
        if calls == (1 if boundary == "before_override" else 2):
            result = (newer.confirm_page_subject(job.job_id, 4, "语文")
                      if boundary == "before_override" else newer.retry_pending(job.job_id))
            assert result.state == "completed"
            completed_jobs = newer_repo.list_workflow_jobs()
        return plan

    monkeypatch.setattr(controller, "_page_split_plan", complete_from_other_connection)
    try:
        result = controller.confirm_page_subject(first.job_id, 4, "英语")
        assert result.state == "completed"
        assert repo.list_workflow_jobs() == completed_jobs
        parent = repo.get_job(first.job_id)
        assert parent.knowledge_applied
        assert result.child_document_ids == tuple(parent.payload["child_document_ids"])
        assert len(result.child_document_ids) == 2
        assert repo.get_document(parent.job_id) is None
        assert {row[0] for row in repo.connection.execute("SELECT document_id FROM documents")} == set(result.child_document_ids)
        assert repo.count_questions("mixed-pages--math") == 1
        assert json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))["state"] == "completed"
    finally:
        newer_repo.close()


def test_page_confirmation_merges_other_connection_pending_choice(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    config, repo, analyzer, session = mixed_setup
    first = controller.finish_and_analyze(session)
    newer_repo = KnowledgeRepository(config)
    newer = controller_for((config, newer_repo, analyzer, session))
    original = controller._page_split_plan
    advanced = False

    def choose_other_page(job):
        nonlocal advanced
        plan = original(job)
        if not advanced:
            advanced = True
            newer.confirm_page_subject(job.job_id, 4, "英语")
        return plan

    monkeypatch.setattr(controller, "_page_split_plan", choose_other_page)
    try:
        result = controller.confirm_page_subject(first.job_id, 2, "数学")
        assert result.state == "completed"
        assert repo.get_job(first.job_id).payload["page_subject_overrides"] == {"2": "数学", "4": "英语"}
        assert result.child_document_ids == ("mixed-pages--math", "mixed-pages--english")
    finally:
        newer_repo.close()


def test_page_confirmation_job_rejects_legacy_batch_confirmation(mixed_setup):
    controller = controller_for(mixed_setup)
    _, _, _, session = mixed_setup
    outcome = controller.finish_and_analyze(session)
    with pytest.raises(ValueError, match="页面科目确认"):
        controller.confirm_subject(outcome.job_id, "数学")


def test_legacy_cached_analysis_is_marked_and_rejects_page_confirmation(setup):
    controller = controller_for(setup)
    _, _, analyzer, session = setup
    document = session.finish()
    staged = controller._stage(session)
    legacy_analysis = asdict(analyzer.analyze(document))
    legacy_analysis.pop("page_subjects")
    analyzing = replace(staged, state="analyzing")
    controller.repo.save_workflow_job(analyzing)
    controller.repo.save_workflow_job(replace(
        analyzing, state="needs_subject_confirmation",
        payload=dict(staged.payload, analysis=legacy_analysis),
    ))
    outcome = {item.job_id: item for item in controller_for(setup).recover_jobs()}[staged.job_id]
    assert controller.repo.get_job(staged.job_id).payload["confirmation_mode"] == "legacy_batch"
    with pytest.raises(ValueError, match="页面科目确认"):
        controller.confirm_page_subject(outcome.job_id, 1, "数学")


def test_completed_mixed_split_publishes_without_parent_document(mixed_setup):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    first = controller.finish_and_analyze(session)
    second = controller.confirm_page_subject(first.job_id, 2, "数学")
    final = controller.confirm_page_subject(second.job_id, 4, "英语")
    saved = repo.get_job(final.job_id)
    assert final.state == "completed" and final.subject is None
    assert saved.payload["split_plan"]["groups"] == [
        {"subject": "数学", "document_id": "mixed-pages--math", "page_numbers": [1, 2, 3]},
        {"subject": "英语", "document_id": "mixed-pages--english", "page_numbers": [4]},
    ]
    assert all("image_path" not in item for item in saved.payload["split_plan"]["unresolved_pages"])
    assert repo.get_document(final.job_id) is None


def _confirm_mixed(controller, session):
    first = controller.finish_and_analyze(session)
    controller.confirm_page_subject(first.job_id, 2, "数学")
    return controller.confirm_page_subject(first.job_id, 4, "英语")


@pytest.mark.parametrize("retry_child", [False, True])
def test_split_publication_failure_preserves_sources_and_retries_stable_batch(mixed_setup, monkeypatch, retry_child):
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    original = controller.dashboard.export
    def fail():
        raise OSError("publication unavailable")
    monkeypatch.setattr(controller.dashboard, "export", fail)
    first = _confirm_mixed(controller, session)
    assert first.state == "pending" and first.error_code == "workflow_failed"
    child_ids = first.child_document_ids
    assert child_ids == ("mixed-pages--math", "mixed-pages--english")
    assert all(Path(a["source"]).exists() for a in repo.get_job(first.job_id).payload["assets"])
    assert all(repo.get_job(c).payload["export_pending"] for c in child_ids)
    monkeypatch.setattr(controller.dashboard, "export", original)
    second = controller.retry_pending(child_ids[0] if retry_child else first.job_id)
    assert second.state == "completed" and second.error_code is None
    assert second.job_id == (child_ids[0] if retry_child else first.job_id)
    parent = repo.get_job(first.job_id)
    assert tuple(parent.payload["child_document_ids"]) == child_ids
    assert parent.payload["publish_state"] == "completed" and not parent.payload["export_pending"]
    assert repo.count_questions(child_ids[0]) == 1 and analyzer.calls == 1
    assert repo.connection.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 2
    assert controller.publication.current()
    for child_id in child_ids:
        child = repo.get_job(child_id)
        assert Path(child.payload["analysis_markdown"]).exists()
        assert child.payload["publish_state"] == "completed" and not child.payload["export_pending"]
        assert repo.connection.execute("SELECT state FROM processing_jobs WHERE document_id=?", (child_id,)).fetchone()[0] == child.state
    assert not any(p.path.exists() for p in session.pages)
    assert (session.session_dir / "session.json").exists()


@pytest.mark.parametrize("status, terminal", [(QuestionStatus.CORRECT, "completed"), (QuestionStatus.NEEDS_REVIEW, "needs_review")])
def test_split_dashboard_renders_terminal_batch_and_remains_current(mixed_setup, monkeypatch, status, terminal):
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    analyze = analyzer.analyze
    def with_review(document):
        result = analyze(document)
        return replace(result, questions=(replace(result.questions[0], status=status),))
    monkeypatch.setattr(analyzer, "analyze", with_review)
    render = controller.dashboard.export
    observed = []
    def inspect():
        observed.append([repo.get_job(c).state for c in ("mixed-pages--math", "mixed-pages--english")])
        assert repo.get_job("mixed-pages").state == terminal
        assert all(p.path.exists() for p in session.pages)
        return render()
    monkeypatch.setattr(controller.dashboard, "export", inspect)
    result = _confirm_mixed(controller, session)
    assert result.state == terminal
    assert observed == [[terminal, "completed"]]
    assert controller.publication.current()


@pytest.mark.parametrize("crash", [False, True])
def test_split_cleanup_resumes_after_unlink_without_republication(mixed_setup, monkeypatch, crash):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    unknown = session.session_dir / "unassigned.jpg"
    unknown.write_bytes(b"keep me")
    source = session.pages[0].path
    unlink = Path.unlink
    def interrupt(path, *args, **kwargs):
        result = unlink(path, *args, **kwargs)
        if path == source:
            assert controller.publication.current()
            assert repo.get_job("mixed-pages").payload["cleanup"]
            raise SimulatedCrash() if crash else OSError("cleanup interrupted")
        return result
    monkeypatch.setattr(Path, "unlink", interrupt)
    if crash:
        with pytest.raises(SimulatedCrash):
            _confirm_mixed(controller, session)
    else:
        result = _confirm_mixed(controller, session)
        assert result.error_code == "cleanup_failed"
    generation = repo.connection.execute("SELECT generation FROM reading_publication").fetchone()[0]
    monkeypatch.setattr(Path, "unlink", unlink)
    restarted = controller_for(mixed_setup)
    assert "mixed-pages" in {item.job_id for item in restarted.recover_jobs()}
    result = restarted.retry_pending("mixed-pages")
    assert result.state == "completed" and result.error_code is None
    assert not repo.get_job(result.job_id).payload["cleanup"]
    assert not any(p.path.exists() for p in session.pages)
    assert unknown.read_bytes() == b"keep me" and (session.session_dir / "session.json").exists()
    assert repo.connection.execute("SELECT generation FROM reading_publication").fetchone()[0] == generation
    assert restarted.publication.current()


@pytest.mark.parametrize("phase", ["pending", "completed"])
def test_split_publication_mirror_failure_rolls_back_whole_candidate_or_finalization(mixed_setup, monkeypatch, phase):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    mirror = controller._mirror
    failed = False
    def interrupt(job):
        nonlocal failed
        if job.job_id == "mixed-pages--english" and job.payload.get("publish_state") == phase and not failed:
            failed = True
            raise OSError("mirror unavailable")
        return mirror(job)
    monkeypatch.setattr(controller, "_mirror", interrupt)
    outcome = _confirm_mixed(controller, session)
    assert failed and outcome.error_code == "workflow_failed" and outcome.state == "pending"
    assert all(p.path.exists() for p in session.pages)
    assert all(repo.get_job(c).payload["export_pending"] for c in outcome.child_document_ids)
    assert controller.retry_pending(outcome.job_id).state == "completed"
    assert controller.publication.current()


@pytest.mark.parametrize("damage", ["image", "manifest"])
def test_split_cleanup_revalidates_child_evidence_after_successful_publication(mixed_setup, monkeypatch, damage):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    publish = controller.publication.publish
    damaged = []
    def corrupt_after_publish(*args, **kwargs):
        result = publish(*args, **kwargs)
        assert result
        page = Path(repo.get_job("mixed-pages--math").payload["pages"][0]["path"])
        target = page if damage == "image" else page.parent / "split-manifest.json"
        damaged.append((target, target.read_bytes()))
        target.write_bytes(b"damaged evidence")
        return result
    monkeypatch.setattr(controller.publication, "publish", corrupt_after_publish)
    outcome = _confirm_mixed(controller, session)
    assert outcome.state == "completed" and outcome.error_code == "cleanup_failed"
    assert all(p.path.exists() for p in session.pages)
    generation = repo.connection.execute("SELECT generation FROM reading_publication").fetchone()[0]
    target, content = damaged[0]
    target.write_bytes(content)
    monkeypatch.setattr(controller.publication, "publish", publish)
    assert controller.retry_pending(outcome.job_id).error_code is None
    assert not any(p.path.exists() for p in session.pages)
    assert repo.connection.execute("SELECT generation FROM reading_publication").fetchone()[0] == generation


def test_split_recovery_finds_crash_after_last_cleanup_checkpoint(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    save = controller._save_if_current
    def interrupt(expected, job):
        result = save(expected, job)
        if (job.job_id == "mixed-pages" and expected.payload.get("cleanup")
                and not job.payload.get("cleanup")):
            raise SimulatedCrash()
        return result
    monkeypatch.setattr(controller, "_save_if_current", interrupt)
    with pytest.raises(SimulatedCrash):
        _confirm_mixed(controller, session)
    assert not any(p.path.exists() for p in session.pages)
    restarted = controller_for(mixed_setup)
    assert "mixed-pages" in {item.job_id for item in restarted.recover_jobs()}
    assert restarted.retry_pending("mixed-pages").state == "completed"
    assert repo.get_job("mixed-pages").payload["cleanup_completed"]


def test_split_stages_verified_pages_and_audit_before_one_batch(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    config, repo, analyzer, session = mixed_setup
    calls = []
    original = getattr(controller.updater, "apply_split_batch", None)

    def batch(parent, children, *, now):
        calls.append(parent.job_id)
        assert all(p.path.exists() for p in session.pages)
        for child, document, analysis in children:
            assert all(sha256(p.path.read_bytes()).hexdigest() == p.sha256 for p in document.pages)
            assert (document.session_dir / "split-manifest.json").exists()
            assert repo.get_document(child.job_id) is None
        return original(parent, children, now=now)

    monkeypatch.setattr(controller.updater, "apply_split_batch", batch, raising=False)
    outcome = _confirm_mixed(controller, session)
    assert calls == ["mixed-pages"]
    assert outcome.child_document_ids == ("mixed-pages--math", "mixed-pages--english")
    assert not any(p.path.exists() for p in session.pages)
    assert repo.get_document(outcome.job_id) is None
    for child_id, pages in (("mixed-pages--math", [1, 2, 3]), ("mixed-pages--english", [4])):
        child = repo.get_job(child_id)
        assert child.knowledge_applied and child.state == "completed"
        assert child.payload["parent_job_id"] == outcome.job_id
        assert child.payload["archive_kind"] == "raw"
        assert child.payload["publish_state"] == "completed" and not child.payload["export_pending"]
        stored = repo.get_document(child_id)
        assert [p["page_number"] for p in stored["pages"]] == pages
        directory = config.knowledge_root / child.subject / "原始资料/2026/09" / child_id
        assert all(Path(p["path"]) == directory / f"page_{p['page_number']:03d}.jpg" for p in stored["pages"])
        manifest = json.loads((directory / "split-manifest.json").read_text("utf-8"))
        assert manifest["parent_document_id"] == outcome.job_id
        assert manifest["page_subject_overrides"] == {"2": "数学", "4": "英语"}
        assert manifest["resolved_pages"][1] == {"page": 2, "subject": "数学", "source": "human", "confidence": 1.0}
        assert manifest["model_page_subjects"][1]["suggested_subject"] == "语文"
        assert manifest["model_page_subjects"][1]["reason"] == "题材不清晰"
        assert manifest["source_pages"][3] == {"page": 4, "filename": "page_004.jpg", "sha256": session.pages[3].sha256}
        assert manifest["groups"][1]["page_numbers"] == [4]
        assert json.loads(Path(child.payload["mirror_path"]).read_text("utf-8"))["document_id"] == child_id
    assert json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))["document_id"] == outcome.job_id
    before = repo.dashboard_snapshot()
    controller.retry_pending(outcome.job_id)
    controller.retry_pending("mixed-pages--math")
    assert repo.dashboard_snapshot() == before
    assert calls == ["mixed-pages"] and analyzer.calls == 1


def test_split_collision_preserves_sources_and_commits_no_children(mixed_setup):
    controller = controller_for(mixed_setup)
    config, repo, _, session = mixed_setup
    target = config.knowledge_root / "英语/原始资料/2026/09/mixed-pages--english/page_004.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"unrelated bytes")
    outcome = _confirm_mixed(controller, session)
    assert outcome.error_code == "workflow_failed"
    assert not repo.get_job(outcome.job_id).knowledge_applied
    assert repo.get_document("mixed-pages--math") is None
    assert repo.get_document("mixed-pages--english") is None
    assert target.read_bytes() == b"unrelated bytes"
    assert all(p.path.exists() for p in session.pages)


def test_split_manifest_collision_preserves_bytes_and_commits_no_children(mixed_setup):
    controller = controller_for(mixed_setup)
    config, repo, _, session = mixed_setup
    manifest = config.knowledge_root / "英语/原始资料/2026/09/mixed-pages--english/split-manifest.json"
    manifest.parent.mkdir(parents=True)
    conflicting = b'{"parent_document_id":"another-parent"}'
    manifest.write_bytes(conflicting)
    outcome = _confirm_mixed(controller, session)
    assert outcome.error_code == "workflow_failed"
    assert manifest.read_bytes() == conflicting
    assert not repo.get_job(outcome.job_id).knowledge_applied
    assert repo.get_document("mixed-pages--math") is None
    assert repo.get_document("mixed-pages--english") is None
    assert all(sha256(p.path.read_bytes()).hexdigest() == p.sha256 for p in session.pages)


@pytest.mark.parametrize("retry_id", ["mixed-pages", "mixed-pages--math"])
@pytest.mark.parametrize("damage", ["missing", "modified"])
def test_split_retry_validates_all_audit_manifests_from_parent(mixed_setup, retry_id, damage):
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    _confirm_mixed(controller, session)
    english = repo.get_job("mixed-pages--english")
    manifest = Path(english.payload["pages"][0]["path"]).parent / "split-manifest.json"
    canonical = manifest.read_bytes()
    before = repo.dashboard_snapshot()
    if damage == "missing":
        manifest.unlink()
    else:
        altered = json.loads(canonical)
        altered["page_subject_overrides"]["4"] = "数学"
        manifest.write_text(json.dumps(altered, ensure_ascii=False), encoding="utf-8")
        conflicting = manifest.read_bytes()
    outcome = controller_for(mixed_setup).retry_pending(retry_id)
    if damage == "missing":
        assert outcome.error_code is None
        assert manifest.read_bytes() == canonical
    else:
        assert outcome.error_code == "workflow_failed"
        assert manifest.read_bytes() == conflicting
    assert repo.dashboard_snapshot() == before
    assert not any(p.path.exists() for p in session.pages)
    assert repo.get_job("mixed-pages").knowledge_applied
    assert analyzer.calls == 1
    if damage == "modified":
        manifest.write_bytes(canonical)
        assert controller_for(mixed_setup).retry_pending(retry_id).state == "completed"


def test_split_database_failure_retries_staged_files_without_model_call(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    original = repo._save_analysis
    calls = 0

    def fail_second(analysis):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected")
        return original(analysis)

    monkeypatch.setattr(repo, "_save_analysis", fail_second)
    outcome = _confirm_mixed(controller, session)
    assert outcome.error_code == "workflow_failed"
    assert repo.get_document("mixed-pages--math") is None
    assert all(p.path.exists() for p in session.pages)
    monkeypatch.setattr(repo, "_save_analysis", original)
    retried = controller_for(mixed_setup).retry_pending(outcome.job_id)
    assert retried.child_document_ids == ("mixed-pages--math", "mixed-pages--english")
    assert repo.count_questions("mixed-pages--math") == 1
    assert analyzer.calls == 1


@pytest.mark.parametrize("boundary", ["manifest", "child_mirror"])
def test_split_recovers_file_write_failure_without_reapplying_facts(mixed_setup, monkeypatch, boundary):
    from qingzi_learning.workflow import controller as module
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    original = module.atomic_write
    failed = False

    def fail_once(destination, content, root):
        nonlocal failed
        target = (destination.name == "split-manifest.json" if boundary == "manifest"
                  else destination.parent.name == "mixed-pages--english"
                  and destination.name == "analysis_state.json")
        if target and not failed:
            failed = True
            raise OSError("disk unavailable")
        return original(destination, content, root)

    monkeypatch.setattr(module, "atomic_write", fail_once)
    outcome = _confirm_mixed(controller, session)
    assert failed and outcome.error_code == "workflow_failed"
    assert repo.get_job(outcome.job_id).knowledge_applied == (boundary == "child_mirror")
    assert all(p.path.exists() for p in session.pages)
    retried = controller_for(mixed_setup).retry_pending(outcome.job_id)
    assert retried.error_code is None
    assert repo.count_questions("mixed-pages--math") == 1
    assert repo.get_job(outcome.job_id).payload["publish_state"] == "completed"
    assert analyzer.calls == 1


def test_split_child_mirror_cannot_target_parent_spool(mixed_setup):
    controller = controller_for(mixed_setup)
    _, repo, _, session = mixed_setup
    _confirm_mixed(controller, session)
    child = repo.get_job("mixed-pages--math")
    mirror = session.session_dir / "analysis_state.json"
    before = mirror.read_bytes()
    with pytest.raises(ValueError):
        controller._mirror(replace(child, payload=dict(child.payload, mirror_path=str(mirror))))
    assert mirror.read_bytes() == before


def test_split_retry_detects_damaged_committed_child_without_overwriting(mixed_setup):
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    outcome = _confirm_mixed(controller, session)
    child = repo.get_job("mixed-pages--english")
    page = Path(child.payload["pages"][0]["path"])
    page.write_bytes(b"changed archive")
    retried = controller.retry_pending(outcome.job_id)
    assert retried.error_code == "workflow_failed"
    assert page.read_bytes() == b"changed archive"
    assert not any(p.path.exists() for p in session.pages)
    assert repo.get_job(outcome.job_id).knowledge_applied
    assert analyzer.calls == 1


def test_recovery_repairs_final_page_override_interrupted_before_state_transition(
        mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    _, repo, analyzer, session = mixed_setup
    first = controller.finish_and_analyze(session)
    second = controller.confirm_page_subject(first.job_id, 2, "数学")
    original = controller._page_split_plan

    def crash_after_final_override(job):
        plan = original(job)
        if job.payload["page_subject_overrides"].get("4") == "英语":
            raise SimulatedCrash()
        return plan

    monkeypatch.setattr(controller, "_page_split_plan", crash_after_final_override)
    with pytest.raises(SimulatedCrash):
        controller.confirm_page_subject(second.job_id, 4, "英语")
    saved = repo.get_job(first.job_id)
    assert saved.state == "needs_subject_confirmation"
    assert saved.payload["page_subject_overrides"] == {"2": "数学", "4": "英语"}

    monkeypatch.setattr(controller, "_page_split_plan", original)
    recovered = controller_for(mixed_setup).retry_pending(first.job_id)
    assert recovered.state == "completed" and recovered.subject is None
    assert analyzer.calls == 1
    assert repo.get_document(first.job_id) is None


def test_finish_archives_and_updates_all_read_models(setup):
    controller = controller_for(setup)
    _, repo, _, session = setup
    original = session.pages[0].path
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "completed"
    assert outcome.subject == "数学"
    assert outcome.analysis_markdown.exists() and outcome.dashboard_path.exists()
    assert outcome.archived_pages[0].parts[-5:] == ("原始资料", "2026", "09", "capture-test", "page_001.jpg")
    assert not original.exists()
    assert repo.get_knowledge_stats("数学", "分数加法").incorrect_count == 1
    assert repo.get_job(outcome.job_id).knowledge_applied is True
    assert repo.get_document(outcome.job_id)["pages"][0]["path"] == str(outcome.archived_pages[0])


def test_duplicate_finish_and_retry_never_double_count(setup):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    first = controller.finish_and_analyze(session)
    second = controller.finish_and_analyze(session)
    third = controller.retry_pending(first.job_id)
    assert first == second == third
    assert analyzer.calls == 1
    assert repo.get_knowledge_stats("数学", "分数加法").exposure_count == 1


def test_low_confidence_requires_choice_before_any_subject_archive(setup):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    analyzer.confidence = 0.4
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_subject_confirmation" and outcome.subject is None
    assert not repo.get_job(outcome.job_id).knowledge_applied
    assert not list(config.knowledge_root.rglob("*.jpg"))
    assert session.pages[0].path.exists()
    assert [item.page for item in outcome.pending_page_subjects] == [1]
    confirmed = controller.confirm_page_subject(outcome.job_id, 1, "数学")
    assert confirmed.state == "completed" and analyzer.calls == 1


def test_failed_analysis_subject_choice_archives_pending_then_retry_recovers(setup):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    analyzer.error = AnalysisError("timeout")
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_subject_confirmation" and outcome.subject is None
    assert session.pages[0].path.exists()
    pending = controller.confirm_subject(outcome.job_id, "数学")
    assert pending.state == "pending"
    assert "待处理" in pending.archived_pages[0].parts
    assert pending.archived_pages[0].exists()
    analyzer.error = None
    completed = controller_for(setup).retry_pending(outcome.job_id)
    assert completed.state == "completed"
    assert "原始资料" in completed.archived_pages[0].parts
    assert not pending.archived_pages[0].exists()
    assert repo.get_knowledge_stats("数学", "分数加法").incorrect_count == 1
    sidecar = json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))
    assert sidecar["state"] == "completed" and sidecar["error_code"] is None


def _failed_mixed_archive(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    _, _, analyzer, session = mixed_setup

    def timeout(_document):
        analyzer.calls += 1
        raise AnalysisError("timeout")

    with monkeypatch.context() as patch:
        patch.setattr(analyzer, "analyze", timeout)
        first = controller.finish_and_analyze(session)
    archived = controller.confirm_subject(first.job_id, "数学")
    assert archived.state == "pending"
    assert all(p.exists() and "待处理" in p.parts for p in archived.archived_pages)
    assert not any(p.path.exists() for p in session.pages)
    return controller, archived


@pytest.mark.parametrize("interrupt_commit", [False, True])
def test_failed_pending_archive_can_become_atomic_mixed_batch(mixed_setup, monkeypatch, interrupt_commit):
    controller, archived = _failed_mixed_archive(mixed_setup, monkeypatch)
    _, repo, analyzer, _ = mixed_setup
    original_document = repo.get_document(archived.job_id)
    retried = controller.retry_pending(archived.job_id)
    assert retried.state == "needs_subject_confirmation"
    controller.confirm_page_subject(archived.job_id, 2, "数学")
    original_save = repo._save_analysis

    def fail_second_child(analysis):
        if analysis.document_id.endswith("--english"):
            raise RuntimeError("interrupted split transaction")
        original_save(analysis)

    with monkeypatch.context() as patch:
        if interrupt_commit:
            patch.setattr(repo, "_save_analysis", fail_second_child)
        result = controller.confirm_page_subject(archived.job_id, 4, "英语")
    if interrupt_commit:
        assert result.state == "pending" and result.error_code == "workflow_failed"
        assert repo.get_document(archived.job_id) == original_document
        assert repo.get_document("mixed-pages--math") is None
        assert repo.get_document("mixed-pages--english") is None
        assert all(p.exists() for p in archived.archived_pages)
        result = controller.retry_pending(archived.job_id)
    assert result.state == "completed"
    assert result.child_document_ids == ("mixed-pages--math", "mixed-pages--english")
    assert repo.get_document(archived.job_id) is None
    assert repo.connection.execute("SELECT COUNT(*) FROM processing_jobs WHERE document_id=?", (archived.job_id,)).fetchone()[0] == 0
    assert repo.count_questions("mixed-pages--math") == 1
    assert repo.connection.execute("PRAGMA foreign_key_check").fetchall() == []
    assert analyzer.calls == 2
    assert controller.retry_pending(archived.job_id).state == "completed"


@pytest.mark.parametrize("conflict", ["facts", "page_manifest", "applied", "review_audit"])
def test_mixed_retry_preserves_conflicting_failed_parent_archive(mixed_setup, monkeypatch, conflict):
    controller, archived = _failed_mixed_archive(mixed_setup, monkeypatch)
    _, repo, analyzer, _ = mixed_setup
    controller.retry_pending(archived.job_id)
    controller.confirm_page_subject(archived.job_id, 2, "数学")
    if conflict == "facts":
        repo.save_analysis(analyzer.analyze(controller._document(repo.get_job(archived.job_id))))
    elif conflict == "applied":
        original_apply = controller.updater.apply_split_batch

        def conflicting_apply(parent, children, *, now):
            # Set the conflict at the transaction boundary, after the ordinary
            # workflow saves have synchronized the processing-job mirror.
            with repo.connection:
                repo.connection.execute("UPDATE processing_jobs SET knowledge_applied=1 WHERE document_id=?", (parent.job_id,))
            return original_apply(parent, children, now=now)

        monkeypatch.setattr(controller.updater, "apply_split_batch", conflicting_apply)
    else:
        with repo.connection:
            if conflict == "page_manifest":
                repo.connection.execute("UPDATE pages SET sha256=? WHERE document_id=? AND page_number=1", ("0" * 64, archived.job_id))
            else:
                repo.connection.execute("INSERT INTO review_audit(document_id,question_id,revision,original_json,before_json,after_json,confirmed_at) VALUES (?,?,1,'{}','{}','{}','2026-09-15')", (archived.job_id, "1"))
    before = repo.get_document(archived.job_id)
    result = controller.confirm_page_subject(archived.job_id, 4, "英语")
    assert result.state == "pending" and result.error_code == "workflow_failed"
    assert repo.get_document(archived.job_id) == before
    assert repo.get_document("mixed-pages--math") is None
    assert repo.get_document("mixed-pages--english") is None
    assert all(p.exists() for p in archived.archived_pages)


def test_mixed_retry_finishes_interrupted_failed_archive_cleanup(mixed_setup, monkeypatch):
    original_unlink = Path.unlink
    _, repo, _, session = mixed_setup

    def keep_spool_page(path, *args, **kwargs):
        if path.parent == session.session_dir and path.suffix == ".jpg":
            raise OSError("cleanup interrupted")
        return original_unlink(path, *args, **kwargs)

    controller = controller_for(mixed_setup)
    analyzer = controller.analyzer
    with monkeypatch.context() as patch:
        def timeout(_document):
            raise AnalysisError("timeout")
        patch.setattr(analyzer, "analyze", timeout)
        first = controller.finish_and_analyze(session)
    with monkeypatch.context() as patch:
        patch.setattr(Path, "unlink", keep_spool_page)
        archived = controller.confirm_subject(first.job_id, "数学")
    assert archived.error_code == "archive_failed"
    assert repo.get_job(first.job_id).payload["cleanup"]
    controller.retry_pending(first.job_id)
    controller.confirm_page_subject(first.job_id, 2, "数学")
    result = controller.confirm_page_subject(first.job_id, 4, "英语")
    assert result.state == "completed" and result.error_code is None
    assert repo.get_job(first.job_id).payload["cleanup_completed"]
    assert not any(p.exists() for p in archived.archived_pages)
    assert not any(p.path.exists() for p in session.pages)


def test_invalid_subject_cannot_change_job(setup):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyzer.confidence = 0.3
    outcome = controller.finish_and_analyze(session)
    with pytest.raises(ValueError):
        controller.confirm_page_subject(outcome.job_id, 1, "../outside")
    assert repo.get_job(outcome.job_id).state == "needs_subject_confirmation"


def test_finished_spool_without_job_is_discovered_without_analysis(setup):
    _, repo, analyzer, session = setup
    session.finish()
    jobs = controller_for(setup).recover_jobs()
    assert len(jobs) == 1 and jobs[0].state == "pending"
    assert analyzer.calls == 0
    assert controller_for(setup).retry_pending(jobs[0].job_id).state == "completed"


def test_unfinished_capture_is_not_automatically_submitted(setup):
    assert controller_for(setup).recover_jobs() == []


def test_source_hash_mismatch_never_reaches_analyzer_or_archive(setup):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    session.pages[0].path.write_bytes(b"corrupted")
    with pytest.raises(ValueError):
        controller.finish_and_analyze(session)
    assert analyzer.calls == 0
    assert not list(config.knowledge_root.rglob("*.jpg"))


class SimulatedCrash(BaseException):
    pass


def test_crash_during_analysis_reconciles_sidecar_and_db(setup, monkeypatch):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    original = analyzer.analyze
    def crash(document):
        raise SimulatedCrash()
    monkeypatch.setattr(analyzer, "analyze", crash)
    with pytest.raises(SimulatedCrash):
        controller.finish_and_analyze(session)
    jobs = controller_for(setup).recover_jobs()
    assert len(jobs) == 1 and jobs[0].state == "pending"
    sidecar = json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))
    assert sidecar["state"] == repo.get_job(jobs[0].job_id).state
    monkeypatch.setattr(analyzer, "analyze", original)
    assert controller_for(setup).retry_pending(jobs[0].job_id).state == "completed"


def test_export_failure_keeps_applied_facts_and_retry_regenerates(setup, monkeypatch):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    def fail():
        raise OSError("secret diagnostic must not persist")
    monkeypatch.setattr(controller.dashboard, "export", fail)
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "pending"
    assert repo.get_job(outcome.job_id).knowledge_applied
    assert repo.get_knowledge_stats("数学", "分数加法").incorrect_count == 1
    assert "secret" not in (session.session_dir / "analysis_state.json").read_text("utf-8")
    recovered = controller_for(setup).retry_pending(outcome.job_id)
    assert recovered.state == "completed" and recovered.dashboard_path.exists()
    assert analyzer.calls == 1
    assert repo.get_knowledge_stats("数学", "分数加法").incorrect_count == 1


def test_needs_review_is_preserved_after_export(setup):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyzer.status = QuestionStatus.NEEDS_REVIEW
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "needs_review"
    assert repo.get_knowledge_stats("数学", "分数加法").exposure_count == 0
    assert repo.count_review_items() == 1


@pytest.mark.parametrize("boundary", ["copy", "before_commit", "after_commit", "cleanup", "before_updater", "updater", "export"])
def test_replay_across_archive_and_publication_crash_boundaries(setup, monkeypatch, boundary):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    source = session.pages[0].path
    if boundary == "copy":
        owner, name = controller, "_copy_verified"
    elif boundary in {"before_commit", "after_commit"}:
        owner, name = repo, "stage_workflow_archive"
    elif boundary == "cleanup":
        owner, name = Path, "unlink"
    elif boundary in {"before_updater", "updater"}:
        owner, name = controller.updater, "apply"
    else:
        owner, name = controller.dashboard, "export"
    original = getattr(owner, name)
    crashed = False
    def crash(*args, **kwargs):
        nonlocal crashed
        should_crash = not crashed and (boundary != "cleanup" or args[0] == source)
        if should_crash and boundary in {"before_commit", "before_updater"}:
            crashed = True
            raise SimulatedCrash()
        result = original(*args, **kwargs)
        if should_crash:
            crashed = True
            raise SimulatedCrash()
        return result
    monkeypatch.setattr(owner, name, crash)
    with pytest.raises(SimulatedCrash):
        controller.finish_and_analyze(session)
    assert crashed
    if boundary in {"copy", "before_commit"}:
        assert source.exists()
        assert repo.get_document("capture-test") is None
    else:
        document = repo.get_document("capture-test")
        assert all(Path(p["path"]).exists() for p in document["pages"])
        processing = repo.connection.execute("SELECT state FROM processing_jobs").fetchone()
        assert processing["state"] == repo.get_job("capture-test").state
    monkeypatch.setattr(owner, name, original)
    restarted = controller_for(setup)
    jobs = restarted.recover_jobs()
    assert len(jobs) == 1 and jobs[0].state == "pending"
    outcome = restarted.retry_pending("capture-test")
    assert outcome.state == "completed"
    assert repo.count_questions("capture-test") == 1
    assert repo.get_knowledge_stats("数学", "分数加法").exposure_count == 1
    assert analyzer.calls == 1
    job = repo.get_job("capture-test")
    row = repo.connection.execute("SELECT * FROM processing_jobs WHERE document_id='capture-test'").fetchone()
    assert row["state"] == job.state == "completed"
    assert bool(row["knowledge_applied"]) == job.knowledge_applied is True


def test_existing_archive_collision_preserves_both_source_and_destination(setup):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    target = config.knowledge_root / "数学/原始资料/2026/09/capture-test/page_001.jpg"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"another file")
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "pending" and not repo.get_job(outcome.job_id).knowledge_applied
    assert target.read_bytes() == b"another file"
    assert session.pages[0].path.exists()


def test_copy_io_error_leaves_source_and_retry_uses_staged_analysis(setup, monkeypatch):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    def fail(*args):
        raise OSError("disk full secret")
    monkeypatch.setattr(controller, "_copy_verified", fail)
    result = controller.finish_and_analyze(session)
    assert result.state == "pending" and session.pages[0].path.exists()
    assert controller_for(setup).retry_pending(result.job_id).state == "completed"
    assert analyzer.calls == 1


def test_retaken_original_is_archived_with_audit_hash(setup):
    controller = controller_for(setup)
    _, _, _, session = setup
    old_digest = session.pages[0].sha256
    rng = np.random.default_rng(17)
    session.retake(rng.integers(40, 220, (1200, 1600, 3), dtype=np.uint8))
    outcome = controller.finish_and_analyze(session)
    audit = outcome.archived_pages[0].parent / "discarded" / old_digest[:12] / "page_001.jpg"
    assert sha256(audit.read_bytes()).hexdigest() == old_digest
    assert not list((session.session_dir / "discarded").rglob("*.jpg"))


def test_restart_repairs_stale_success_sidecar_without_model_call(setup):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyzer.confidence = 0.2
    outcome = controller.finish_and_analyze(session)
    (session.session_dir / "analysis_state.json").write_text('{"state":"completed"}', encoding="utf-8")
    restored = controller_for(setup).recover_jobs()
    assert restored[0].state == "needs_subject_confirmation"
    assert analyzer.calls == 1 and not repo.get_job(outcome.job_id).knowledge_applied
    assert json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))["state"] == "needs_subject_confirmation"


def test_archive_symlink_rejected_without_writing_outside(setup):
    controller = controller_for(setup)
    config, _, _, session = setup
    outside = config.knowledge_root.parent / "outside"
    outside.mkdir()
    config.knowledge_root.mkdir()
    try:
        (config.knowledge_root / "数学").symlink_to(outside, target_is_directory=True)
    except OSError:
        if os.name != "nt":
            raise
        result = subprocess.run(["cmd", "/c", "mklink", "/J", str(config.knowledge_root / "数学"),
                                 str(outside)], capture_output=True)
        if result.returncode:
            pytest.skip("symlink/junction privileges unavailable")
    result = controller.finish_and_analyze(session)
    assert result.state == "pending"
    assert list(outside.iterdir()) == []
    assert session.pages[0].path.exists()


def test_successful_finish_closes_the_in_memory_capture_session(setup):
    _, _, _, session = setup
    controller_for(setup).finish_and_analyze(session)
    assert session.finished


def test_pending_relocation_can_recover_cleanup_interrupted_twice(setup, monkeypatch):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyzer.error = AnalysisError("timeout")
    result = controller.finish_and_analyze(session)
    original = Path.unlink
    def crash_spool(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == session.pages[0].path:
            raise SimulatedCrash()
        return result
    monkeypatch.setattr(Path, "unlink", crash_spool)
    with pytest.raises(SimulatedCrash):
        controller.confirm_subject(result.job_id, "数学")
    pending_path = Path(repo.get_job(result.job_id).payload["pages"][0]["path"])
    def crash_pending(path, *args, **kwargs):
        result = original(path, *args, **kwargs)
        if path == pending_path:
            raise SimulatedCrash()
        return result
    monkeypatch.setattr(Path, "unlink", crash_pending)
    analyzer.error = None
    with pytest.raises(SimulatedCrash):
        controller_for(setup).retry_pending(result.job_id)
    monkeypatch.setattr(Path, "unlink", original)
    completed = controller_for(setup).retry_pending(result.job_id)
    assert completed.state == "completed"
    assert repo.get_knowledge_stats("数学", "分数加法").exposure_count == 1


def test_recovery_reconciles_processing_job_from_authoritative_journal(setup):
    controller = controller_for(setup)
    _, repo, _, session = setup
    outcome = controller.finish_and_analyze(session)
    with repo.connection:
        repo.connection.execute("UPDATE processing_jobs SET state='pending', knowledge_applied=0")
    controller_for(setup).recover_jobs()
    row = repo.connection.execute("SELECT state, knowledge_applied FROM processing_jobs").fetchone()
    assert row["state"] == "completed" and row["knowledge_applied"] == 1
    assert repo.count_questions(outcome.job_id) == 1


def test_illegal_terminal_to_capture_transition_rejected(setup):
    controller = controller_for(setup)
    _, repo, _, session = setup
    outcome = controller.finish_and_analyze(session)
    job = repo.get_job(outcome.job_id)
    with pytest.raises(ValueError):
        repo.save_workflow_job(replace(job, state="capturing"))
    assert repo.get_job(outcome.job_id).state == "completed"


def test_archive_file_changed_after_journal_commit_is_not_deleted_or_analyzed(setup):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyzer.error = AnalysisError("timeout")
    outcome = controller.finish_and_analyze(session)
    pending = controller.confirm_subject(outcome.job_id, "数学")
    pending.archived_pages[0].write_bytes(b"changed after staging")
    analyzer.error = None
    retry = controller.retry_pending(outcome.job_id)
    assert retry.state == "pending"
    assert analyzer.calls == 1
    assert pending.archived_pages[0].read_bytes() == b"changed after staging"
    assert not repo.get_job(outcome.job_id).knowledge_applied


def test_interrupted_copy_before_rename_keeps_original_and_replays(setup, monkeypatch):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    original = Path.rename
    def interrupt(path, target):
        if Path(target).name == "page_001.jpg":
            raise OSError("interrupted atomic archive rename")
        return original(path, target)
    monkeypatch.setattr(Path, "rename", interrupt)
    outcome = controller.finish_and_analyze(session)
    assert outcome.state == "pending"
    assert session.pages[0].path.exists()
    assert repo.get_document(outcome.job_id) is None
    assert not list(config.knowledge_root.rglob("*.part"))
    monkeypatch.setattr(Path, "rename", original)
    assert controller_for(setup).retry_pending(outcome.job_id).state == "completed"
    assert analyzer.calls == 1


@pytest.mark.parametrize("status, terminal", [(QuestionStatus.INCORRECT, "completed"),
                                             (QuestionStatus.NEEDS_REVIEW, "needs_review")])
def test_first_direct_retry_after_terminal_commit_publishes_without_recovery_scan(setup, monkeypatch, status, terminal):
    controller = controller_for(setup)
    _, repo, analyzer, session = setup
    analyzer.status = status
    def crash(document_id):
        raise SimulatedCrash()
    monkeypatch.setattr(controller.markdown, "export_document", crash)
    with pytest.raises(SimulatedCrash):
        controller.finish_and_analyze(session)
    job = repo.get_job("capture-test")
    assert job.state == terminal and job.payload["export_pending"]
    assert job.knowledge_applied
    restarted = controller_for(setup)
    outcome = restarted.retry_pending(job.job_id)
    assert outcome.state == terminal
    assert outcome.analysis_markdown.exists() and outcome.dashboard_path.exists()
    assert not repo.get_job(job.job_id).payload["export_pending"]
    assert restarted.retry_pending(job.job_id) == outcome
    assert analyzer.calls == 1 and repo.count_questions(job.job_id) == 1
    assert repo.get_knowledge_stats("数学", "分数加法").exposure_count == (1 if terminal == "completed" else 0)


def _extra_finished_session(config, name):
    capture = CaptureSession(config, name)
    capture.capture(np.random.default_rng(19).integers(40, 220, (1200, 1600, 3), dtype=np.uint8))
    capture.finish()
    return capture


@pytest.mark.parametrize("corruption", ["json", "missing_page", "missing_state"])
def test_recovery_isolates_early_corrupt_session_and_recovers_later_sessions_and_journal(setup, corruption):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    analyzer.confidence = 0.2
    existing = controller.finish_and_analyze(session)
    (session.session_dir / "analysis_state.json").write_text('{"state":"completed"}', encoding="utf-8")
    bad = _extra_finished_session(config, "aaa-corrupt")
    if corruption == "json":
        (bad.session_dir / "session.json").write_text('{bad secret content', encoding="utf-8")
    elif corruption == "missing_state":
        (bad.session_dir / "session.json").rename(bad.session_dir / "preserved-session.json")
    else:
        bad.pages[0].path.rename(bad.session_dir / "preserved-evidence.jpg")
    later = _extra_finished_session(config, "zzz-healthy")
    before = {p.name: p.read_bytes() for p in bad.session_dir.iterdir() if p.is_file()}
    outcomes = {o.job_id: o for o in controller_for(setup).recover_jobs()}
    assert outcomes[later.session_dir.name].state == "pending"
    assert outcomes[existing.job_id].state == "needs_subject_confirmation"
    assert outcomes[bad.session_dir.name].error_code == "recovery_failed"
    assert outcomes[bad.session_dir.name].recovery_path == bad.session_dir
    assert "secret" not in repr(outcomes[bad.session_dir.name])
    assert repo.get_job(bad.session_dir.name) is None
    assert before == {p.name: p.read_bytes() for p in bad.session_dir.iterdir() if p.is_file()}
    assert json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))["state"] == "needs_subject_confirmation"
    assert analyzer.calls == 1


@pytest.mark.parametrize("failure", ["new_sidecar", "existing_mirror", "journal_json"])
def test_recovery_isolates_failed_sidecar_and_invalid_existing_journal(setup, monkeypatch, failure):
    controller = controller_for(setup)
    config, repo, analyzer, session = setup
    analyzer.confidence = 0.2
    good_existing = controller.finish_and_analyze(session)
    bad = _extra_finished_session(config, "aaa-bad")
    if failure != "new_sidecar":
        controller.finish_and_analyze(bad)
    later = _extra_finished_session(config, "zzz-good")
    if failure == "journal_json":
        with repo.connection:
            repo.connection.execute("UPDATE workflow_jobs SET payload_json='{invalid secret' WHERE job_id='aaa-bad'")
    else:
        from qingzi_learning.workflow import controller as module
        original = module.atomic_write
        def fail_sidecar(destination, content, root):
            if destination.parent == bad.session_dir:
                raise PermissionError("private secret diagnostic")
            return original(destination, content, root)
        monkeypatch.setattr(module, "atomic_write", fail_sidecar)
    calls = analyzer.calls
    outcomes = controller_for(setup).recover_jobs()
    by_id = {o.job_id: o for o in outcomes}
    assert len(outcomes) == len(by_id)
    assert by_id["aaa-bad"].error_code == "recovery_failed"
    assert by_id["aaa-bad"].recovery_path == bad.session_dir
    assert "secret" not in repr(by_id["aaa-bad"])
    assert by_id["zzz-good"].state == "pending"
    assert by_id[good_existing.job_id].state == "needs_subject_confirmation"
    assert bad.pages[0].path.exists() and later.pages[0].path.exists()
    assert analyzer.calls == calls
