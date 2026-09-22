"""The visible reading layer must obey the same ownership as SQLite facts."""
from dataclasses import replace
from pathlib import Path
import threading

import pytest

from test_review_service import repo, seed, service, decision
from test_publication_ownership import NeverAnalyze
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.workflow.controller import WorkflowController


class PublicationCrash(BaseException):
    pass


def test_parent_render_and_mirror_failure_still_commits_retryable_state(repo, monkeypatch):
    seed(repo, ("needs_review",))
    parent = service(repo)
    def fail(*args):
        raise OSError("injected filesystem failure")
    def fail_renderer():
        assert not repo.connection.in_transaction
        fail()
    monkeypatch.setattr(parent.dashboard, "export", fail_renderer)
    monkeypatch.setattr(parent, "_mirror", fail)
    decision(parent, parent.list_pending()[0], "correct")
    assert repo.get_job("doc").state == "pending"
    assert repo.pending_review_publications() == ("doc",)


def visible_files(root):
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and p.suffix in {".md", ".html"}}


def document_note(repo, document_id="doc", subject="语文"):
    return (repo.config.knowledge_root / subject / "分析记录"
            / f"{repo.document_display_name(document_id)}.md")


@pytest.mark.parametrize("kind", ["html", "markdown"])
@pytest.mark.parametrize("old_failure", [False, True])
@pytest.mark.parametrize("remaining", [0, 1])
def test_parent_publication_wins_over_old_actual_write_entrance(repo, monkeypatch, kind, old_failure, remaining):
    seed(repo, ("needs_review",) * (remaining + 1))
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
    other = KnowledgeRepository(repo.config)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    owner = controller.dashboard if kind == "html" else controller.dashboard.markdown
    original = owner._atomic_write
    expected = {}

    def old_write(destination, content):
        assert not repo.connection.in_transaction
        wanted = destination.name == (
            "知识库首页.html" if kind == "html" else document_note(repo).name
        )
        if wanted and not expected:
            parent = service(other)
            decision(parent, parent.list_pending()[0], "correct")
            expected["job"] = other.get_job("doc")
            expected["files"] = visible_files(repo.config.knowledge_root)
            assert expected["job"].state == ("needs_review" if remaining else "completed")
            # This is the old renderer's real write entrance, with bytes rendered
            # before the parent changed facts. It must only stage, never replace.
            original(destination, content)
            if old_failure:
                raise OSError("old staged renderer failed")
        else:
            original(destination, content)

    monkeypatch.setattr(owner, "_atomic_write", old_write)
    try:
        result = controller.retry_pending("doc")
        assert result.state == expected["job"].state
        assert repo.get_job("doc") == expected["job"]
        assert visible_files(repo.config.knowledge_root) == expected["files"]
        assert len(service(repo).history("doc", "1")) == 1
        assert repo.get_knowledge_stats("语文", "概括主要内容").correct_count == 1
    finally:
        other.close()


@pytest.mark.parametrize("parent_owned", [False, True])
@pytest.mark.parametrize("kind", ["html", "markdown"])
@pytest.mark.parametrize("restart", [False, True])
def test_terminal_retry_repairs_corrupt_visible_files_without_analysis(repo, parent_owned, kind, restart):
    seed(repo, ("needs_review",) if parent_owned else ("correct",))
    if parent_owned:
        parent = service(repo)
        decision(parent, parent.list_pending()[0], "correct")
    else:
        initial = repo.get_job("doc")
        repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
        assert WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc").state == "completed"
    root = repo.config.knowledge_root
    target = root / "知识库首页.html" if kind == "html" else document_note(repo)
    target.write_text("stale visible content", encoding="utf-8")
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    if restart:
        assert any(job.job_id == "doc" and job.state == "pending" for job in controller.recover_jobs())
    result = controller.retry_pending("doc")
    assert result.state == "completed"
    assert "stale visible content" not in target.read_text(encoding="utf-8")
    assert repo.get_knowledge_stats("语文", "概括主要内容").correct_count == (1 if parent_owned else 0)
    assert len(service(repo).history("doc", "1")) == int(parent_owned)


def test_other_document_change_rejects_staged_aggregate_and_leaves_retryable_job(repo, monkeypatch):
    seed(repo, ("needs_review",))
    seed(repo, ("needs_review",), document_id="other")
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
    second = KnowledgeRepository(repo.config)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    original = controller.dashboard._atomic_write
    expected = {}
    def interleave(destination, content):
        parent = service(second)
        decision(parent, parent.get_question("other", "1"), "correct")
        expected.update(visible_files(repo.config.knowledge_root))
        original(destination, content)
    monkeypatch.setattr(controller.dashboard, "_atomic_write", interleave)
    try:
        assert controller.retry_pending("doc").state == "pending"
        assert visible_files(repo.config.knowledge_root) == expected
        assert WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc").state == "needs_review"
        assert service(repo).get_question("other", "1").status == "correct"
    finally:
        second.close()


@pytest.mark.parametrize("parent_owned", [False, True])
@pytest.mark.parametrize("kind", ["html", "markdown"])
@pytest.mark.parametrize("crash", [False, True])
def test_partial_visible_replace_failure_is_recoverable_and_unlocks(repo, monkeypatch, parent_owned, kind, crash):
    from qingzi_learning.export import publication
    seed(repo, ("needs_review",))
    original = publication.os.replace
    failed = False
    def interrupt(source, destination):
        nonlocal failed
        wanted = Path(destination).name == (
            "知识库首页.html" if kind == "html" else document_note(repo).name
        )
        result = original(source, destination)
        if not failed and wanted and Path(source).suffix == ".part" and Path(source).parent == Path(destination).parent:
            failed = True
            raise PublicationCrash() if crash else OSError("injected visible replacement failure")
        return result
    monkeypatch.setattr(publication.os, "replace", interrupt)
    def run():
        if parent_owned:
            parent = service(repo)
            decision(parent, parent.list_pending()[0], "correct")
        else:
            initial = repo.get_job("doc")
            repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
            WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc")
    if crash:
        with pytest.raises(PublicationCrash):
            run()
    else:
        run()
        assert repo.get_job("doc").state == "pending"
    assert failed
    assert repo.connection.execute("SELECT pending FROM reading_publication").fetchone()[0] == 1
    monkeypatch.setattr(publication.os, "replace", original)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    assert controller.recover_jobs()[0].state == "pending"
    assert controller.retry_pending("doc").state == ("completed" if parent_owned else "needs_review")
    assert controller.publication.current()
    assert len(service(repo).history("doc", "1")) == int(parent_owned)


@pytest.mark.parametrize("old_failure", [False, True])
@pytest.mark.parametrize("remaining", [0, 1])
def test_ordinary_holds_visible_lock_first_then_parent_publishes_latest(repo, monkeypatch, old_failure, remaining):
    from qingzi_learning.export import publication
    seed(repo, ("needs_review",) * (remaining + 1))
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
    enter_parent, attempted = threading.Event(), threading.Event()
    failures = []
    def parent_thread():
        try:
            assert enter_parent.wait(10)
            attempted.set()
            other = KnowledgeRepository(repo.config)
            try:
                parent = service(other)
                decision(parent, parent.list_pending()[0], "correct")
            finally:
                other.close()
        except BaseException as exc:
            failures.append(exc)
    worker = threading.Thread(target=parent_thread)
    worker.start()
    original = publication.os.replace
    entered = False
    def publish_old(source, destination):
        nonlocal entered
        if not entered and Path(source).suffix == ".part" and Path(source).parent == Path(destination).parent:
            entered = True
            enter_parent.set()
            assert attempted.wait(5)
            # Parent may now wait for our short DB transaction or root lock; we
            # never wait for its renderer/publication while holding either lock.
            if old_failure:
                raise OSError("old visible publication interrupted")
        return original(source, destination)
    monkeypatch.setattr(publication.os, "replace", publish_old)
    try:
        WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc")
    finally:
        enter_parent.set()
        worker.join(15)
    assert not worker.is_alive() and not failures
    assert repo.get_job("doc").state == ("needs_review" if remaining else "completed")
    assert "decision_source: parent" in document_note(repo).read_text(encoding="utf-8")
    assert WorkflowController(repo.config, NeverAnalyze(), repo).publication.current()


@pytest.mark.parametrize("manifest", ["[1]", '{"../outside.md":"invalid"}', "{bad json"])
def test_invalid_manifest_is_repaired_without_trusting_its_paths(repo, manifest):
    seed(repo, ("needs_review",))
    parent = service(repo)
    decision(parent, parent.list_pending()[0], "correct")
    outside = repo.config.knowledge_root.parent / "outside.md"
    outside.write_text("untouched", encoding="utf-8")
    with repo.connection:
        repo.connection.execute("UPDATE reading_publication SET files_json=?", (manifest,))
    assert WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc").state == "completed"
    assert WorkflowController(repo.config, NeverAnalyze(), repo).publication.current()
    assert outside.read_text(encoding="utf-8") == "untouched"


def test_publication_does_not_rescan_all_facts_for_every_output_file(repo, monkeypatch):
    seed(repo, ("needs_review",))
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
    original = repo.reading_revision
    scans = []
    def count_scan():
        scans.append(True)
        return original()
    monkeypatch.setattr(repo, "reading_revision", count_scan)
    result = WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc")
    assert result.state == "needs_review"
    assert len(visible_files(repo.config.knowledge_root)) >= 6
    assert len(scans) <= 2, "whole-knowledge fingerprint must not become quadratic in output count"


def test_recovery_file_check_cannot_overwrite_a_parent_that_publishes_after_its_snapshot(repo, monkeypatch):
    seed(repo, ("needs_review",))
    other = KnowledgeRepository(repo.config)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    original = controller.publication.current
    expected = {}
    def check_then_parent_publishes():
        observed = original()
        parent = service(other)
        decision(parent, parent.list_pending()[0], "correct")
        expected["job"] = other.get_job("doc")
        return observed
    monkeypatch.setattr(controller.publication, "current", check_then_parent_publishes)
    try:
        outcomes = controller.recover_jobs()
        assert repo.get_job("doc") == expected["job"]
        assert not any(item.job_id == "doc" and item.state == "pending" for item in outcomes)
        assert WorkflowController(repo.config, NeverAnalyze(), repo).publication.current()
    finally:
        other.close()
