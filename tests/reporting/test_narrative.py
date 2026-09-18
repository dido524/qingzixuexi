import json
from pathlib import Path
import subprocess

import pytest


@pytest.fixture
def profile() -> dict:
    return {
        "schema_version": 1,
        "cutoff_at": "2026-09-18T00:00:00Z",
        "summary": {
            "question_count": 12,
            "document_count": 3,
            "pending_count": 0,
            "excluded_low_confidence_count": 0,
        },
        "delta": {"question_count": 4, "new_document_count": 1, "pending_count": 0},
        "subjects": {
            "语文": _subject(),
            "数学": _subject(
                question_count=12,
                evidence_level="forming",
                points={
                    "分数应用": {
                        "evidence_id": "kp-math-fraction",
                        "knowledge_point": "分数应用",
                        "exposure_count": 4,
                        "correct_count": 2,
                        "incorrect_count": 2,
                        "partial_count": 0,
                        "mastery_rate": 0.5,
                        "review_priority": 55,
                        "trend": "improving",
                        "last_seen_at": "2026-09-18 10:00:00",
                        "error_categories": [{"name": "审题", "count": 2}],
                        "representative_questions": [],
                        "delta": {
                            "exposure_count": 1,
                            "correct_count": 1,
                            "incorrect_count": 0,
                            "partial_count": 0,
                        },
                        "change": "improved",
                    }
                },
            ),
            "英语": _subject(),
        },
    }


def test_local_narrative_is_grounded_and_avoids_shame_labels(profile: dict) -> None:
    from qingzi_learning.reporting.narrative import LocalNarrativeProvider, validate_narrative

    narrative = LocalNarrativeProvider().generate(profile)
    validate_narrative(profile, narrative)

    text = json.dumps(narrative, ensure_ascii=False)
    assert "分数应用" in text
    assert all(word not in text for word in ("很差", "笨", "落后", "粗心大意"))
    assert len(narrative["child_goals"]) <= 3
    assert narrative["child_focus"][0]["evidence_ids"] == ["kp-math-fraction"]


def test_narrative_rejects_unknown_evidence_and_unsafe_html(profile: dict) -> None:
    from qingzi_learning.reporting.narrative import validate_narrative

    bad = _valid_narrative("missing-point")
    with pytest.raises(ValueError, match="证据"):
        validate_narrative(profile, bad)

    unsafe = _valid_narrative("kp-math-fraction")
    unsafe["child_headline"] = "<script>bad()</script>"
    with pytest.raises(ValueError, match="安全"):
        validate_narrative(profile, unsafe)


def test_narrative_rejects_numeric_claims_not_part_of_a_knowledge_point(profile: dict) -> None:
    from qingzi_learning.reporting.narrative import validate_narrative

    bad = _valid_narrative("kp-math-fraction")
    bad["parent_summary"] = "掌握率已经达到百分之八十（80%）。"
    with pytest.raises(ValueError, match="数字"):
        validate_narrative(profile, bad)


def test_service_uses_validated_local_fallback_when_primary_fails(profile: dict) -> None:
    from qingzi_learning.reporting.narrative import LocalNarrativeProvider, NarrativeService

    class FailingProvider:
        def generate(self, _profile):
            raise RuntimeError("external failure must not escape")

    result = NarrativeService(FailingProvider(), LocalNarrativeProvider()).generate(profile)

    assert result["source"] == "local_template"
    assert "external failure" not in json.dumps(result, ensure_ascii=False)


def test_codex_provider_uses_read_only_schema_contract(profile: dict, tmp_path: Path) -> None:
    from qingzi_learning.reporting.narrative import CodexNarrativeProvider

    payload = _valid_narrative("kp-math-fraction")

    class Runner:
        def __init__(self):
            self.args = None
            self.prompt = None

        def run(self, args, **kwargs):
            self.args = args
            self.prompt = kwargs["input"]
            output = Path(args[args.index("--output-last-message") + 1])
            output.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "", "")

    runner = Runner()
    provider = CodexNarrativeProvider(
        runner=runner,
        resolver=lambda: "C:/safe/codex.exe",
        temporary_root=tmp_path,
    )

    assert provider.generate(profile) == payload
    assert runner.args is not None
    assert runner.args[runner.args.index("--sandbox") + 1] == "read-only"
    assert "--output-schema" in runner.args
    assert "不得修改统计事实" in runner.prompt
    assert "reference_answer" not in runner.prompt


def _subject(
    *, question_count: int = 0, evidence_level: str = "no_data", points: dict | None = None
) -> dict:
    return {
        "question_count": question_count,
        "document_count": 0 if not question_count else 3,
        "study_day_count": 0 if not question_count else 2,
        "evidence_level": evidence_level,
        "correct_count": 0,
        "incorrect_count": 0,
        "partial_count": 0,
        "knowledge_points": points or {},
    }


def _item(text: str, evidence_id: str) -> dict:
    return {"text": text, "evidence_ids": [evidence_id]}


def _valid_narrative(evidence_id: str) -> dict:
    return {
        "child_headline": "把现在会的内容练得更稳",
        "child_progress": [_item("最近的练习已经出现进步。", evidence_id)],
        "child_focus": [_item("分数应用值得继续练习。", evidence_id)],
        "child_goals": [_item("完成后再检查题意。", evidence_id)],
        "child_closing": "按自己的节奏，一步一步确认。",
        "parent_summary": "当前结论仍需结合后续练习继续观察。",
        "parent_observations": [_item("分数应用近期有所改善。", evidence_id)],
        "parent_recommendations": [_item("安排一次同类题复测。", evidence_id)],
    }
