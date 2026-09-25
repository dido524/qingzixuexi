from hashlib import sha256
from importlib.resources import files
import json
from pathlib import Path

import pytest

from qingzi_learning.analysis.codex_cli import AnalysisError
from qingzi_learning.domain import CapturedDocument, CapturedPage
from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.models.settings import MemorySecretStore, ModelSettingsManager


class RecordingTransport:
    def __init__(self, payload, *, status=200, failure=None):
        self.payload = payload
        self.status = status
        self.failure = failure
        self.calls = []

    def send(self, url, headers, body, timeout):
        self.calls.append((url, headers, json.loads(body), timeout))
        if self.failure:
            raise self.failure
        wire = {
            "choices": [{"message": {"content": json.dumps(self.payload, ensure_ascii=False)}}]
        }
        return self.status, json.dumps(wire, ensure_ascii=False).encode("utf-8")


def configured_manager(tmp_path, *, model="deepseek-flash"):
    settings = ModelSettingsManager(tmp_path, secret_store=MemorySecretStore())
    settings.save(provider="deepseek", deepseek_model=model, api_key="sk-private-test")
    return settings


def test_client_sends_openai_compatible_json_request_with_images(tmp_path):
    from qingzi_learning.models.deepseek import DeepSeekClient

    image = tmp_path / "作业.jpg"
    image.write_bytes(b"jpeg bytes")
    transport = RecordingTransport({"ok": True})
    client = DeepSeekClient(configured_manager(tmp_path), transport=transport)

    assert client.complete_json("请分析", images=(image,), reasoning_effort="low") == {"ok": True}

    url, headers, body, timeout = transport.calls[0]
    assert url == "https://api.deepseek.com/chat/completions"
    assert headers["Authorization"] == "Bearer sk-private-test"
    assert body["model"] == "deepseek-flash"
    assert body["response_format"] == {"type": "json_object"}
    assert body["messages"][0]["role"] == "user"
    assert body["messages"][0]["content"][0] == {"type": "text", "text": "请分析"}
    assert body["messages"][0]["content"][1]["image_url"]["url"].startswith(
        "data:image/jpeg;base64,"
    )
    assert "sk-private-test" not in json.dumps(body)
    assert timeout == 600


@pytest.mark.parametrize(
    ("status", "code"),
    [(401, "deepseek_auth_failed"), (403, "deepseek_auth_failed"),
     (429, "deepseek_rate_limited"), (500, "deepseek_unavailable")],
)
def test_http_errors_are_sanitized(tmp_path, status, code):
    from qingzi_learning.models.deepseek import DeepSeekClient

    transport = RecordingTransport({"SECRET": "server details"}, status=status)
    with pytest.raises(AnalysisError) as caught:
        DeepSeekClient(configured_manager(tmp_path), transport=transport).complete_json("test")

    assert caught.value.code == code
    assert "SECRET" not in str(caught.value)
    assert "sk-private-test" not in str(caught.value)


@pytest.mark.parametrize("failure", [
    TimeoutError("SECRET"), OSError("SECRET"),
    ValueError("Invalid header Bearer sk-private-test SECRET"),
])
def test_network_errors_are_sanitized(tmp_path, failure):
    from qingzi_learning.models.deepseek import DeepSeekClient

    with pytest.raises(AnalysisError) as caught:
        DeepSeekClient(
            configured_manager(tmp_path),
            transport=RecordingTransport({}, failure=failure),
        ).complete_json("test")
    assert caught.value.code == "deepseek_unavailable"
    assert "SECRET" not in str(caught.value)
    assert "sk-private-test" not in str(caught.value)


def test_invalid_model_content_is_rejected_without_echoing_it(tmp_path):
    from qingzi_learning.models.deepseek import DeepSeekClient

    class InvalidTransport:
        def send(self, *_args, **_kwargs):
            return 200, b'{"choices":[{"message":{"content":"not json SECRET"}}]}'

    with pytest.raises(AnalysisError) as caught:
        DeepSeekClient(configured_manager(tmp_path), transport=InvalidTransport()).complete_json("test")
    assert caught.value.code == "invalid_deepseek_response"
    assert "SECRET" not in str(caught.value)


def test_analyzer_reuses_strict_local_analysis_contract(tmp_path):
    from qingzi_learning.models.deepseek import DeepSeekAnalyzer, DeepSeekClient

    page = tmp_path / "page_001.jpg"
    page.write_bytes(b"page")
    document = CapturedDocument(
        "capture-1", (CapturedPage(1, page, sha256(b"page").hexdigest()),)
    )
    payload = {
        "document_id": "capture-1",
        "subject": "数学",
        "subject_confidence": 0.99,
        "document_type": "作业",
        "source_exam_id": None,
        "grading_mode": "auto_grade",
        "teacher_mark_evidence": [],
        "page_subjects": [{
            "page": 1, "suggested_subject": "数学", "confidence": 0.99,
            "needs_confirmation": False, "reason": "计算题",
        }],
        "questions": [{
            "question_id": "1", "source_exam_question_id": None,
            "question_type": "calculation", "page": 1,
            "prompt_summary": "计算", "student_answer": "3", "reference_answer": "4",
            "status": "incorrect", "decision_source": "model",
            "knowledge_points": ["整数计算"], "error_categories": ["计算"],
            "confidence": 0.98, "reason": "答案不等",
            "answer_bbox": {"x": .1, "y": .2, "width": .3, "height": .1},
        }],
        "summary": "计算错误",
    }
    transport = RecordingTransport(payload)
    result = DeepSeekAnalyzer(
        DeepSeekClient(configured_manager(tmp_path / "settings"), transport=transport)
    ).analyze(document)

    assert result.subject.value == "数学"
    assert result.questions[0].status.value == "incorrect"
    analysis_text = transport.calls[0][2]["messages"][0]["content"][0]["text"]
    assert "answer_bbox" in analysis_text
    assert "每题 reason 使用2到4句简明中文" in analysis_text
    assert "关键规则或解题步骤" in analysis_text
    schema_text = (
        files("qingzi_learning.schema") / "analysis-transport.schema.json"
    ).read_text("utf-8")
    assert schema_text in analysis_text


def test_narrative_and_exam_adapters_validate_the_same_schemas(tmp_path):
    from qingzi_learning.models.deepseek import (
        DeepSeekClient,
        DeepSeekExamGenerator,
        DeepSeekExamVerifier,
        DeepSeekNarrativeProvider,
    )

    settings = configured_manager(tmp_path / "settings")
    profile = {
        "subjects": {"数学": {"knowledge_points": {"分数应用": {
            "evidence_id": "kp-1", "knowledge_point": "分数应用",
            "change": "stable", "incorrect_count": 1, "partial_count": 0,
            "review_priority": 50,
        }}}}
    }
    item = {"text": "分数应用值得继续练习。", "evidence_ids": ["kp-1"]}
    narrative = {
        "child_headline": "把现在会的内容练得更稳",
        "child_progress": [item], "child_focus": [item], "child_goals": [item],
        "child_closing": "按自己的节奏继续。", "parent_summary": "继续观察。",
        "parent_observations": [item], "parent_recommendations": [item],
    }
    assert DeepSeekNarrativeProvider(
        DeepSeekClient(settings, transport=RecordingTransport(narrative))
    ).generate(profile) == narrative

    request = ExamRequest("数学", "分数", 40, "适中", 5, False, False)
    blueprint = {
        "targets": [{"knowledge_point": "分数应用", "evidence_id": "kp-1"}],
        "slots": [{"slot": 1, "knowledge_point": "分数应用", "category": "primary"}],
    }
    generation = {"title": "练习", "instructions": "说明", "questions": []}
    generator_transport = RecordingTransport(generation)
    generator = DeepSeekExamGenerator(DeepSeekClient(settings, transport=generator_transport))
    assert generator.generate("QZ-1", request, blueprint) == generation
    assert "只生成全新的题目" in generator_transport.calls[0][2]["messages"][0]["content"]

    verification = {"approved": True, "issues": [], "question_verdicts": []}
    verifier_transport = RecordingTransport(verification)
    verifier = DeepSeekExamVerifier(DeepSeekClient(settings, transport=verifier_transport))
    assert verifier.verify(request, blueprint, generation) == verification
    assert "独立校验" in verifier_transport.calls[0][2]["messages"][0]["content"]
