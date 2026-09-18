import json
from pathlib import Path
import subprocess
from importlib.resources import files

from qingzi_learning.exams.blueprint import ExamRequest


REQUEST = ExamRequest("数学", "分数", 40, "适中", 5, False, False)
BLUEPRINT = {
    "targets": [{"knowledge_point": "分数应用", "evidence_id": "kp-1"}],
    "slots": [{"slot": 1, "knowledge_point": "分数应用", "category": "primary"}],
}


class RecordingRunner:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.args = None
        self.prompt = ""

    def run(self, args, **kwargs):
        self.args = args
        self.prompt = kwargs["input"]
        Path(args[args.index("--output-last-message") + 1]).write_text(
            json.dumps(self.payload, ensure_ascii=False), "utf-8"
        )
        return subprocess.CompletedProcess(args, 0, "", "")


def test_generator_uses_bounded_read_only_schema_contract(tmp_path: Path) -> None:
    from qingzi_learning.exams.generator import ExamGenerator

    payload = {"title": "练习", "instructions": "说明", "questions": []}
    runner = RecordingRunner(payload)
    result = ExamGenerator(
        runner=runner, resolver=lambda: "C:/safe/codex.exe", temporary_root=tmp_path
    ).generate("QZ-MATH-1", REQUEST, BLUEPRINT)

    assert result == payload
    assert runner.args[runner.args.index("--sandbox") + 1] == "read-only"
    assert runner.args[runner.args.index("--output-schema") + 1].endswith(
        "exam-generation.schema.json"
    )
    assert "只生成全新的题目" in runner.prompt
    assert "student_answer" not in runner.prompt
    assert "只能逐字复制" in runner.prompt


def test_repair_passes_only_bounded_validator_issues(tmp_path: Path) -> None:
    from qingzi_learning.exams.generator import ExamGenerator

    payload = {"title": "练习", "instructions": "说明", "questions": []}
    runner = RecordingRunner(payload)
    generator = ExamGenerator(
        runner=runner, resolver=lambda: "C:/safe/codex.exe", temporary_root=tmp_path
    )

    generator.repair("QZ-MATH-1", REQUEST, BLUEPRINT, ["知识点越界"] * 30)

    assert "repair_issues" in runner.prompt
    assert runner.prompt.count("知识点越界") == 20


def test_verifier_has_independent_read_only_contract(tmp_path: Path) -> None:
    from qingzi_learning.exams.generator import ExamVerifier

    payload = {
        "approved": True,
        "issues": [],
        "question_verdicts": [
            {"question_id": "Q01", "valid": True, "issues": []}
        ],
    }
    runner = RecordingRunner(payload)
    result = ExamVerifier(
        runner=runner, resolver=lambda: "C:/safe/codex.exe", temporary_root=tmp_path
    ).verify(REQUEST, BLUEPRINT, {"questions": []})

    assert result == payload
    assert runner.args[runner.args.index("--sandbox") + 1] == "read-only"
    assert runner.args[runner.args.index("--output-schema") + 1].endswith(
        "exam-verification.schema.json"
    )
    assert "独立校验" in runner.prompt


def test_remote_output_schemas_use_only_supported_constraints() -> None:
    """Codex Structured Outputs rejects uniqueItems even though JSON Schema permits it."""
    for name in ("exam-generation.schema.json", "exam-verification.schema.json"):
        text = (files("qingzi_learning.schema") / name).read_text("utf-8")
        assert '"uniqueItems"' not in text
