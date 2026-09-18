"""Static contracts for the Windows delivery assets.

These checks intentionally inspect the source scripts rather than invoking a
packaged application: a normal test run must never open the document camera or
call the authenticated Codex CLI.
"""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess


def _root() -> Path:
    return Path(__file__).resolve().parents[1]


def _script(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_packaging_scripts_never_contain_api_keys_or_unsafe_codex_flags() -> None:
    root = _root()
    paths = [
        root / "scripts" / "build.ps1",
        root / "scripts" / "install_desktop_shortcut.ps1",
    ]
    assert all(path.exists() for path in paths)
    text = "\n".join(_script(path) for path in paths)
    assert "OPENAI_API_KEY=" not in text
    assert "sk-" not in text
    assert "--dangerously-bypass-approvals-and-sandbox" not in text


def test_build_script_packages_required_schemas_and_windows_runtime_dependencies() -> None:
    root = _root()
    text = _script(root / "scripts" / "build.ps1")
    assert "pyinstaller==6.22.3" in (root / "requirements-build.txt").read_text("utf-8")
    assert "analysis-result.schema.json" in text
    assert "analysis-transport.schema.json" in text
    assert "report-narrative.schema.json" in text
    assert "exam-generation.schema.json" in text
    assert "exam-verification.schema.json" in text
    assert "storage\\schema.sql" in text
    assert "--collect-all cv2_enumerate_cameras" in text
    assert "--collect-all PIL" in text
    assert "--collect-all tkinter" in text
    assert "--collect-all multiprocessing" in text
    assert "--icon" in text
    assert "--version-file" in text
    assert "-m pytest -q" in text


def test_delivery_assets_define_a_real_icon_and_fixed_safe_install_location() -> None:
    root = _root()
    assert (root / "assets" / "qingzi-learning-assistant.ico").is_file()
    text = _script(root / "scripts" / "install_desktop_shortcut.ps1")
    assert "GetFolderPath('Desktop')" in text
    assert "CreateShortcut" in text
    assert "QingziLearningAssistant" in text
    assert "LocalApplicationData" in text
    assert "Remove-Item" in text
    assert "Test-Path -LiteralPath" in text
    assert "WorkingDirectory" in text
    assert "IconLocation" in text
    assert "连续拍摄作业并更新晴子知识库" in text


def test_main_entrypoint_has_frozen_runtime_and_safe_smoke_switch() -> None:
    root = _root()
    text = _script(root / "src" / "qingzi_learning" / "main.py")
    assert "multiprocessing.freeze_support()" in text
    assert "--smoke-check" in text
    assert "Codex CLI" in text


def test_smoke_check_opens_and_closes_a_temporary_real_repository(monkeypatch) -> None:
    """The diagnostic proves bundled schema.sql works without user-data writes."""
    from qingzi_learning import main

    created = []
    real_repository = main.KnowledgeRepository

    class TrackingRepository(real_repository):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            created.append(self)

        def close(self):
            self.schema_exists = self.connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='documents'"
            ).fetchone()[0] == "documents"
            self.closed = True
            super().close()

    monkeypatch.setattr(main.shutil, "which", lambda _name: "codex.cmd")
    monkeypatch.setattr(main, "resolve_codex_cli", lambda: "codex.cmd")
    monkeypatch.setattr(main, "KnowledgeRepository", TrackingRepository)
    assert main._smoke_check() == 0
    assert created
    assert created[0].schema_exists and created[0].closed


def test_windows_scripts_have_utf8_bom_and_parse_in_each_installed_shell() -> None:
    root = _root()
    scripts = [root / "scripts" / "build.ps1", root / "scripts" / "install_desktop_shortcut.ps1"]
    parse = (
        "$tokens=$null;$errors=$null;"
        "$path=[Environment]::GetEnvironmentVariable('QINGZI_PARSE_PATH');"
        "[void][System.Management.Automation.Language.Parser]::ParseFile($path,[ref]$tokens,[ref]$errors);"
        "if($errors.Count){$errors | ForEach-Object {$_.ToString()}; exit 1}"
    )
    shells = [shell for shell in (shutil.which("powershell.exe"), shutil.which("pwsh.exe")) if shell]
    assert shells, "Windows PowerShell is required on this Windows project"
    for script in scripts:
        assert script.read_bytes().startswith(b"\xef\xbb\xbf")
        for shell in shells:
            completed = subprocess.run(
                [shell, "-NoProfile", "-NonInteractive", "-Command", parse],
                text=True,
                capture_output=True,
                env={**os.environ, "QINGZI_PARSE_PATH": str(script)},
                timeout=30,
                check=False,
            )
            assert completed.returncode == 0, completed.stderr or completed.stdout


def test_installer_rolls_back_old_app_shortcut_and_leaves_spool_on_injected_failure(tmp_path) -> None:
    """A post-shortcut failure restores both old user-visible delivery objects."""
    root = _root()
    shell = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    assert shell
    package = tmp_path / "package"
    internal = package / "_internal" / "qingzi_learning"
    (internal / "schema").mkdir(parents=True)
    (internal / "storage").mkdir(parents=True)
    for name in ("analysis-result.schema.json", "analysis-transport.schema.json"):
        (internal / "schema" / name).write_text("{}", encoding="utf-8")
    (internal / "storage" / "schema.sql").write_text("CREATE TABLE smoke(id INTEGER);", encoding="utf-8")
    for name in ("PIL", "cv2", "tkinter", "multiprocessing"):
        (package / "_internal" / name).mkdir()
    system_exe = Path(os.environ["WINDIR"]) / "System32" / "notepad.exe"
    shutil.copy2(system_exe, package / "晴子学习助手.exe")

    install_root = tmp_path / "QingziLearningAssistant"
    old_app = install_root / "app"
    old_app.mkdir(parents=True)
    (old_app / "old-install.txt").write_text("preserve me", encoding="utf-8")
    spool = install_root / "spool"
    spool.mkdir()
    (spool / "session.json").write_text("keep", encoding="utf-8")
    desktop = tmp_path / "Desktop"
    desktop.mkdir()
    old_shortcut = desktop / "晴子学习助手.lnk"
    old_shortcut.write_bytes(b"old-shortcut")

    completed = subprocess.run(
        [
            shell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
            str(root / "scripts" / "install_desktop_shortcut.ps1"),
            "-SourceDirectory", str(package),
            "-InstallRoot", str(install_root),
            "-DesktopDirectory", str(desktop),
            "-SkipSmokeCheck",
            "-TestFailureAt", "after_shortcut",
        ],
        text=True,
        capture_output=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    assert completed.returncode != 0
    assert "INJECTED_INSTALL_FAILURE" in completed.stderr
    assert (old_app / "old-install.txt").read_text("utf-8") == "preserve me"
    assert old_shortcut.read_bytes() == b"old-shortcut"
    assert (spool / "session.json").read_text("utf-8") == "keep"
    assert not list(install_root.glob(".app-staging-*"))
    assert not list(install_root.glob(".app-backup-*"))


def test_pending_cli_failure_tells_family_how_to_restore_authenticated_analysis() -> None:
    """A no-key build must explain the local Codex dependency in the UI."""
    from qingzi_learning.ui.app import CaptureViewModel
    from qingzi_learning.workflow.controller import WorkflowOutcome

    view_model = CaptureViewModel()
    view_model.on_workflow_outcome(
        WorkflowOutcome("document-1", "pending", None, error_code="cli_unavailable")
    )

    assert "Codex CLI" in view_model.status
    assert "登录" in view_model.status
