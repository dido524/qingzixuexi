"""Parent decisions must change effective facts without destroying original evidence."""
from dataclasses import asdict, replace
from datetime import datetime, timezone
from pathlib import Path
import json

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult, GradingMode, PageSubjectAssignment, QuestionAnalysis,
    QuestionStatus, QuestionType, Subject,
)
from qingzi_learning.knowledge.updater import KnowledgeUpdater
from qingzi_learning.storage.repository import KnowledgeRepository, WorkflowJob
from tests.workflow.test_controller import mixed_setup, controller_for, _confirm_mixed


@pytest.mark.parametrize("sibling_review", [False, True])
def test_reviewing_split_math_child_preserves_english_and_refreshes_parent_aggregate(mixed_setup, monkeypatch, sibling_review):
    controller = controller_for(mixed_setup)
    _, repository, analyzer, session = mixed_setup
    analyze = analyzer.analyze
    def reviewable(document):
        result = analyze(document)
        questions = (replace(result.questions[0], status=QuestionStatus.NEEDS_REVIEW),)
        if sibling_review:
            questions += (replace(questions[0], question_id="english-1", page=4),)
        return replace(result, questions=questions)
    monkeypatch.setattr(analyzer, "analyze", reviewable)
    result = _confirm_mixed(controller, session)
    math_id, english_id = result.child_document_ids
    before = repository.get_document(english_id)
    sibling_job = repository.get_job(english_id)
    assert repository.get_job(result.job_id).state == "needs_review"
    english_mirror = Path(sibling_job.payload["mirror_path"]).read_bytes()
    controller.review.confirm_question(math_id, "1", "incorrect", "42", "家长确认")
    assert repository.get_document(english_id) == before
    assert repository.get_job(english_id) == sibling_job
    assert Path(sibling_job.payload["mirror_path"]).read_bytes() == english_mirror
    parent_state = "needs_review" if sibling_review else "completed"
    assert repository.get_job(result.job_id).state == parent_state
    parent_mirror = json.loads((session.session_dir / "analysis_state.json").read_text("utf-8"))
    assert parent_mirror["document_id"] == result.job_id and parent_mirror["state"] == parent_state
    assert repository.get_document(result.job_id) is None
    assert repository.get_job(math_id).state == "completed"
    assert controller.review.pending_publications() == ()
    assert controller.publication.current()
    # Review-owned child retries still verify the whole source batch, while
    # retaining the parent's effective decision and separate publication owner.
    image = Path(sibling_job.payload["pages"][0]["path"])
    original = image.read_bytes()
    image.write_bytes(b"damaged sibling source")
    assert controller.retry_pending(math_id).error_code == "workflow_failed"
    image.write_bytes(original)
    assert controller.retry_pending(math_id).state == "completed"
    assert repository.get_document(math_id)["questions"][0]["status"] == "incorrect"
    assert analyzer.calls == 1


@pytest.fixture
def repo(tmp_path):
    config = AppConfig(knowledge_root=tmp_path / "kb", subjects=("语文", "数学", "英语"),
                       camera_vid=1, camera_pid=2, spool_root=tmp_path / "spool", app_data_root=tmp_path / "data")
    repo = KnowledgeRepository(config)
    yield repo
    repo.close()


def seed(repo, statuses=("needs_review", "needs_review", "correct"), document_id="doc", subject=Subject.CHINESE):
    page = repo.config.knowledge_root / subject.value / "原始资料" / document_id / "page_001.jpg"
    page.parent.mkdir(parents=True, exist_ok=True)
    page.write_bytes(b"source evidence")
    session = repo.config.spool_root / document_id
    session.mkdir(parents=True, exist_ok=True)
    result = AnalysisResult(document_id, subject, .99, "试卷", GradingMode.MIXED, ("老师第3题打勾",), tuple(
        QuestionAnalysis(str(i), QuestionType.READING_OPEN, 1, "概括事件", "学生原答案", "参考原答案", QuestionStatus(s),
                         "teacher" if i == 3 else "model", ("概括主要内容",), ("要点遗漏",), .95 if i == 3 else .60, "系统原理由")
        for i, s in enumerate(statuses, 1)), "原始总结",
        (PageSubjectAssignment(1, subject, .99, False, "阅读理解题"),))
    repo.create_document(document_id, subject.value, "试卷", [(1, str(page), "a" * 64)])
    payload = dict(session_dir=str(session), pages=[dict(page_number=1, path=str(page), sha256="a" * 64)],
                   archive_kind="raw", analysis=asdict(result), export_pending=False,
                   version=1, date="2026-09", assets=[], cleanup=[], subject_confirmed=True)
    repo.save_workflow_job(WorkflowJob(document_id, "needs_review" if "needs_review" in statuses else "completed", subject.value, False, None, payload))
    KnowledgeUpdater(repo).apply(result)
    return result


def service(repo, **kwargs):
    from qingzi_learning.review.service import ReviewService
    return ReviewService(repo, **kwargs)


def decision(svc, item, status="incorrect", **kwargs):
    return svc.confirm_question(item.document_id, item.question_id, status, "人物、事件、结果", "家长对照答案确认",
                                expected_version=item.version, **kwargs)


def test_review_changes_mastery_and_exports_but_preserves_original_evidence(repo):
    original = seed(repo)
    svc = service(repo)
    first, second = svc.list_pending()
    assert first.source_path.exists() and first.student_answer == "学生原答案"
    assert first.status == "needs_review" and first.confidence == .60
    before = repo.get_knowledge_stats("语文", "概括主要内容")
    assert (before.exposure_count, before.correct_count, before.needs_review) == (1, 1, 2)
    decision(svc, first)
    after = repo.get_knowledge_stats("语文", "概括主要内容")
    assert (after.exposure_count, after.incorrect_count, after.needs_review) == (2, 1, 1)
    assert (after.teacher_correct_count, after.model_incorrect_count) == (1, 0)
    assert repo.get_job("doc").state == "needs_review"
    assert repo.connection.execute("SELECT state FROM processing_jobs").fetchone()[0] == "needs_review"
    q = repo.get_document("doc")["questions"][0]
    assert (q["status"], q["decision_source"], q["confidence"]) == ("incorrect", "parent", .60)
    assert q["original_status"] == "needs_review" and q["original_decision_source"] == "model"
    assert repo.get_job("doc").payload["analysis"] == json.loads(json.dumps(asdict(original)))
    assert "家长对照答案确认" in svc.markdown.document_path("语文", "doc").read_text(encoding="utf-8")
    assert repo.dashboard_snapshot()["summary"]["review_count"] == 1
    decision(svc, second, "partial")
    assert repo.get_job("doc").state == "completed"
    assert repo.connection.execute("SELECT state FROM processing_jobs").fetchone()[0] == "completed"
    assert '"state": "completed"' in (repo.config.spool_root / "doc" / "analysis_state.json").read_text()
    assert svc.list_pending() == ()
    assert repo.dashboard_snapshot()["subjects"]["语文"]["mastery_rate"] == .5


def test_duplicate_confirmation_and_stale_edit_and_correct_incorrect_reversal(repo):
    seed(repo, ("needs_review",))
    svc = service(repo)
    item = svc.list_pending()[0]
    decision(svc, item)
    decision(svc, item)
    assert len(svc.history("doc", "1")) == 1
    assert repo.get_knowledge_stats("语文", "概括主要内容").exposure_count == 1
    with pytest.raises(ValueError, match="已变化"):
        decision(svc, item, "correct")
    fresh = svc.get_question("doc", "1")
    decision(svc, fresh, "correct")
    stats = repo.get_knowledge_stats("语文", "概括主要内容")
    assert (stats.exposure_count, stats.correct_count, stats.incorrect_count, stats.common_error_categories) == (1, 1, 0, ())
    assert repo.dashboard_snapshot()["summary"]["error_count"] == 0
    decision(svc, svc.get_question("doc", "1"), "incorrect")
    assert len(svc.history("doc", "1")) == 3
    assert repo.dashboard_snapshot()["summary"]["error_count"] == 1


def test_invalid_final_status_unknown_question_and_missing_version_rejected(repo):
    seed(repo, ("needs_review",))
    svc = service(repo)
    with pytest.raises(ValueError, match="最终状态"):
        svc.confirm_question("doc", "1", "needs_review", "", "")
    with pytest.raises(ValueError, match="不存在"):
        svc.confirm_question("doc", "missing", "incorrect", "", "")
    svc.confirm_question("doc", "1", "correct", "", "")
    with pytest.raises(ValueError, match="重新打开"):
        svc.confirm_question("doc", "1", "incorrect", "", "")


def test_recompute_failure_rolls_back_review_audit_and_both_job_journals(repo, monkeypatch):
    seed(repo, ("needs_review",))
    svc = service(repo)
    item = svc.list_pending()[0]
    def fail(*args): raise RuntimeError("injected recompute failure")
    monkeypatch.setattr(repo, "_recompute_knowledge_stat", fail)
    with pytest.raises(RuntimeError): decision(svc, item)
    assert svc.get_question("doc", "1").status == "needs_review"
    assert svc.history("doc", "1") == ()
    assert repo.get_job("doc").state == "needs_review"
    assert repo.connection.execute("SELECT state FROM processing_jobs").fetchone()[0] == "needs_review"


def test_export_failure_is_restart_recoverable_without_reapplying_analysis(repo):
    original = seed(repo, ("needs_review",))
    class BrokenDashboard:
        def export(self): raise OSError("private failure text")
    svc = service(repo, dashboard=BrokenDashboard())
    summary = decision(svc, svc.list_pending()[0])
    assert summary.document_id == "doc"
    job = repo.get_job("doc")
    assert job.state == "pending" and job.payload["export_pending"] and job.last_error == "review_export_failed"
    assert repo.connection.execute("SELECT state FROM processing_jobs").fetchone()[0] == "pending"
    assert repo.get_knowledge_stats("语文", "概括主要内容").incorrect_count == 1
    reopened = KnowledgeRepository(repo.config)
    try:
        recovered = service(reopened)
        assert recovered.pending_publications() == ("doc",)
        recovered.retry_publication("doc")
        assert reopened.get_job("doc").state == "completed"
        assert not reopened.get_job("doc").payload["export_pending"]
        assert reopened.get_job("doc").payload["analysis"] == json.loads(json.dumps(asdict(original)))
        assert len(recovered.history("doc", "1")) == 1
        assert recovered.pending_publications() == ()
    finally:
        reopened.close()


def test_reviews_of_other_subjects_and_teacher_evidence_are_independent(repo):
    seed(repo, ("needs_review",), "chinese")
    seed(repo, ("needs_review",), "math", Subject.MATH)
    svc = service(repo)
    decision(svc, svc.get_question("math", "1"), "correct")
    assert repo.get_knowledge_stats("语文", "概括主要内容").exposure_count == 0
    assert repo.get_knowledge_stats("数学", "概括主要内容").correct_count == 1
    seed(repo, ("correct", "correct", "correct"), "teacher")
    teacher = svc.get_question("teacher", "3")
    decision(svc, teacher, "incorrect")
    q = repo.get_document("teacher")["questions"][2]
    assert q["original_decision_source"] == "teacher" and q["original_status"] == "correct"
    assert q["decision_source"] == "parent"


def test_two_repository_instances_cannot_overwrite_stale_review(repo):
    seed(repo, ("needs_review",))
    second = KnowledgeRepository(repo.config)
    try:
        a, b = service(repo), service(second)
        old = b.get_question("doc", "1")
        decision(a, a.get_question("doc", "1"), "correct")
        with pytest.raises(ValueError, match="已变化"):
            decision(b, old, "incorrect")
        assert b.get_question("doc", "1").status == "correct"
    finally:
        second.close()


@pytest.mark.parametrize("restart", [False, True])
def test_controller_retry_prioritizes_parent_publication_without_model_or_old_status(repo, restart):
    from qingzi_learning.workflow.controller import WorkflowController
    seed(repo, ("needs_review",))
    class BrokenDashboard:
        def export(self): raise OSError("injected")
    svc = service(repo, dashboard=BrokenDashboard())
    decision(svc, svc.list_pending()[0])
    class NeverAnalyze:
        def analyze_document(self, *args): raise AssertionError("parent review must not invoke model")
    controller = WorkflowController(repo.config, NeverAnalyze(), repo)
    if restart:
        assert controller.recover_jobs()[0].state == "pending"
    outcome = controller.retry_pending("doc")
    assert outcome.state == "completed" and not outcome.error_code
    assert repo.get_document("doc")["questions"][0]["status"] == "incorrect"
    assert repo.pending_review_publications() == ()
    assert repo.get_knowledge_stats("语文", "概括主要内容").exposure_count == 1


def test_reapplying_model_analysis_cannot_erase_parent_review(repo):
    original = seed(repo, ("needs_review",))
    svc = service(repo)
    decision(svc, svc.list_pending()[0])
    with pytest.raises(ValueError, match="已有家长复核"):
        KnowledgeUpdater(repo).apply(original)
    assert svc.get_question("doc", "1").status == "incorrect"
    assert len(svc.history("doc", "1")) == 1


def test_journal_cannot_complete_while_review_questions_remain(repo):
    from dataclasses import replace
    seed(repo, ("needs_review",))
    with pytest.raises(ValueError, match="待确认"):
        repo.save_workflow_job(replace(repo.get_job("doc"), state="completed"))
    assert repo.get_job("doc").state == "needs_review"


def test_parent_decision_counts_in_recent_subject_trend_despite_low_model_confidence(repo):
    seed(repo, ("needs_review",))
    svc = service(repo)
    decision(svc, svc.list_pending()[0], "correct")
    trend = repo.dashboard_snapshot()["subjects"]["语文"]["recent_trend"]
    assert (trend["question_sample_size"], trend["mastery_rate"]) == (1, 1.0)


def test_original_confidence_and_parent_provenance_visible_in_dashboard(repo):
    seed(repo, ("needs_review",))
    svc = service(repo)
    decision(svc, svc.list_pending()[0])
    html = (repo.config.knowledge_root / "知识库首页.html").read_text(encoding="utf-8")
    assert "家长复核" in html
    assert repo.dashboard_snapshot()["error_bank"][0]["decision_source"] == "parent"


def test_delayed_export_failure_cannot_reopen_already_published_review(repo):
    seed(repo, ("needs_review",))
    svc = service(repo)
    decision(svc, svc.list_pending()[0])
    # A failing exporter released its transaction; another process has already
    # published the durable entry before the first process reports its failure.
    repo.mark_review_export_failed("doc")
    assert repo.get_job("doc").state == "completed"
    assert not repo.get_job("doc").payload["export_pending"]
    assert svc.pending_publications() == ()


def test_publication_event_version_advances_when_retry_finishes_same_review(repo):
    seed(repo, ("needs_review",))
    class BrokenDashboard:
        def export(self): raise OSError("injected")
    svc = service(repo, dashboard=BrokenDashboard())
    decision(svc, svc.list_pending()[0])
    pending_version = repo.review_publication_revision("doc")
    service(repo).retry_publication("doc")
    # Delayed pending results for this same parent decision must sort before
    # the successful retry, even though the decision itself was not edited.
    assert repo.review_publication_revision("doc") > pending_version


def test_reclassified_knowledge_point_invalidates_open_review_even_with_same_answer(repo):
    from dataclasses import replace
    original = seed(repo, ("needs_review",))
    svc = service(repo)
    old = svc.list_pending()[0]
    changed = replace(original, questions=(replace(original.questions[0], knowledge_points=("另一知识点",)),))
    KnowledgeUpdater(repo).apply(changed)
    with pytest.raises(ValueError, match="已变化"):
        decision(svc, old)
    assert repo.get_knowledge_stats("语文", "另一知识点").exposure_count == 0
    assert svc.history("doc", "1") == ()
