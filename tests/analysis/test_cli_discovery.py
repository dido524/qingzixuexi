import subprocess
from pathlib import Path

import pytest


def test_prefers_new_authenticated_desktop_cli_over_old_path(monkeypatch, tmp_path):
    from qingzi_learning.analysis.codex_cli import resolve_codex_cli
    bundled = tmp_path / "OpenAI" / "Codex" / "bin" / "hash" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "C:/old/codex.cmd")
    calls = []
    def probe(args, **kwargs):
        calls.append(args)
        assert kwargs["shell"] is False and kwargs["timeout"] <= 10
        output = "codex-cli 0.154.0-alpha.6.2" if args[-1] == "--version" else "--output-schema --sandbox --image --output-last-message" if args[-1] == "--help" else "Logged in using ChatGPT"
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(subprocess, "run", probe)
    assert resolve_codex_cli() == str(bundled)
    assert all(args[0] == str(bundled) for args in calls)


def test_unusable_desktop_falls_back_to_verified_path_cli(monkeypatch, tmp_path):
    from qingzi_learning.analysis.codex_cli import resolve_codex_cli
    bundled = tmp_path / "OpenAI" / "Codex" / "bin" / "hash" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "C:/supported/codex.cmd")
    def probe(args, **kwargs):
        if args[0] == str(bundled):
            return subprocess.CompletedProcess(args, 1, "", "private diagnostic")
        output = "codex-cli 0.154.0" if args[-1] == "--version" else "--output-schema --sandbox --image --output-last-message" if args[-1] == "--help" else "Logged in using ChatGPT"
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(subprocess, "run", probe)
    assert resolve_codex_cli() == "C:/supported/codex.cmd"


def test_unauthenticated_or_incompatible_cli_returns_sanitized_error(monkeypatch, tmp_path):
    from qingzi_learning.analysis.codex_cli import AnalysisError, resolve_codex_cli
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "C:/private/codex.cmd")
    monkeypatch.setattr(subprocess, "run", lambda args, **kw: subprocess.CompletedProcess(args, 1, "", "secret"))
    with pytest.raises(AnalysisError, match="cli_unavailable"):
        resolve_codex_cli()


@pytest.mark.parametrize("version,accepted", [
    ("codex-cli 0.137.0", False), ("codex-cli 0.153.99", False),
    ("codex-cli 0.154.0-alpha.6.2", True), ("codex-cli 1.2.0", True),
    ("codex-cli nonsense", False), ("codex-cli 0.154", False),
])
def test_version_floor_uses_numeric_semantic_core(monkeypatch, tmp_path, version, accepted):
    from qingzi_learning.analysis.codex_cli import AnalysisError, resolve_codex_cli
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "C:/cli/codex.cmd")
    def probe(args, **kwargs):
        output = version if args[-1] == "--version" else "--output-schema --sandbox --image --output-last-message" if args[-1] == "--help" else "Logged in using ChatGPT"
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(subprocess, "run", probe)
    if accepted:
        assert resolve_codex_cli() == "C:/cli/codex.cmd"
    else:
        with pytest.raises(AnalysisError, match="cli_unavailable"):
            resolve_codex_cli()


def test_old_desktop_is_skipped_for_compatible_path(monkeypatch, tmp_path):
    from qingzi_learning.analysis.codex_cli import resolve_codex_cli
    bundled = tmp_path / "OpenAI" / "Codex" / "bin" / "hash" / "codex.exe"
    bundled.parent.mkdir(parents=True)
    bundled.write_bytes(b"fixture")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _: "C:/supported/codex.cmd")
    def probe(args, **kwargs):
        version = "codex-cli 0.137.0" if args[0] == str(bundled) else "codex-cli 0.154.0"
        output = version if args[-1] == "--version" else "--output-schema --sandbox --image --output-last-message" if args[-1] == "--help" else "Logged in using ChatGPT"
        return subprocess.CompletedProcess(args, 0, output, "")
    monkeypatch.setattr(subprocess, "run", probe)
    assert resolve_codex_cli() == "C:/supported/codex.cmd"
