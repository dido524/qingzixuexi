from qingzi_learning.models.router import ModelServices
from qingzi_learning.models.settings import MemorySecretStore, ModelSettingsManager


class Spy:
    def __init__(self, name):
        self.name = name
        self.calls = []

    def analyze(self, value):
        self.calls.append(("analyze", value))
        return self.name

    def generate(self, *values):
        self.calls.append(("generate", values))
        return self.name

    def repair(self, *values):
        self.calls.append(("repair", values))
        return self.name

    def verify(self, *values):
        self.calls.append(("verify", values))
        return self.name


def test_every_model_task_uses_only_the_selected_provider_and_switches_next_call(tmp_path):
    settings = ModelSettingsManager(tmp_path, secret_store=MemorySecretStore())
    codex = {name: Spy(f"codex-{name}") for name in ("analysis", "narrative", "generator", "verifier")}
    deepseek = {name: Spy(f"deepseek-{name}") for name in codex}
    services = ModelServices.build(
        settings,
        codex_analyzer=codex["analysis"], deepseek_analyzer=deepseek["analysis"],
        codex_narrative=codex["narrative"], deepseek_narrative=deepseek["narrative"],
        codex_generator=codex["generator"], deepseek_generator=deepseek["generator"],
        codex_verifier=codex["verifier"], deepseek_verifier=deepseek["verifier"],
    )

    assert services.analyzer.analyze("doc") == "codex-analysis"
    assert services.narrative.generate("profile") == "codex-narrative"
    assert services.generator.generate("id", "request", "blueprint") == "codex-generator"
    assert services.generator.repair("id", "request", "blueprint", ["issue"]) == "codex-generator"
    assert services.verifier.verify("request", "blueprint", "generation") == "codex-verifier"
    assert all(not spy.calls for spy in deepseek.values())

    settings.save(provider="deepseek", api_key="sk-test")

    assert services.analyzer.analyze("doc2") == "deepseek-analysis"
    assert services.narrative.generate("profile2") == "deepseek-narrative"
    assert services.narrative.source_name == "deepseek"
    assert services.generator.generate("id2", "request", "blueprint") == "deepseek-generator"
    assert services.generator.repair("id2", "request", "blueprint", ["issue"]) == "deepseek-generator"
    assert services.verifier.verify("request", "blueprint", "generation") == "deepseek-verifier"
    assert len(codex["analysis"].calls) == 1
