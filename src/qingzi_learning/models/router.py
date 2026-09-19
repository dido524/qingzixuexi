from __future__ import annotations

from dataclasses import dataclass

from qingzi_learning.models.settings import ModelSettingsManager


class _Router:
    def __init__(self, settings: ModelSettingsManager, codex, deepseek) -> None:
        self.settings = settings
        self.codex = codex
        self.deepseek = deepseek

    def selected(self):
        return self.deepseek if self.settings.snapshot().provider == "deepseek" else self.codex


class AnalyzerRouter(_Router):
    def analyze(self, document):
        return self.selected().analyze(document)

    def cancel(self) -> None:
        cancel = getattr(self.selected(), "cancel", None)
        if callable(cancel):
            cancel()


class NarrativeRouter(_Router):
    @property
    def source_name(self) -> str:
        return self.settings.snapshot().provider

    def generate(self, profile):
        return self.selected().generate(profile)


class GeneratorRouter(_Router):
    def generate(self, exam_id, request, blueprint):
        return self.selected().generate(exam_id, request, blueprint)

    def repair(self, exam_id, request, blueprint, issues):
        return self.selected().repair(exam_id, request, blueprint, issues)


class VerifierRouter(_Router):
    def verify(self, request, blueprint, generation):
        return self.selected().verify(request, blueprint, generation)


@dataclass(frozen=True)
class ModelServices:
    analyzer: AnalyzerRouter
    narrative: NarrativeRouter
    generator: GeneratorRouter
    verifier: VerifierRouter

    @classmethod
    def build(
        cls,
        settings: ModelSettingsManager,
        *,
        codex_analyzer,
        deepseek_analyzer,
        codex_narrative,
        deepseek_narrative,
        codex_generator,
        deepseek_generator,
        codex_verifier,
        deepseek_verifier,
    ) -> "ModelServices":
        return cls(
            AnalyzerRouter(settings, codex_analyzer, deepseek_analyzer),
            NarrativeRouter(settings, codex_narrative, deepseek_narrative),
            GeneratorRouter(settings, codex_generator, deepseek_generator),
            VerifierRouter(settings, codex_verifier, deepseek_verifier),
        )
