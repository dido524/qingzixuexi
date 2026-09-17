"""Two real connections interleave ordinary publication and parent ownership."""
from dataclasses import asdict, replace
import json

import pytest

from test_review_service import repo, seed, service, decision
from qingzi_learning.export.dashboard import DashboardExporter
from qingzi_learning.storage.repository import KnowledgeRepository
from qingzi_learning.workflow.controller import WorkflowController


class NeverAnalyze:
    def analyze(self, _document):
        raise AssertionError("publication must not re-analyze durable facts")


def test_workflow_snapshot_compares_the_persisted_json_form(repo):
    original = seed(repo, ("needs_review",))
    persisted = repo.get_job("doc")
    # In-memory analysis dataclasses contain tuples; SQLite JSON returns lists.
    expected = replace(persisted, payload=dict(persisted.payload, analysis=asdict(original)))
    advanced = replace(expected, state="pending")
    assert repo.save_workflow_job_if_current(expected, advanced)
    assert repo.get_job("doc").state == "pending"


@pytest.mark.parametrize("export_pending", [False, True])
def test_parent_ownership_taken_after_entry_snapshot_is_not_overwritten(repo, monkeypatch, export_pending):
    seed(repo, ("needs_review",))
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=export_pending)))
    other = KnowledgeRepository(repo.config)
    revision = repo.review_publication_revision
    expected = {}

    def take_parent_ownership(document_id):
        observed = revision(document_id)
        if not expected:
            parent = service(other)
            decision(parent, parent.list_pending()[0])
            expected["job"] = other.get_job(document_id)
        return observed

    monkeypatch.setattr(repo, "review_publication_revision", take_parent_ownership)
    try:
        outcome = WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc")
        assert repo.get_job("doc") == expected["job"]
        assert outcome.state == "completed" and outcome.error_code is None
        assert json.loads((repo.config.spool_root / "doc" / "analysis_state.json").read_text())["state"] == "completed"
        assert len(service(repo).history("doc", "1")) == 1
    finally:
        other.close()


@pytest.mark.parametrize("old_failure", [False, True])
@pytest.mark.parametrize("parent_state", ["completed", "needs_review", "pending"])
def test_old_ordinary_publication_cannot_overwrite_new_parent_owner(repo, old_failure, parent_state):
    statuses = ("needs_review", "needs_review") if parent_state == "needs_review" else ("needs_review",)
    seed(repo, statuses)
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
    other = KnowledgeRepository(repo.config)
    expected = {}
    ordinary_dashboard = DashboardExporter(repo)

    class ParentExportFailure:
        def export(self):
            raise OSError("parent export interrupted")

    class PausedOrdinaryDashboard:
        def export(self):
            path = ordinary_dashboard.export()
            # The ordinary exporter has produced its result but has not written
            # back success/failure. A second real SQLite connection takes over.
            parent = service(other, **({"dashboard": ParentExportFailure()} if parent_state == "pending" else {}))
            decision(parent, parent.list_pending()[0])
            expected["job"] = other.get_job("doc")
            expected["outbox"] = tuple(other.connection.execute("SELECT revision, pending FROM review_publications WHERE document_id='doc'").fetchone())
            assert expected["job"].state == parent_state
            if old_failure:
                raise OSError("old exporter failure must not replace parent error")
            return path

    try:
        controller = WorkflowController(repo.config, NeverAnalyze(), repo, dashboard=PausedOrdinaryDashboard())
        outcome = controller.retry_pending("doc")
        assert repo.get_job("doc") == expected["job"]
        assert outcome.state == parent_state and outcome.error_code == expected["job"].last_error
        mirror = repo.connection.execute("SELECT state, knowledge_applied, last_error FROM processing_jobs WHERE document_id='doc'").fetchone()
        assert tuple(mirror) == (parent_state, 1, expected["job"].last_error)
        assert tuple(repo.connection.execute("SELECT revision, pending FROM review_publications WHERE document_id='doc'").fetchone()) == expected["outbox"]
        assert json.loads((repo.config.spool_root / "doc" / "analysis_state.json").read_text())["state"] == parent_state
        assert service(repo).get_question("doc", "1").status == "incorrect"
        assert len(service(repo).history("doc", "1")) == 1
        retry = WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc")
        assert retry.state == ("needs_review" if parent_state == "needs_review" else "completed")
        assert repo.get_knowledge_stats("语文", "概括主要内容").exposure_count == 1
        assert len(service(repo).history("doc", "1")) == 1
    finally:
        other.close()


@pytest.mark.parametrize("remaining", [0, 1])
@pytest.mark.parametrize("erase_parent_flag", [False, True])
def test_completed_review_outbox_repairs_inconsistent_journals_on_direct_retry(repo, remaining, erase_parent_flag):
    seed(repo, ("needs_review",) * (remaining + 1))
    parent = service(repo)
    decision(parent, parent.list_pending()[0])
    old = repo.get_job("doc")
    payload = dict(old.payload, export_pending=True)
    if erase_parent_flag:
        payload.pop("parent_review")
    # Simulate journals damaged by an earlier stale ordinary callback while the
    # already-completed parent outbox and audited facts remain authoritative.
    with repo.connection:
        repo.connection.execute("UPDATE workflow_jobs SET state='pending', knowledge_applied=0, last_error='workflow_failed', payload_json=? WHERE job_id='doc'", (json.dumps(payload),))
        repo.connection.execute("UPDATE processing_jobs SET state='pending', knowledge_applied=0, last_error='workflow_failed' WHERE document_id='doc'")
    before = tuple(repo.connection.execute("SELECT revision, pending FROM review_publications WHERE document_id='doc'").fetchone())
    assert before == (1, 0)
    outcome = WorkflowController(repo.config, NeverAnalyze(), repo).retry_pending("doc")
    wanted = "needs_review" if remaining else "completed"
    assert outcome.state == wanted and outcome.error_code is None
    current = repo.get_job("doc")
    assert current.payload["parent_review"] and not current.payload["export_pending"] and current.knowledge_applied
    assert tuple(repo.connection.execute("SELECT state, knowledge_applied, last_error FROM processing_jobs WHERE document_id='doc'").fetchone()) == (wanted, 1, None)
    assert tuple(repo.connection.execute("SELECT revision, pending FROM review_publications WHERE document_id='doc'").fetchone()) == before
    assert json.loads((repo.config.spool_root / "doc" / "analysis_state.json").read_text())["state"] == wanted
    assert len(parent.history("doc", "1")) == 1
    assert repo.get_knowledge_stats("语文", "概括主要内容").exposure_count == 1


@pytest.mark.parametrize("old_failure", [False, True])
def test_ordinary_export_writeback_also_respects_a_new_ordinary_owner(repo, old_failure):
    seed(repo, ("needs_review",))
    initial = repo.get_job("doc")
    repo.save_workflow_job(replace(initial, payload=dict(initial.payload, export_pending=True)))
    other = KnowledgeRepository(repo.config)
    expected = {}
    class ChangedOwner:
        def export(self):
            current = other.get_job("doc")
            advanced = replace(current, state="pending", payload=dict(current.payload, ordinary_publication_id="next-owner"), last_error="new-owner-retry")
            other.save_workflow_job(advanced)
            expected["job"] = other.get_job("doc")
            if old_failure:
                raise OSError("stale failure")
            return repo.config.knowledge_root / "知识库首页.html"
    try:
        outcome = WorkflowController(repo.config, NeverAnalyze(), repo, dashboard=ChangedOwner()).retry_pending("doc")
        assert repo.get_job("doc") == expected["job"]
        assert outcome.state == "pending" and outcome.error_code == "new-owner-retry"
    finally:
        other.close()
