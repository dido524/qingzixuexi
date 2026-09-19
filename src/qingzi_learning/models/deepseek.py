from __future__ import annotations

import base64
import json
import mimetypes
from importlib.resources import files
from pathlib import Path
import socket
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from jsonschema import ValidationError, validate

from qingzi_learning.analysis.codex_cli import (
    AnalysisError,
    _from_payload,
    _prompt as analysis_prompt,
    _reject_constant,
    _unique_object,
    _validate_pages,
    validate_result,
)
from qingzi_learning.domain import AnalysisResult, CapturedDocument
from qingzi_learning.exams.blueprint import ExamRequest
from qingzi_learning.exams.generator import build_generation_prompt, build_verification_prompt
from qingzi_learning.models.settings import ModelSettingsManager
from qingzi_learning.reporting.narrative import _prompt as narrative_prompt, validate_narrative


class DeepSeekTransport(Protocol):
    def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout: int
    ) -> tuple[int, bytes]: ...


class UrllibDeepSeekTransport:
    def send(
        self, url: str, headers: dict[str, str], body: bytes, timeout: int
    ) -> tuple[int, bytes]:
        request = Request(url, data=body, headers=headers, method="POST")
        try:
            with urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except HTTPError as exc:
            # Status is useful; the response body may contain provider or account
            # details and is deliberately not read into exceptions or logs.
            return exc.code, b""


class DeepSeekClient:
    def __init__(
        self,
        settings: ModelSettingsManager,
        *,
        transport: DeepSeekTransport | None = None,
        timeout: int = 600,
    ) -> None:
        self.settings = settings
        self.transport = transport or UrllibDeepSeekTransport()
        self.timeout = timeout

    def complete_json(
        self,
        prompt: str,
        *,
        images: tuple[Path, ...] = (),
        reasoning_effort: str = "none",
    ) -> dict[str, Any]:
        settings = self.settings.snapshot()
        key = self.settings.deepseek_key()
        if not key:
            raise AnalysisError("deepseek_api_key_missing")

        content: str | list[dict[str, Any]]
        if images:
            content = [{"type": "text", "text": prompt}]
            for image in images:
                try:
                    raw = image.read_bytes()
                except OSError:
                    raise AnalysisError("invalid_document") from None
                mime = mimetypes.guess_type(image.name)[0] or "image/jpeg"
                encoded = base64.b64encode(raw).decode("ascii")
                content.append({
                    "type": "image_url",
                    "image_url": {"url": f"data:{mime};base64,{encoded}"},
                })
        else:
            content = prompt

        body: dict[str, Any] = {
            "model": settings.deepseek_model,
            "messages": [{"role": "user", "content": content}],
            "response_format": {"type": "json_object"},
        }
        if reasoning_effort in {"high", "xhigh"}:
            body["thinking"] = {"type": "enabled"}
        elif reasoning_effort == "none":
            body["thinking"] = {"type": "disabled"}
        wire = json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        url = settings.deepseek_base_url.rstrip("/") + "/chat/completions"
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        try:
            status, response_body = self.transport.send(url, headers, wire, self.timeout)
        except (TimeoutError, socket.timeout, URLError, OSError, ValueError, UnicodeError):
            raise AnalysisError("deepseek_unavailable") from None
        if status in {401, 403}:
            raise AnalysisError("deepseek_auth_failed")
        if status == 429:
            raise AnalysisError("deepseek_rate_limited")
        if status < 200 or status >= 300:
            raise AnalysisError("deepseek_unavailable")
        try:
            envelope = json.loads(
                response_body.decode("utf-8"),
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
            model_content = envelope["choices"][0]["message"]["content"]
            if not isinstance(model_content, str):
                raise TypeError
            payload = json.loads(
                model_content,
                parse_constant=_reject_constant,
                object_pairs_hook=_unique_object,
            )
            if not isinstance(payload, dict):
                raise TypeError
            return payload
        except (UnicodeError, ValueError, TypeError, KeyError, IndexError):
            raise AnalysisError("invalid_deepseek_response") from None


class DeepSeekAnalyzer:
    def __init__(self, client: DeepSeekClient) -> None:
        self.client = client

    def analyze(self, document: CapturedDocument) -> AnalysisResult:
        _validate_pages(document)
        try:
            payload = self.client.complete_json(
                analysis_prompt(document),
                images=tuple(page.path.resolve() for page in document.pages),
                reasoning_effort="low",
            )
            result = _from_payload(payload)
            validate_result(result, document, require_answer_bbox=True)
            return result
        except AnalysisError:
            raise
        except (ValueError, TypeError, KeyError, ValidationError):
            raise AnalysisError("invalid_deepseek_response") from None


class DeepSeekNarrativeProvider:
    source_name = "deepseek"

    def __init__(self, client: DeepSeekClient) -> None:
        self.client = client

    def generate(self, profile: dict[str, Any]) -> dict[str, Any]:
        payload = self.client.complete_json(narrative_prompt(profile), reasoning_effort="none")
        try:
            validate_narrative(profile, payload)
        except (ValueError, TypeError, ValidationError):
            raise AnalysisError("invalid_narrative") from None
        return payload


class DeepSeekExamGenerator:
    def __init__(self, client: DeepSeekClient) -> None:
        self.client = client

    def generate(
        self, exam_id: str, request: ExamRequest, blueprint: dict[str, Any]
    ) -> dict[str, Any]:
        return self._generate(exam_id, request, blueprint, ())

    def repair(
        self,
        exam_id: str,
        request: ExamRequest,
        blueprint: dict[str, Any],
        issues: list[str],
    ) -> dict[str, Any]:
        return self._generate(exam_id, request, blueprint, tuple(issues[:20]))

    def _generate(
        self,
        exam_id: str,
        request: ExamRequest,
        blueprint: dict[str, Any],
        issues: tuple[str, ...],
    ) -> dict[str, Any]:
        payload = self.client.complete_json(
            build_generation_prompt(exam_id, request, blueprint, issues),
            reasoning_effort="high",
        )
        return _validate_schema(payload, "exam-generation.schema.json", "invalid_exam_response")


class DeepSeekExamVerifier:
    def __init__(self, client: DeepSeekClient) -> None:
        self.client = client

    def verify(
        self,
        request: ExamRequest,
        blueprint: dict[str, Any],
        generation: dict[str, Any],
    ) -> dict[str, Any]:
        payload = self.client.complete_json(
            build_verification_prompt(request, blueprint, generation),
            reasoning_effort="high",
        )
        return _validate_schema(payload, "exam-verification.schema.json", "invalid_exam_response")


def _validate_schema(payload: dict[str, Any], schema_name: str, code: str) -> dict[str, Any]:
    try:
        schema = json.loads((files("qingzi_learning.schema") / schema_name).read_text("utf-8"))
        validate(instance=payload, schema=schema)
        return payload
    except (OSError, ValueError, TypeError, ValidationError):
        raise AnalysisError(code) from None
