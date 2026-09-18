import json
from pathlib import Path

import pytest

from qingzi_learning.config import AppConfig
from qingzi_learning.exams.service import TargetedExamService
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


def _created_run(repo: KnowledgeRepository, *, generated: bool):
    run = repo.create_exam_run(
        "QZ-MATH-20260918-SERVICE",
        "数学",
        {"scope": "分数", "duration_minutes": 40, "difficulty": "适中"},
        {
            "allocation": {"primary": 1, "related": 0, "stable": 0},
            "allocation_note": "关联证据不足。",
            "targets": [{"knowledge_point": "分数应用", "category": "primary"}],
        },
    )
    if generated:
        run = repo.save_exam_generation(
            run.exam_id,
            {"title": "五年级数学针对性练习", "instructions": "认真答题"},
            {"approved": True, "issues": []},
            [{
                "question_id": "Q01", "question_type": "application", "points": 100,
                "knowledge_points": ["分数应用"], "blueprint_category": "primary",
                "prompt": "新题目", "answer": "答案内容", "explanation": "解析内容",
                "rubric": "评分标准",
            }],
        )
    return run


def test_unvalidated_exam_cannot_publish(repo: KnowledgeRepository) -> None:
    run = _created_run(repo, generated=False)
    service = TargetedExamService(repo)

    with pytest.raises(ValueError, match="家长确认|校验"):
        service.approve(run.exam_id, expected_revision=run.revision)


def test_approval_publishes_five_guarded_outputs_and_hash_manifest(repo) -> None:
    run = _created_run(repo, generated=True)
    service = TargetedExamService(repo)

    artifacts = service.approve(run.exam_id, expected_revision=run.revision)

    approved = repo.get_exam_run(run.exam_id)
    assert approved.status == "approved"
    assert {path.name for path in (
        artifacts.student_path, artifacts.answer_sheet_path,
        artifacts.solutions_path, artifacts.blueprint_path,
    )} == {"学生试卷.html", "答题纸.html", "答案与解析.html", "组卷说明.html"}
    assert all(path.is_file() for path in artifacts.__dict__.values())
    manifest = json.loads(artifacts.manifest_path.read_text("utf-8"))
    assert set(manifest["sha256"]) == {"student", "answer_sheet", "solutions", "blueprint"}
    assert "答案内容" not in artifacts.student_path.read_text("utf-8")
    assert "答案内容" in artifacts.solutions_path.read_text("utf-8")


def test_approval_rejects_stale_preview_without_writing(repo) -> None:
    run = _created_run(repo, generated=True)
    service = TargetedExamService(repo)

    with pytest.raises(ValueError, match="重新预览"):
        service.approve(run.exam_id, expected_revision=run.revision - 1)

    assert not (repo.config.knowledge_root / "模拟试卷").exists()
