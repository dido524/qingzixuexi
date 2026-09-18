from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.storage.repository import KnowledgeRepository


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


def test_report_run_lifecycle_is_immutable_after_completion(repo: KnowledgeRepository) -> None:
    snapshot = {"subjects": {"数学": {"question_count": 12}}}
    created = repo.create_report_run(
        "report-20260918-001", None, "2026-09-18T00:00:00Z", snapshot
    )

    assert created.status == "generating"
    assert created.snapshot == snapshot
    assert created.narrative == {}
    assert created.output_files == {}

    completed = repo.complete_report_run(
        created.report_id,
        {"summary": "本阶段正在逐步形成稳定表现。"},
        {"child": "学习报告/孩子版.html", "parent": "学习报告/家长版.html"},
    )

    assert completed.status == "completed"
    assert completed.completed_at is not None
    assert completed.narrative["summary"] == "本阶段正在逐步形成稳定表现。"
    assert completed.output_files["child"] == "学习报告/孩子版.html"
    with pytest.raises(ValueError, match="已完成"):
        repo.complete_report_run(completed.report_id, {}, {})


def test_latest_completed_report_ignores_failed_and_generating_runs(
    repo: KnowledgeRepository,
) -> None:
    old = repo.create_report_run(
        "report-old", None, "2026-09-17T00:00:00Z", {"version": 1}
    )
    repo.complete_report_run(old.report_id, {"source": "local_template"}, {"child": "old.html"})

    failed = repo.create_report_run(
        "report-failed", old.report_id, "2026-09-18T00:00:00Z", {"version": 2}
    )
    repo.fail_report_run(failed.report_id, "narrative_failed")
    repo.create_report_run(
        "report-generating", old.report_id, "2026-09-18T01:00:00Z", {"version": 3}
    )

    latest = repo.latest_completed_report()
    assert latest is not None
    assert latest.report_id == old.report_id
    assert [run.report_id for run in repo.list_report_runs()] == [
        "report-generating",
        "report-failed",
        "report-old",
    ]


def test_report_failure_is_durable_and_cannot_later_publish(
    repo: KnowledgeRepository,
) -> None:
    run = repo.create_report_run(
        "report-failure", None, "2026-09-18T00:00:00Z", {"version": 1}
    )
    failed = repo.fail_report_run(run.report_id, "publication_failed")

    assert failed.status == "failed"
    assert failed.error_code == "publication_failed"
    assert repo.get_report_run(run.report_id) == failed
    with pytest.raises(ValueError, match="失败"):
        repo.complete_report_run(run.report_id, {}, {})


def test_report_previous_id_must_reference_an_existing_run(repo: KnowledgeRepository) -> None:
    with pytest.raises(ValueError, match="上一份报告"):
        repo.create_report_run(
            "report-orphan", "missing", "2026-09-18T00:00:00Z", {"version": 1}
        )
