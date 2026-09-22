"""A manifest cannot define its own completeness or a parent's ownership."""
from dataclasses import replace
from contextlib import contextmanager
from hashlib import sha256
import json
import threading

import pytest

from test_review_service import repo, seed, service, decision
from test_publication_ownership import NeverAnalyze
from test_file_publication import visible_files
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.workflow.controller import WorkflowController
from qingzi_learning.review.service import ReviewService
from tests.workflow.test_controller import mixed_setup, controller_for, _confirm_mixed


def document_relative(repo, document_id="doc", subject="语文"):
    return f"{subject}/分析记录/{repo.document_display_name(document_id)}.md"


def test_split_batch_has_one_parent_publication_with_complete_outputs(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    owners = []
    publish = controller.publication.publish
    def observe(expected, render, finalize, **kwargs):
        owners.append(expected.job_id)
        return publish(expected, render, finalize, **kwargs)
    monkeypatch.setattr(controller.publication, "publish", observe)
    result = _confirm_mixed(controller, session)
    assert owners == ["mixed-pages"]
    assert result.state == "completed"
    row = repository.connection.execute("SELECT * FROM reading_publication").fetchone()
    outputs = json.loads(row["files_json"])
    assert document_relative(repository, "mixed-pages--math", "数学") in outputs
    assert document_relative(repository, "mixed-pages--english", "英语") in outputs
    assert not any(path.endswith("/mixed-pages.md") for path in outputs)
    assert set(outputs) == controller.publication._expected_outputs()
    assert row["facts_hash"] == repository.reading_revision()
    assert controller.publication.current()


@pytest.mark.parametrize("old_failure", [False, True])
def test_superseded_split_publisher_cannot_downgrade_new_batch(mixed_setup, monkeypatch, old_failure):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    render = controller.dashboard.export
    winners = []
    def supersede():
        newer = controller_for(mixed_setup)
        winners.append(newer.retry_pending("mixed-pages"))
        assert winners[-1].state == "completed" and newer.publication.current()
        if old_failure:
            raise OSError("old renderer failed")
        return render()
    monkeypatch.setattr(controller.dashboard, "export", supersede)
    result = _confirm_mixed(controller, session)
    assert result == winners[0]
    assert controller.publication.current()
    assert all(not repository.get_job(c).payload["export_pending"] for c in result.child_document_ids)


def test_superseded_split_outcome_does_not_expose_unpublished_terminal_candidate(mixed_setup, monkeypatch):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    render = controller.dashboard.export
    def newer_candidate():
        parent = repository.get_job("mixed-pages")
        for identity in [parent.job_id, *parent.payload["child_document_ids"]]:
            job = repository.get_job(identity)
            repository.save_workflow_job(replace(job, payload=dict(job.payload, ordinary_publication_id="newer-attempt")))
        return render()
    monkeypatch.setattr(controller.dashboard, "export", newer_candidate)
    result = _confirm_mixed(controller, session)
    assert result.state == "pending"
    durable = repository.get_job(result.job_id)
    assert durable.state == "completed" and durable.payload["export_pending"]
    assert all(p.path.exists() for p in session.pages)
    assert controller_for(mixed_setup).retry_pending(result.child_document_ids[0]).state == "completed"


@pytest.mark.parametrize("retry_id", ["mixed-pages", "mixed-pages--english"])
@pytest.mark.parametrize("still_failing", [False, True])
def test_split_retry_reconciles_failed_child_review_with_its_owner(mixed_setup, monkeypatch, retry_id, still_failing):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    _confirm_mixed(controller, session)
    render = controller.dashboard.export
    def fail():
        raise OSError("review export unavailable")
    monkeypatch.setattr(controller.dashboard, "export", fail)
    version = controller.review.get_question("mixed-pages--math", "1").version
    controller.review.confirm_question("mixed-pages--math", "1", "incorrect", "42", "parent correction", expected_version=version)
    failed_child = repository.get_job("mixed-pages--math")
    sibling = repository.get_job("mixed-pages--english")
    assert controller.review.pending_publications() == (failed_child.job_id,)
    if not still_failing:
        monkeypatch.setattr(controller.dashboard, "export", render)
    outcome = controller.retry_pending(retry_id)
    child = repository.get_job(failed_child.job_id)
    assert child.payload["ordinary_publication_id"] == failed_child.payload["ordinary_publication_id"]
    assert child.payload["parent_publication_id"] != failed_child.payload["parent_publication_id"]
    assert repository.get_job(sibling.job_id) == sibling
    if still_failing:
        assert outcome.state == "pending"
        assert child.last_error == "review_export_failed" and child.payload["export_pending"]
        assert controller.review.pending_publications() == (child.job_id,)
    else:
        assert outcome.state == "completed"
        assert child.last_error is None and not child.payload["export_pending"]
        assert controller.review.pending_publications() == ()
        assert controller.publication.current()


@pytest.mark.parametrize("old_failure", [False, True])
def test_old_split_publisher_cannot_invalidate_new_child_review(mixed_setup, monkeypatch, old_failure):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    initial = _confirm_mixed(controller, session)
    initial.dashboard_path.write_text("force ordinary batch refresh", encoding="utf-8")
    render = controller.dashboard.export
    newer_jobs = []
    def review_during_render():
        review = ReviewService(repository)
        version = review.get_question("mixed-pages--math", "1").version
        review.confirm_question("mixed-pages--math", "1", "incorrect", "42", "newer review", expected_version=version)
        assert review.publication.current()
        newer_jobs.extend(repository.get_job(c) for c in [initial.job_id, *initial.child_document_ids])
        if old_failure:
            raise OSError("stale batch renderer failed")
        return render()
    monkeypatch.setattr(controller.dashboard, "export", review_during_render)
    controller.retry_pending(initial.job_id)
    assert len(newer_jobs) == 3
    assert [repository.get_job(j.job_id) for j in newer_jobs] == newer_jobs
    assert controller.publication.current()
    assert controller.review.pending_publications() == ()


@pytest.mark.parametrize("ordinary_failure", [False, True])
def test_split_candidate_and_finalizer_preserve_completed_review_owner(mixed_setup, monkeypatch, ordinary_failure):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    initial = _confirm_mixed(controller, session)
    version = controller.review.get_question("mixed-pages--math", "1").version
    controller.review.confirm_question("mixed-pages--math", "1", "incorrect", "42", "reviewed", expected_version=version)
    sibling = repository.get_job("mixed-pages--english")
    repository.save_workflow_job(replace(sibling, state="pending", payload=dict(sibling.payload, export_pending=True)))
    reviewed = []
    retry_review = controller.review.retry_publication
    def capture_review_owner(identity):
        result = retry_review(identity)
        assert result
        reviewed.append(repository.get_job(identity))
        return result
    monkeypatch.setattr(controller.review, "retry_publication", capture_review_owner)
    publish = controller.publication.publish
    def ordinary(expected, render, finalize, **kwargs):
        assert repository.get_job("mixed-pages--math") == reviewed[-1]
        if ordinary_failure:
            raise OSError("ordinary batch unavailable")
        return publish(expected, render, finalize, **kwargs)
    monkeypatch.setattr(controller.publication, "publish", ordinary)
    result = controller.retry_pending(initial.job_id)
    assert len(reviewed) == 1
    assert repository.get_job("mixed-pages--math") == reviewed[0]
    assert controller.review.pending_publications() == ()
    assert result.state == ("pending" if ordinary_failure else "completed")
    if not ordinary_failure:
        assert controller.publication.current()


@pytest.mark.parametrize("review_failure", [False, True])
def test_split_child_review_before_coordinator_baseline_supersedes_batch(mixed_setup, monkeypatch, review_failure):
    controller = controller_for(mixed_setup)
    _, repository, _, session = mixed_setup
    initial = _confirm_mixed(controller, session)
    initial.dashboard_path.write_text("force ordinary refresh", encoding="utf-8")
    publish = controller.publication.publish
    newer = {}
    def review_before_baseline(expected, render, finalize, **kwargs):
        review = ReviewService(repository)
        if review_failure:
            def fail():
                raise OSError("new review export failed")
            monkeypatch.setattr(review.dashboard, "export", fail)
        version = review.get_question("mixed-pages--math", "1").version
        review.confirm_question("mixed-pages--math", "1", "incorrect", "42", "before baseline", expected_version=version)
        newer["jobs"] = [repository.get_job(c) for c in [initial.job_id, *initial.child_document_ids]]
        newer["outbox"] = tuple(repository.connection.execute("SELECT * FROM review_publications").fetchone())
        newer["publication"] = tuple(repository.connection.execute("SELECT * FROM reading_publication").fetchone())
        newer["files"] = visible_files(repository.config.knowledge_root)
        assert review.publication.current() == (not review_failure)
        return publish(expected, render, finalize, **kwargs)
    monkeypatch.setattr(controller.publication, "publish", review_before_baseline)
    controller.retry_pending(initial.job_id)
    assert newer
    assert [repository.get_job(c.job_id) for c in newer["jobs"]] == newer["jobs"]
    assert tuple(repository.connection.execute("SELECT * FROM review_publications").fetchone()) == newer["outbox"]
    assert tuple(repository.connection.execute("SELECT * FROM reading_publication").fetchone()) == newer["publication"]
    assert visible_files(repository.config.knowledge_root) == newer["files"]
    assert controller.publication.current() == (not review_failure)


@pytest.mark.parametrize("deny_at", ["manifest", "first_file", "second_file", "finalize", None])
def test_publication_owner_predicate_runs_locked_in_every_write_transaction(repo, monkeypatch, deny_at):
    from qingzi_learning.export import publication as module
    publish_initial(repo, False)
    coordinator = module.PublicationCoordinator(repo)
    initial = tuple(repo.connection.execute("SELECT * FROM reading_publication").fetchone())
    file_count = len(json.loads(initial[3]))
    stop = {"manifest": 1, "first_file": 2, "second_file": 3, "finalize": file_count + 2, None: None}[deny_at]
    calls, replacements, finalized, lock_held = [], [], [], []
    lock, replace_file = module.publication_lock, module.replace_guarded
    @contextmanager
    def tracked_lock(root):
        with lock(root):
            lock_held.append(True)
            try:
                yield
            finally:
                lock_held.pop()
    def tracked_replace(*args):
        replacements.append(args[1])
        return replace_file(*args)
    def owner_current():
        assert lock_held and repo.connection.in_transaction
        calls.append(True)
        return len(calls) != stop
    monkeypatch.setattr(module, "publication_lock", tracked_lock)
    monkeypatch.setattr(module, "replace_guarded", tracked_replace)
    result = coordinator.publish(repo.get_job("doc"), service(repo).dashboard.export,
                                 lambda paths: finalized.append(paths), ownership_check=owner_current)
    assert result == (deny_at is None)
    assert len(calls) == (file_count + 2 if stop is None else stop)
    assert len(replacements) == (file_count if stop is None else min(file_count, max(0, stop - 2)))
    assert bool(finalized) == (deny_at is None)
    if deny_at == "manifest":
        assert tuple(repo.connection.execute("SELECT * FROM reading_publication").fetchone()) == initial


def publish_initial(repo, parent_owned):
    seed(repo, ("needs_review",) if parent_owned else ("correct",))
    if parent_owned:
        parent = service(repo)
        decision(parent, parent.list_pending()[0], "correct")
    else:
        job = repo.get_job("doc")
        repo.save_workflow_job(replace(job, payload=dict(job.payload, export_pending=True)))
        assert WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc").state == "completed"


def test_missing_parent_curriculum_note_is_recreated_without_overwriting_other_notes(repo):
    from qingzi_learning.curriculum.exporter import CurriculumExporter
    from qingzi_learning.storage.paths import KnowledgePaths

    publish_initial(repo, False)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    curriculum = CurriculumExporter(KnowledgePaths(repo.config))
    missing = curriculum._file("我的-u01.md")
    preserved = curriculum._file("我的-u02.md")
    preserved.write_text("家长自己写的笔记", encoding="utf-8")
    missing.unlink()

    assert not controller.publication.current()
    assert controller.retry_pending("doc").state == "completed"
    assert missing.is_file()
    assert preserved.read_text(encoding="utf-8") == "家长自己写的笔记"
    assert controller.publication.current()


@pytest.mark.parametrize("parent_owned", [False, True])
@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("damage", ["omitted_corrupt", "extra", "renamed", "empty", "missing", "hash", "path"])
def test_manifest_exact_set_is_reconstructed_on_retry(repo, parent_owned, restart, damage):
    publish_initial(repo, parent_owned)
    root = repo.config.knowledge_root
    expected = visible_files(root)
    row = repo.connection.execute("SELECT * FROM reading_publication").fetchone()
    manifest = json.loads(row["files_json"])
    path = document_relative(repo)
    if damage == "omitted_corrupt":
        del manifest[path]
        (root / path).write_text("corrupt omitted file", encoding="utf-8")
    elif damage == "extra":
        (root / "user.md").write_text("user-owned", encoding="utf-8")
        manifest["user.md"] = sha256(b"user-owned").hexdigest()
    elif damage == "renamed":
        renamed = "语文/分析记录/renamed.md"
        (root / path).rename(root / renamed)
        manifest[renamed] = manifest.pop(path)
    elif damage == "empty":
        manifest = {}
    elif damage == "missing":
        (root / path).unlink()
    elif damage == "hash":
        manifest[path] = "0" * 64
    else:
        manifest[f"语文/分析记录/../分析记录/{repo.document_display_name('doc')}.md"] = manifest.pop(path)
    with repo.connection:
        repo.connection.execute("UPDATE reading_publication SET files_json=?", (json.dumps(manifest),))
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    assert not controller.publication.current(), "manifest must not conceal an incomplete or noncanonical set"
    if restart:
        assert any(item.job_id == "doc" for item in controller.recover_jobs())
    assert controller.retry_pending("doc").state == "completed"
    assert controller.publication.current()
    actual = json.loads(repo.connection.execute("SELECT files_json FROM reading_publication").fetchone()[0])
    # Parent-owned 我的-* notes are real linked files, but deliberately not
    # hash-owned by the generated-output manifest.
    assert set(actual) == controller.publication._expected_outputs()
    assert all((root / name).read_bytes() == content for name, content in expected.items())
    if damage == "extra":
        assert (root / "user.md").read_text(encoding="utf-8") == "user-owned"
    assert len(service(repo).history("doc", "1")) == int(parent_owned)


@pytest.mark.parametrize("old_failure", [False, True])
@pytest.mark.parametrize("new_phase", ["rendering", "published"])
@pytest.mark.parametrize("remaining", [0, 1])
@pytest.mark.parametrize("new_revision", [False, True])
def test_old_parent_cannot_mutate_a_new_review_attempt(repo, old_failure, new_phase, remaining, new_revision):
    seed(repo, ("needs_review",) * (remaining + 1))
    parent = service(repo)
    parent.updater.confirm_review("doc", "1", "correct", "first answer", "first note")
    old_rendering, release_old, old_finished = (threading.Event() for _ in range(3))
    errors, old_results = [], []
    def old_thread():
        other = KnowledgeRepository(repo.config)
        try:
            old = service(other)
            original = old.dashboard.export
            def pause():
                old_rendering.set()
                assert release_old.wait(10)
                if old_failure:
                    raise OSError("delayed old parent renderer failure")
                return original()
            old.dashboard.export = pause
            old_results.append(old.retry_publication("doc"))
        except BaseException as exc:
            errors.append(exc)
        finally:
            other.close()
            old_finished.set()
    worker = threading.Thread(target=old_thread)
    worker.start()
    try:
        assert old_rendering.wait(10)
        if new_revision:
            parent.updater.confirm_review("doc", "1", "incorrect", "new answer", "new note",
                                          expected_version=parent.get_question("doc", "1").version)
        original = parent.dashboard.export
        def newer_render():
            if new_phase == "rendering":
                snapshot = repo.get_job("doc")
                version = repo.review_publication_revision("doc")
                release_old.set()
                assert old_finished.wait(10)
                assert repo.get_job("doc") == snapshot
                assert repo.review_publication_revision("doc") == version
            return original()
        parent.dashboard.export = newer_render
        assert parent.retry_publication("doc"), "stale callback must not invalidate the newer renderer"
        expected = repo.get_job("doc")
        files = visible_files(repo.config.knowledge_root)
        release_old.set()
        assert old_finished.wait(10)
        assert repo.get_job("doc") == expected
        assert repo.get_job("doc").state == ("needs_review" if remaining else "completed")
        assert visible_files(repo.config.knowledge_root) == files
        assert parent.publication.current()
        assert WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc").state == expected.state
        assert len(parent.history("doc", "1")) == (2 if new_revision else 1)
        stats = repo.get_knowledge_stats("语文", "概括主要内容")
        assert (stats.correct_count, stats.incorrect_count, stats.needs_review) == (int(not new_revision), int(new_revision), remaining)
    finally:
        release_old.set()
        worker.join(10)
    assert not worker.is_alive() and not errors
    assert old_results == [False]


@pytest.mark.parametrize("damage", ["omission", "empty", "extra", "generation"])
def test_expected_set_cannot_hide_missing_manifest_entries(repo, damage):
    publish_initial(repo, True)
    row = repo.connection.execute("SELECT * FROM reading_output_set").fetchone()
    expected = json.loads(row["paths_json"])
    manifest = json.loads(repo.connection.execute("SELECT files_json FROM reading_publication").fetchone()[0])
    path = document_relative(repo)
    if damage == "generation":
        generation = "stale-generation"
    else:
        generation = row["generation"]
        if damage == "omission":
            expected.remove(path)
            del manifest[path]
        elif damage == "empty":
            expected, manifest = [], {}
        else:
            expected.append("user.md")
            (repo.config.knowledge_root / "user.md").write_bytes(b"untouched")
            manifest["user.md"] = sha256(b"untouched").hexdigest()
    with repo.connection:
        repo.connection.execute("UPDATE reading_output_set SET paths_json=?, generation=?", (json.dumps(expected), generation))
        repo.connection.execute("UPDATE reading_publication SET files_json=?", (json.dumps(manifest),))
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    assert not controller.publication.current()
    assert controller.retry_pending("doc").state == "completed"
    assert controller.publication.current()


@pytest.mark.parametrize("parent_owned", [False, True])
def test_actual_batch_must_include_every_expected_destination(repo, monkeypatch, parent_owned):
    publish_initial(repo, parent_owned)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    visible = visible_files(repo.config.knowledge_root)
    # A broken renderer must not replace even its valid subset or commit a
    # self-certified subset. Both nested exporters deliberately omit one note.
    from qingzi_learning.export.markdown import MarkdownExporter
    original = MarkdownExporter._atomic_write
    def omit_document(self, destination, content):
        if destination.name != f"{repo.document_display_name('doc')}.md":
            original(self, destination, content)
    with repo.connection:
        repo.connection.execute("UPDATE reading_publication SET pending=1")
    with monkeypatch.context() as patch:
        patch.setattr(MarkdownExporter, "_atomic_write", omit_document)
        assert controller.retry_pending("doc").state == "pending"
        assert visible_files(repo.config.knowledge_root) == visible
    assert controller.retry_pending("doc").state == "completed"
    assert controller.publication.current()


@pytest.mark.parametrize("parent_owned", [False, True])
def test_expected_destinations_follow_document_and_knowledge_set_changes(repo, parent_owned):
    publish_initial(repo, parent_owned)
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    original = set(json.loads(repo.connection.execute("SELECT files_json FROM reading_publication").fetchone()[0]))
    seed(repo, ("needs_review",), document_id="new")
    with repo.connection:
        repo.connection.execute("INSERT INTO question_knowledge_points VALUES ('new', '1', 'new point')")
    assert not controller.publication.current()
    assert controller.retry_pending("doc").state == "completed"
    expanded = set(json.loads(repo.connection.execute("SELECT files_json FROM reading_publication").fetchone()[0]))
    assert len(expanded - original) == 2
    # Simulate removal of a document without analysis-derived stats, retaining
    # user files on disk. The authoritative current set must shrink, not trust
    # yesterday's nonempty mapping or delete unrelated/retired user material.
    with repo.connection:
        repo.connection.execute("DELETE FROM documents WHERE document_id='new'")
    assert not controller.publication.current()
    assert controller.retry_pending("doc").state == "completed"
    final = repo.connection.execute("SELECT * FROM reading_output_set").fetchone()
    assert set(json.loads(final["paths_json"])) == original
    assert controller.publication.current()


@pytest.mark.parametrize("missing", ["expected", "review_revision"])
def test_failure_writeback_without_complete_ownership_is_noop(repo, missing):
    seed(repo, ("needs_review",))
    parent = service(repo)
    parent.updater.confirm_review("doc", "1", "correct", "answer", "note")
    expected = repo.prepare_review_publication("doc", files_current=False)
    revision = repo.review_publication_revision("doc")
    arguments = dict(expected=expected, review_revision=revision)
    del arguments[missing]
    mirrors = []
    repo.mark_review_export_failed("doc", **arguments, before_commit=lambda: mirrors.append(True))
    assert repo.get_job("doc") == expected
    assert repo.review_publication_revision("doc") == revision
    assert mirrors == []
