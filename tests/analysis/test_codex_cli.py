from dataclasses import replace
import importlib
from importlib.resources import files
import json
from pathlib import Path

import pytest
from jsonschema import ValidationError, validate


def adapter(runner):
    module = importlib.import_module("qingzi_learning.analysis.codex_cli")
    return module.CodexCliAnalyzer(runner=runner)


def analysis_error():
    return importlib.import_module("qingzi_learning.analysis.codex_cli").AnalysisError


def parse_payload(payload, *, allow_legacy=False):
    return importlib.import_module("qingzi_learning.analysis.codex_cli")._from_payload(
        payload, allow_legacy=allow_legacy
    )


def validate_result(result, document):
    return importlib.import_module("qingzi_learning.analysis.codex_cli").validate_result(
        result, document
    )


def test_subprocess_runner_hides_the_windows_console(monkeypatch):
    module = importlib.import_module("qingzi_learning.analysis.codex_cli")
    seen = {}

    def run(args, **kwargs):
        seen.update(kwargs)
        return module.subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(module.subprocess, "run", run)
    monkeypatch.setattr(module.subprocess, "CREATE_NO_WINDOW", 0x08000000, raising=False)

    module.SubprocessRunner().run(
        ["codex.exe", "exec"], input="prompt", text=True,
        capture_output=True, timeout=10, shell=False, encoding="utf-8",
    )

    assert seen["creationflags"] & 0x08000000


def test_codex_command_is_read_only_and_uses_argument_list(runner, document):
    result = adapter(runner).analyze(document)
    args = runner.last_args
    assert isinstance(args, list)
    assert args[0].lower().endswith("codex.cmd")
    assert args[1:3] == ["exec", "--skip-git-repo-check"]
    assert args[args.index("--sandbox") : args.index("--sandbox") + 2] == ["--sandbox", "read-only"]
    assert "--dangerously-bypass-approvals-and-sandbox" not in args
    assert args.count("--image") == 2
    assert [args[i + 1] for i, arg in enumerate(args) if arg == "--image"] == [str(p.path) for p in document.pages]
    assert args[args.index("-C") + 1] == str(document.pages[0].path.parent)
    assert args[-1] == "-"
    schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text("utf-8"))
    assert schema["additionalProperties"] is False
    options = runner.last_kwargs
    assert options["shell"] is False
    assert options["timeout"] == 600
    assert options["text"] is True and options["capture_output"] is True
    assert options["encoding"] == "utf-8"
    assert document.document_id in options["input"]
    assert "question_id 必须原样保留卷面上印刷的完整题号" in options["input"]
    assert result.document_id == document.document_id
    assert result.questions[1].page == 2
    assert result.subject.value == "语文"
    assert result.questions[0].status.value == "correct"
    assert result.questions[0].answer_bbox == (0.12, 0.28, 0.34, 0.09)
    assert all(term in options["input"] for term in ("answer_bbox", "归一化", "答案区域"))


@pytest.mark.parametrize("bbox", [
    {"x": -0.01, "y": 0.2, "width": 0.3, "height": 0.2},
    {"x": 0.1, "y": 0.2, "width": 0.0, "height": 0.2},
    {"x": 0.8, "y": 0.2, "width": 0.3, "height": 0.2},
    {"x": 0.1, "y": 0.9, "width": 0.3, "height": 0.2},
])
def test_rejects_invalid_or_out_of_page_answer_boxes(runner, document, bbox):
    runner.payload["questions"][0]["answer_bbox"] = bbox
    with pytest.raises(analysis_error()) as caught:
        adapter(runner).analyze(document)
    assert caught.value.code == "invalid_response"


def test_new_model_response_requires_page_subject_mapping(runner, document):
    """Reject a newly generated response that omits the per-page mapping."""
    runner.payload.pop("page_subjects")
    with pytest.raises(analysis_error()) as caught:
        adapter(runner).analyze(document)
    assert caught.value.code == "invalid_response"


@pytest.mark.parametrize("guidance_source", ["prompt", "transport_schema"])
def test_analyzer_sends_semantic_page_confirmation_rule_and_honors_high_confidence_flags(runner, document, guidance_source):
    """Dropping the outbound mixed/insufficient-content instruction must fail at the runner boundary."""
    from qingzi_learning.workflow.subject_split import build_subject_split_plan

    runner.payload["page_subjects"][0].update(confidence=.99, needs_confirmation=True, reason="单页同时包含语文和数学")
    runner.payload["page_subjects"][1].update(confidence=.99, needs_confirmation=True, reason="页面内容不足，仅有标题")
    result = adapter(runner).analyze(document)
    if guidance_source == "prompt":
        guidance = runner.last_kwargs["input"]
    else:
        args = runner.last_args
        sent_schema = json.loads(Path(args[args.index("--output-schema") + 1]).read_text("utf-8"))
        guidance = sent_schema["$defs"]["page_subject"]["properties"]["needs_confirmation"].get("description", "")
    # Inspect what the real adapter delivered to its external model, not Python
    # source text. This checks the instruction contract, not live model accuracy.
    rule = [sentence for sentence in guidance.split("。") if "needs_confirmation=true" in sentence]
    assert any(all(term in sentence for term in ("单页", "多个学科", "内容不足", "无论", "置信度")) for sentence in rule)
    plan = build_subject_split_plan(document, result, {}, .85)
    assert [page.page for page in plan.unresolved_pages] == [1, 2]
    assert plan.groups == ()


def test_validate_result_rejects_duplicate_page_subject_mapping(payload, document):
    """Reject duplicate mappings instead of silently accepting incomplete coverage."""
    payload["page_subjects"] = [
        {
            "page": 1,
            "suggested_subject": "数学",
            "confidence": 0.9,
            "needs_confirmation": False,
            "reason": "计算题",
        },
        {
            "page": 1,
            "suggested_subject": "数学",
            "confidence": 0.9,
            "needs_confirmation": False,
            "reason": "重复页",
        },
    ]
    with pytest.raises(ValueError, match="页面学科映射"):
        validate_result(parse_payload(payload), document)


def test_legacy_cached_payload_requires_explicit_compatibility_path(payload):
    """Only callers deliberately loading old persisted data may omit mappings."""
    payload.pop("page_subjects")
    with pytest.raises(ValidationError):
        parse_payload(payload)
    assert parse_payload(payload, allow_legacy=True).page_subjects == ()


@pytest.mark.parametrize("mutation", ["document_id", "page", "duplicate_question", "schema", "nan"])
def test_rejects_invalid_or_unrelated_analysis(runner, document, mutation):
    if mutation == "document_id":
        runner.payload["document_id"] = "other-document"
    elif mutation == "page":
        runner.payload["questions"][0]["page"] = 3
    elif mutation == "duplicate_question":
        runner.payload["questions"][1]["question_id"] = "1"
    elif mutation == "nan":
        runner.payload["subject_confidence"] = float("nan")
    else:
        runner.payload["unexpected"] = "secret"
    analyzer = adapter(runner)
    with pytest.raises(analysis_error()) as caught:
        analyzer.analyze(document)
    assert caught.value.code == "invalid_response"
    assert "secret" not in str(caught.value).lower()


@pytest.mark.parametrize("failure", ["timeout", "not_logged_in", "quota", "network", "missing_cli", "missing_response", "invalid_json"])
def test_process_failures_are_sanitized(runner, document, failure):
    runner.failure = failure
    analyzer = adapter(runner)
    with pytest.raises(analysis_error()) as caught:
        analyzer.analyze(document)
    assert "SECRET" not in str(caught.value)
    assert all(p.path.exists() for p in document.pages)


def test_missing_cli_fails_without_launching_process(runner, document, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda name: None)
    analyzer = adapter(runner)
    with pytest.raises(analysis_error()) as caught:
        analyzer.analyze(document)
    assert caught.value.code == "cli_unavailable"
    assert runner.calls == []


@pytest.mark.parametrize("mutation", ["hash", "empty", "different_parent", "unsafe_batch_path", "duplicate_page"])
def test_invalid_pages_are_rejected_before_process_launch(runner, document, tmp_path, mutation):
    pages = list(document.pages)
    if mutation == "hash":
        pages[0].path.write_bytes(b"changed")
    elif mutation == "empty":
        pages = []
    elif mutation == "duplicate_page":
        pages[1] = replace(pages[1], page_number=1)
    else:
        target = tmp_path / ("x&whoami.jpg" if mutation == "unsafe_batch_path" else "other.jpg")
        target.write_bytes(pages[0].path.read_bytes())
        pages[0] = replace(pages[0], path=target)
        if mutation == "unsafe_batch_path":
            pages = pages[:1]
    analyzer = adapter(runner)
    with pytest.raises(analysis_error()):
        analyzer.analyze(replace(document, pages=tuple(pages)))
    assert runner.calls == []


def test_retry_cannot_accept_stale_response(runner, document):
    analyzer = adapter(runner)
    analyzer.analyze(document)
    first_path = runner.last_args[runner.last_args.index("--output-last-message") + 1]
    runner.failure = "missing_response"
    with pytest.raises(analysis_error()):
        analyzer.analyze(document)
    second_path = runner.last_args[runner.last_args.index("--output-last-message") + 1]
    assert first_path != second_path


def test_command_uses_structured_outputs_transport_subset(runner, document):
    adapter(runner).analyze(document)
    args = runner.last_args
    schema_path = Path(args[args.index("--output-schema") + 1])
    assert schema_path.name == "analysis-transport.schema.json"
    schema = json.loads(schema_path.read_text("utf-8"))
    unsupported = {
        "allOf", "anyOf", "oneOf", "not", "if", "then", "else",
        "patternProperties", "unevaluatedProperties", "dependentSchemas", "dependentRequired",
    }

    def check_node(node):
        if isinstance(node, dict):
            assert not unsupported.intersection(node)
            if node.get("type") == "object":
                assert node["additionalProperties"] is False
                assert set(node["required"]) == set(node["properties"])
            for child in node.values():
                check_node(child)
        elif isinstance(node, list):
            for child in node:
                check_node(child)

    check_node(schema)
    validate(runner.payload, schema)


def test_transport_preserves_domain_required_fields_and_enums():
    resources = files("qingzi_learning.schema")
    transport = json.loads((resources / "analysis-transport.schema.json").read_text("utf-8"))
    domain = json.loads((resources / "analysis-result.schema.json").read_text("utf-8"))
    for transport_object, domain_object in (
        (transport, domain),
        (transport["$defs"]["page_subject"], domain["$defs"]["page_subject"]),
        (transport["$defs"]["question"], domain["$defs"]["question"]),
    ):
        assert set(domain_object["required"]).issubset(transport_object["required"])
        assert set(transport_object["properties"]) == set(domain_object["properties"])
        for name, field in domain_object["properties"].items():
            if "enum" in field:
                assert transport_object["properties"][name]["enum"] == field["enum"]


def test_teacher_priority_is_enforced_locally_after_transport_accepts(runner, document):
    runner.payload["grading_mode"] = "teacher_marked"
    # The fixture's second answer is model-only: valid transport, invalid domain.
    with pytest.raises(analysis_error()) as caught:
        adapter(runner).analyze(document)
    assert caught.value.code == "invalid_response"
    args = runner.last_args
    transport = json.loads(Path(args[args.index("--output-schema") + 1]).read_text("utf-8"))
    validate(runner.payload, transport)
