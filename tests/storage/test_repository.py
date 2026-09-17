from pathlib import Path
from dataclasses import asdict, replace
from datetime import datetime, timezone
import json

import pytest
import sqlite3

from qingzi_learning.config import AppConfig
from qingzi_learning.domain import (
    AnalysisResult,
    GradingMode,
    QuestionAnalysis,
    QuestionStatus,
    Subject,
)
from qingzi_learning.storage.repository import KnowledgeRepository


@pytest.fixture
def split_batch(repo, analysis):
    from qingzi_learning.domain import CapturedDocument, CapturedPage, PageSubjectAssignment
    from qingzi_learning.storage.repository import WorkflowJob
    from qingzi_learning.workflow.subject_split import build_subject_split_plan, split_analysis

    document = CapturedDocument("batch", (
        CapturedPage(2, repo.config.spool_root / "batch/page_002.jpg", "b" * 64),
        CapturedPage(5, repo.config.spool_root / "batch/page_005.jpg", "c" * 64),
    ), "试卷")
    result = replace(analysis, document_id="batch", page_subjects=(
        PageSubjectAssignment(2, Subject.MATH, .97, False, "计算"),
        PageSubjectAssignment(5, Subject.ENGLISH, .98, False, "阅读"),
    ), questions=(analysis.questions[0], replace(analysis.questions[0], page=5)))
    plan = build_subject_split_plan(document, result, {}, .85)
    payload = dict(analysis=asdict(result), split_plan=asdict(plan),
                   pages=[dict(page_number=p.page_number, path=str(p.path), sha256=p.sha256)
                          for p in document.pages], page_subject_overrides={},
                   child_document_ids=[], publish_state="not_started")
    parent = WorkflowJob("batch", "pending", None, False, None, json.loads(json.dumps(payload)))
    repo.save_workflow_job(parent)
    children = []
    for group, child_analysis in zip(plan.groups, split_analysis(result, plan)):
        directory = repo.config.knowledge_root / group.subject.value / "原始资料/2026/09" / group.document_id
        pages = tuple(replace(p, path=directory / p.path.name) for p in document.pages
                      if p.page_number in group.page_numbers)
        child_document = CapturedDocument(group.document_id, pages, "试卷")
        child_payload = dict(parent_job_id="batch", archive_kind="raw",
                             analysis=asdict(child_analysis),
                             pages=[dict(page_number=p.page_number, path=str(p.path), sha256=p.sha256)
                                    for p in pages])
        child = WorkflowJob(group.document_id, "pending", group.subject.value, False, None,
                            json.loads(json.dumps(child_payload)))
        children.append((child, child_document, child_analysis))
    return parent, tuple(children)


def test_apply_split_batch_rolls_back_all_children(repo, split_batch, monkeypatch):
    parent, children = split_batch
    original = repo._save_analysis
    calls = 0

    def fail_second(analysis):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("injected")
        original(analysis)

    monkeypatch.setattr(repo, "_save_analysis", fail_second)
    with pytest.raises(RuntimeError, match="injected"):
        repo.apply_split_batch(parent, children, now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert repo.get_job(parent.job_id) == parent
    for child, _, _ in children:
        assert repo.get_document(child.job_id) is None
        assert repo.get_job(child.job_id) is None
    for table in ("pages", "questions", "processing_jobs", "knowledge_stats"):
        assert repo.connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


def test_retrying_same_split_batch_uses_one_transaction_and_does_not_duplicate(repo, split_batch):
    parent, children = split_batch
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    statements = []
    repo.connection.set_trace_callback(statements.append)
    repo.apply_split_batch(parent, children, now=now)
    assert [s for s in statements if s.startswith("BEGIN")] == ["BEGIN IMMEDIATE"]
    assert statements.count("COMMIT") == 1
    before = repo.dashboard_snapshot()
    repo.apply_split_batch(parent, children, now=now)
    assert repo.dashboard_snapshot() == before
    assert repo.get_document(parent.job_id) is None
    assert repo.get_job(parent.job_id).payload["child_document_ids"] == ["batch--math", "batch--english"]
    assert repo.get_job(parent.job_id).payload["publish_state"] == "facts_applied"
    assert repo.get_job(parent.job_id).knowledge_applied
    for child, _, _ in children:
        stored = repo.get_job(child.job_id)
        assert stored.knowledge_applied and stored.state == "pending"
        assert stored.payload["export_pending"]
        assert repo.get_knowledge_stats(child.subject, "分数除法").exposure_count == 1


def test_split_rejects_stale_parent_and_noncanonical_children(repo, split_batch):
    parent, children = split_batch
    now = datetime(2026, 9, 15, tzinfo=timezone.utc)
    with pytest.raises(ValueError):
        repo.apply_split_batch(parent, children[:1], now=now)
    repo.save_workflow_job(replace(parent, payload=dict(parent.payload, page_subject_overrides={"5": "语文"})))
    with pytest.raises(ValueError):
        repo.apply_split_batch(parent, children, now=now)
    assert repo.get_document(children[0][0].job_id) is None


def test_split_recomputes_removed_and_new_knowledge_points(repo, split_batch):
    parent, children = split_batch
    child, document, result = children[0]
    repo.create_document(document, child.subject)
    repo.save_workflow_job(child)
    previous = replace(result, questions=(replace(result.questions[0], knowledge_points=("旧知识点",)),))
    repo.replace_analysis_and_recompute(previous, now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    repo.apply_split_batch(parent, children, now=datetime(2026, 9, 15, tzinfo=timezone.utc))
    assert repo.get_knowledge_stats(child.subject, "旧知识点").exposure_count == 0
    assert repo.get_knowledge_stats(child.subject, "分数除法").exposure_count == 1


@pytest.fixture
def repo(tmp_path: Path) -> KnowledgeRepository:
    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "app-data",
    )
    repository = KnowledgeRepository(config)
    yield repository
    repository.close()


@pytest.fixture
def analysis() -> AnalysisResult:
    return AnalysisResult(
        document_id="doc-20260915-001",
        subject=Subject.MATH,
        subject_confidence=0.96,
        document_type="试卷",
        grading_mode=GradingMode.MIXED,
        teacher_mark_evidence=("page_002: red cross",),
        questions=(
            QuestionAnalysis(
                question_id="4",
                question_type="application",
                page=2,
                prompt_summary="分数除法应用题",
                student_answer="12",
                reference_answer="18",
                status=QuestionStatus.INCORRECT,
                decision_source="teacher",
                knowledge_points=("分数除法", "应用题"),
                error_categories=("列式",),
                confidence=0.93,
                reason="单位1识别错误",
            ),
        ),
        summary="本次主要问题是单位1识别。",
    )


@pytest.fixture
def stored_analysis(repo: KnowledgeRepository, analysis: AnalysisResult) -> AnalysisResult:
    repo.create_document(
        document_id=analysis.document_id,
        subject=analysis.subject.value,
        document_type=analysis.document_type,
        pages=((2, "C:/spool/page_002.jpg", "b" * 64),),
    )
    return analysis


def test_saving_same_analysis_twice_does_not_duplicate_questions(
    repo: KnowledgeRepository, stored_analysis: AnalysisResult
) -> None:
    """Removing replacement semantics would inflate the visible question count on retry."""
    repo.save_analysis(stored_analysis)
    repo.save_analysis(stored_analysis)

    assert repo.count_questions(stored_analysis.document_id) == len(stored_analysis.questions)


def test_saving_analysis_replaces_old_question_knowledge_point_links(
    repo: KnowledgeRepository, stored_analysis: AnalysisResult
) -> None:
    """Skipping link deletion would retain knowledge points absent from the newer analysis."""
    repo.save_analysis(stored_analysis)
    replacement = AnalysisResult(
        document_id=stored_analysis.document_id,
        subject=stored_analysis.subject,
        subject_confidence=stored_analysis.subject_confidence,
        document_type=stored_analysis.document_type,
        grading_mode=stored_analysis.grading_mode,
        teacher_mark_evidence=stored_analysis.teacher_mark_evidence,
        questions=(
            QuestionAnalysis(
                question_id="4",
                question_type="calculation",
                page=2,
                prompt_summary="分数除法应用题",
                student_answer="12",
                reference_answer="18",
                status=QuestionStatus.INCORRECT,
                decision_source="teacher",
                knowledge_points=("分数除法",),
                error_categories=("列式",),
                confidence=0.93,
                reason="单位1识别错误",
            ),
        ),
        summary="修正后的分析。",
    )
    repo.save_analysis(replacement)

    document = repo.get_document(stored_analysis.document_id)
    assert document is not None
    assert document["questions"][0]["knowledge_points"] == ["分数除法"]
    assert document["questions"][0]["question_type"] == "calculation"


def test_saving_analysis_rejects_question_without_captured_page_and_rolls_back(
    repo: KnowledgeRepository, analysis: AnalysisResult
) -> None:
    """Removing the page foreign key would save a question that cannot be traced to evidence."""
    repo.create_document(
        document_id=analysis.document_id,
        subject=analysis.subject.value,
        document_type=analysis.document_type,
        pages=((1, "C:/spool/page_001.jpg", "a" * 64),),
    )

    with pytest.raises(sqlite3.IntegrityError):
        repo.save_analysis(analysis)

    document = repo.get_document(analysis.document_id)
    assert document is not None
    assert document["questions"] == []
    assert document["pages"] == [
        {"page_number": 1, "path": "C:/spool/page_001.jpg", "sha256": "a" * 64}
    ]


def test_saving_analysis_removes_existing_job_from_pending_list(
    repo: KnowledgeRepository, stored_analysis: AnalysisResult
) -> None:
    """Leaving the conflict state untouched would keep a successfully analyzed job pending."""
    assert [job["document_id"] for job in repo.list_pending()] == [
        stored_analysis.document_id
    ]

    repo.save_analysis(stored_analysis)

    assert stored_analysis.document_id not in {
        job["document_id"] for job in repo.list_pending()
    }


def test_recapturing_analyzed_document_replaces_pages_and_clears_analysis(
    repo: KnowledgeRepository, stored_analysis: AnalysisResult
) -> None:
    """Restrictive page deletion would make a recapture fail after analysis is saved."""
    repo.save_analysis(stored_analysis)

    repo.create_document(
        document_id=stored_analysis.document_id,
        subject=stored_analysis.subject.value,
        document_type=stored_analysis.document_type,
        pages=((3, "C:/spool/page_003-recapture.jpg", "c" * 64),),
    )

    document = repo.get_document(stored_analysis.document_id)
    assert document is not None
    assert document["pages"] == [
        {
            "page_number": 3,
            "path": "C:/spool/page_003-recapture.jpg",
            "sha256": "c" * 64,
        }
    ]
    assert document["questions"] == []
    assert repo.connection.execute(
        "SELECT COUNT(*) FROM question_knowledge_points WHERE document_id = ?",
        (stored_analysis.document_id,),
    ).fetchone()[0] == 0
    assert [job["document_id"] for job in repo.list_pending()] == [
        stored_analysis.document_id
    ]


def test_create_document_upserts_pages_and_pending_job(repo: KnowledgeRepository) -> None:
    """Without the document transaction, a retry would create duplicate recovery jobs."""
    repo.create_document(
        document_id="doc-20260915-002",
        subject="语文",
        document_type="作业",
        pages=((1, "C:/spool/page_001.jpg", "a" * 64),),
    )
    repo.create_document(
        document_id="doc-20260915-002",
        subject="语文",
        document_type="作业",
        pages=((1, "C:/spool/page_001.jpg", "a" * 64),),
    )

    pending = repo.list_pending()
    assert len(pending) == 1
    assert pending[0]["document_id"] == "doc-20260915-002"
