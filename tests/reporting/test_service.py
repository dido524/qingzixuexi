from datetime import datetime, timezone
from pathlib import Path
from contextlib import contextmanager

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.reporting.narrative import LocalNarrativeProvider, NarrativeService
from qingzi_learning.storage.repository import KnowledgeRepository


UTC = timezone.utc


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


class _FailingNarrative:
    def generate(self, _profile):
        raise RuntimeError("offline")


def _narratives() -> NarrativeService:
    return NarrativeService(_FailingNarrative(), LocalNarrativeProvider())


def test_service_publishes_immutable_report_and_latest_alias(repo: KnowledgeRepository) -> None:
    from qingzi_learning.reporting.service import LearningReportService

    ids = iter(("report-first", "report-second"))
    service = LearningReportService(repo, narrative_service=_narratives(), id_factory=lambda _now: next(ids))

    first = service.generate(now=datetime(2026, 9, 18, 8, 0, tzinfo=UTC))
    first_child = first.child_path.read_text("utf-8")
    second = service.generate(now=datetime(2026, 9, 18, 9, 0, tzinfo=UTC))

    assert first.child_path.exists() and first.parent_path.exists() and first.manifest_path.exists()
    assert first.child_path.read_text("utf-8") == first_child
    assert second.latest_path.read_text("utf-8") == second.child_path.read_text("utf-8")
    runs = service.history()
    assert [run.report_id for run in runs] == ["report-second", "report-first"]
    assert runs[0].previous_report_id == "report-first"
    assert runs[0].status == "completed"
    assert runs[0].narrative["source"] == "local_template"


def test_service_does_not_replace_latest_when_publication_fails(
    repo: KnowledgeRepository, monkeypatch
) -> None:
    from qingzi_learning.reporting.service import LearningReportService

    ids = iter(("report-good", "report-failed"))
    service = LearningReportService(repo, narrative_service=_narratives(), id_factory=lambda _now: next(ids))
    good = service.generate(now=datetime(2026, 9, 18, 8, 0, tzinfo=UTC))
    previous = good.latest_path.read_text("utf-8")

    def fail_publish(*_args, **_kwargs):
        raise OSError("injected publication failure")

    monkeypatch.setattr(service, "_publish", fail_publish)
    with pytest.raises(OSError, match="injected"):
        service.generate(now=datetime(2026, 9, 18, 9, 0, tzinfo=UTC))

    assert good.latest_path.read_text("utf-8") == previous
    assert repo.latest_completed_report().report_id == "report-good"
    failed = repo.get_report_run("report-failed")
    assert failed is not None and failed.status == "failed"
    assert failed.error_code == "report_generation_failed"


def test_publication_rejects_a_tampered_staged_report(
    repo: KnowledgeRepository, monkeypatch
) -> None:
    from qingzi_learning.reporting import service as service_module
    from qingzi_learning.reporting.service import LearningReportService

    @contextmanager
    def tampering_lock(root):
        staged = next(root.rglob("*.part"))
        staged.write_text("tampered", encoding="utf-8")
        yield

    monkeypatch.setattr(service_module, "publication_lock", tampering_lock)
    service = LearningReportService(
        repo,
        narrative_service=_narratives(),
        id_factory=lambda _now: "report-tampered",
    )

    with pytest.raises(ValueError, match="暂存"):
        service.generate(now=datetime(2026, 9, 18, 8, 0, tzinfo=UTC))

    assert repo.get_report_run("report-tampered").status == "failed"
    assert not repo.config.knowledge_root.joinpath("学习报告", "最新学情报告.html").exists()
