from datetime import datetime, timezone
from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.storage.repository import KnowledgeRepository

@pytest.fixture
def repo(tmp_path: Path):
    config = AppConfig(
        knowledge_root=tmp_path / "knowledge",
        subjects=("语文", "数学", "英语"),
        camera_vid=1,
        camera_pid=2,
        spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "data",
    )
    value = KnowledgeRepository(config)
    yield value
    value.close()


def _blueprint() -> dict:
    return {
        "schema_version": 1,
        "request": {},
        "allocation": {"primary": 5, "related": 0, "stable": 0},
        "allocation_note": "test",
        "targets": [{"knowledge_point": "分数应用", "category": "primary"}],
        "slots": [
            {
                "slot": number,
                "category": "primary",
                "subject": "数学",
                "knowledge_point": "分数应用",
                "evidence_id": "kp-1",
                "difficulty": "适中",
            }
            for number in range(1, 6)
        ],
    }


def valid_generation() -> dict:
    return {
        "title": "五年级数学针对性练习",
        "instructions": "认真读题，写出必要步骤。",
        "questions": [
            {
                "question_id": f"Q{index:02d}",
                "question_type": "application",
                "points": 20,
                "knowledge_points": ["分数应用"],
                "blueprint_category": "primary",
                "prompt": f"第{index}个新的分数应用情境题，请列式计算。",
                "answer": f"{index}/10",
                "explanation": f"先找单位一，再计算第{index}题。",
                "rubric": "列式正确10分，计算正确10分。",
            }
            for index in range(1, 6)
        ],
    }


class Builder:
    def build(self, _request):
        return _blueprint()


class Generator:
    def __init__(self, values):
        self.values = iter(values)
        self.calls = 0

    def generate(self, _exam_id, _request, _blueprint):
        self.calls += 1
        return next(self.values)


class Verifier:
    def __init__(self, approved=True):
        self.approved = approved

    def verify(self, _request, _blueprint, generation):
        return {
            "approved": self.approved,
            "issues": [] if self.approved else ["题意不清"],
            "question_verdicts": [
                {"question_id": item["question_id"], "valid": self.approved, "issues": []}
                for item in generation.get("questions", [])
            ],
        }


def test_service_repairs_once_then_requires_parent_approval(repo) -> None:
    from qingzi_learning.exams.service import TargetedExamService

    bad = valid_generation()
    bad["questions"][0]["prompt"] = "答案：泄露"
    generator = Generator([bad, valid_generation()])
    service = TargetedExamService(repo, Builder(), generator, Verifier())

    run = service.create_draft(
        ExamRequest("数学", "分数", 40, "适中", 5, False, False),
        now=datetime(2026, 9, 18, 8, 30, tzinfo=timezone.utc),
    )

    assert generator.calls == 2
    assert run.status == "needs_parent_approval"
    assert len(repo.exam_questions(run.exam_id)) == 5


def test_service_fails_closed_after_one_unsuccessful_repair(repo) -> None:
    from qingzi_learning.exams.service import ExamGenerationError, TargetedExamService

    bad = valid_generation()
    bad["questions"][0]["knowledge_points"] = ["伪造知识点"]
    generator = Generator([bad, bad])
    service = TargetedExamService(repo, Builder(), generator, Verifier())

    with pytest.raises(ExamGenerationError, match="exam_validation_failed"):
        service.create_draft(
            ExamRequest("数学", "分数", 40, "适中", 5, False, False),
            now=datetime(2026, 9, 18, tzinfo=timezone.utc),
        )

    runs = repo.list_exam_runs()
    assert len(runs) == 1 and runs[0].status == "failed"
    assert generator.calls == 2


@pytest.mark.parametrize("code", [
    "deepseek_api_key_missing", "deepseek_auth_failed",
    "deepseek_rate_limited", "deepseek_unavailable",
])
def test_provider_failure_is_not_retried_as_a_quality_repair(repo, code) -> None:
    from qingzi_learning.analysis.codex_cli import AnalysisError
    from qingzi_learning.exams.service import ExamGenerationError, TargetedExamService

    class FailingGenerator:
        def __init__(self): self.calls = 0
        def generate(self, *_args):
            self.calls += 1
            raise AnalysisError(code)
        def repair(self, *_args):
            self.calls += 1
            raise AssertionError("provider failure must not be retried")

    generator = FailingGenerator()
    service = TargetedExamService(repo, Builder(), generator, Verifier())
    with pytest.raises(ExamGenerationError) as caught:
        service.create_draft(
            ExamRequest("数学", "分数", 40, "适中", 5, False, False),
            now=datetime(2026, 9, 18, tzinfo=timezone.utc),
        )
    assert caught.value.code == code
    assert generator.calls == 1


def test_service_supplies_validator_feedback_to_repair(repo) -> None:
    from qingzi_learning.exams.service import TargetedExamService

    bad = valid_generation()
    bad["questions"][0]["knowledge_points"] = ["伪造知识点"]

    class RepairingGenerator:
        def __init__(self): self.issues = None
        def generate(self, *_args): return bad
        def repair(self, _exam_id, _request, _blueprint, issues):
            self.issues = issues
            return valid_generation()

    generator = RepairingGenerator()
    run = TargetedExamService(repo, Builder(), generator, Verifier()).create_draft(
        ExamRequest("数学", "分数", 40, "适中", 5, False, False)
    )

    assert run.status == "needs_parent_approval"
    assert generator.issues and "知识点" in generator.issues[0]
