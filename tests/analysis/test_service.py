from dataclasses import replace
from hashlib import sha256
import importlib
import json

import pytest


def service_with_runner(runner):
    cli = importlib.import_module("qingzi_learning.analysis.codex_cli")
    module = importlib.import_module("qingzi_learning.analysis.service")
    return module.AnalysisService(cli.CodexCliAnalyzer(runner=runner))


@pytest.mark.parametrize("failure", ["not_logged_in", "timeout", "invalid_json", "missing_response", "quota", "network", "missing_cli"])
def test_analysis_failure_queues_document_without_losing_pages(runner, document, failure):
    runner.failure = failure
    result = service_with_runner(runner).analyze_or_queue(document)
    assert result.state == "pending"
    assert result.analysis is None
    assert result.error_code
    for page in document.pages:
        assert sha256(page.path.read_bytes()).hexdigest() == page.sha256
    state_path = document.pages[0].path.parent / "analysis_state.json"
    state_text = state_path.read_text("utf-8")
    state = json.loads(state_text)
    assert state["state"] == "pending"
    assert state["document_id"] == document.document_id
    assert "SECRET" not in state_text
    assert not list(state_path.parent.glob("*.part"))


@pytest.mark.parametrize(
    ("question_type", "source", "confidence", "expected"),
    [
        ("composition", "model", 0.899, "needs_review"),
        ("reading_open", "model", 0.899, "needs_review"),
        ("short_answer", "model", 0.899, "needs_review"),
        ("composition", "model", 0.90, "partial"),
        ("composition", "teacher", 0.899, "partial"),
        ("reading_open", "mixed", 0.899, "partial"),
        ("objective", "model", 0.899, "partial"),
        ("fill_blank", "model", 0.899, "partial"),
        ("calculation", "model", 0.899, "partial"),
        ("application", "model", 0.899, "partial"),
        ("translation", "model", 0.899, "partial"),
        ("other", "model", 0.899, "partial"),
    ],
)
def test_subjective_protection_boundaries(runner, document, question_type, source, confidence, expected):
    question = runner.payload["questions"][1]
    question.update(question_type=question_type, decision_source=source, confidence=confidence)
    outcome = service_with_runner(runner).analyze_or_queue(document)
    protected = outcome.analysis.questions[1]
    assert protected.status.value == expected
    assert protected.confidence == confidence
    if expected == "needs_review":
        assert protected.counts_toward_mastery is False
    assert outcome.analysis.questions[0].status.value == "correct"


@pytest.mark.parametrize(("confidence", "state"), [(0.849, "needs_subject_confirmation"), (0.85, "analyzed"), (0.96, "analyzed")])
def test_subject_confirmation_threshold(runner, document, confidence, state):
    runner.payload["subject_confidence"] = confidence
    result = service_with_runner(runner).analyze_or_queue(document)
    assert result.state == state
    assert result.analysis.subject_confidence == confidence
    saved = json.loads((document.session_dir / "analysis_state.json").read_text("utf-8"))
    assert saved["state"] == state


def test_retry_replaces_pending_sidecar_and_preserves_source_evidence(runner, document):
    service = service_with_runner(runner)
    runner.failure = "timeout"
    assert service.analyze_or_queue(document).state == "pending"
    runner.failure = None
    result = service.analyze_or_queue(document)
    assert result.state == "analyzed"
    state = json.loads((document.session_dir / "analysis_state.json").read_text("utf-8"))
    assert state["error_code"] is None
    assert all(sha256(p.path.read_bytes()).hexdigest() == p.sha256 for p in document.pages)


def test_service_revalidates_analyzer_result(runner, document):
    service = service_with_runner(runner)
    valid = service.analyzer.analyze(document)

    class InvalidAnalyzer:
        def analyze(self, document):
            return replace(valid, questions=(replace(valid.questions[0], confidence=2.0),))

    service.analyzer = InvalidAnalyzer()
    assert service.analyze_or_queue(document).state == "pending"


def test_state_is_durable_before_runner_starts(runner, document):
    original_run = runner.run

    def check_state(args, **kwargs):
        state = json.loads((document.session_dir / "analysis_state.json").read_text("utf-8"))
        assert state["state"] == "pending"
        return original_run(args, **kwargs)

    runner.run = check_state
    service_with_runner(runner).analyze_or_queue(document)


def test_document_session_directory_is_derived_and_validated(document, tmp_path):
    assert document.session_dir == document.pages[0].path.parent
    with pytest.raises(ValueError, match="页面"):
        replace(document, pages=()).session_dir
    pages = (document.pages[0], replace(document.pages[1], path=tmp_path / "other.jpg"))
    with pytest.raises(ValueError, match="目录"):
        replace(document, pages=pages).session_dir
