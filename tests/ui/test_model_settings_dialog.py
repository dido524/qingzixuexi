import pytest
import queue

from qingzi_learning.config import AppConfig
from qingzi_learning.models.settings import MemorySecretStore, ModelSettingsManager
from qingzi_learning.ui.app import CaptureViewModel, CompletionSummary, LearningAssistantApp
from qingzi_learning.workflow.controller import WorkflowOutcome
from qingzi_learning.ui.model_settings_dialog import ModelSettingsDialog


@pytest.fixture
def tk_root(tk_interpreter):
    tkinter = pytest.importorskip("tkinter")
    root = tkinter.Toplevel(tk_interpreter)
    root.withdraw()
    yield root
    root.destroy()


def test_dialog_masks_key_and_blank_save_preserves_it(tk_root, tmp_path):
    settings = ModelSettingsManager(tmp_path, secret_store=MemorySecretStore())
    settings.save(provider="deepseek", api_key="sk-existing")
    saved = []
    dialog = ModelSettingsDialog(tk_root, settings, on_saved=saved.append)

    assert dialog.api_key_entry.cget("show") == "•"
    assert dialog.api_key_var.get() == ""
    assert "已安全保存" in dialog.key_status_var.get()
    dialog.provider_var.set("codex")
    assert dialog.save()

    assert settings.deepseek_key() == "sk-existing"
    assert saved[-1].provider == "codex"
    assert not dialog.window.winfo_exists()


def test_deepseek_requires_key_and_shows_inline_validation(tk_root, tmp_path):
    settings = ModelSettingsManager(tmp_path, secret_store=MemorySecretStore())
    dialog = ModelSettingsDialog(tk_root, settings, on_saved=lambda _: None)
    dialog.provider_var.set("deepseek")

    assert not dialog.save()
    assert "API Key" in dialog.error_var.get()
    assert dialog.window.winfo_exists()

    dialog.api_key_var.set("sk-new")
    assert dialog.save()
    assert settings.snapshot().provider == "deepseek"
    assert settings.deepseek_key() == "sk-new"


def test_explicit_clear_removes_key_without_exposing_it(tk_root, tmp_path):
    settings = ModelSettingsManager(tmp_path, secret_store=MemorySecretStore("sk-secret"))
    dialog = ModelSettingsDialog(tk_root, settings, on_saved=lambda _: None)
    dialog.clear_key_var.set(True)

    assert dialog.save()
    assert settings.deepseek_key() is None
    assert "sk-secret" not in dialog.error_var.get()


def test_unreadable_saved_key_does_not_block_replacement(tk_root, tmp_path):
    class RecoverableStore:
        def __init__(self): self.value = None; self.broken = True
        def read(self):
            if self.broken:
                from qingzi_learning.models.settings import ModelSettingsError
                raise ModelSettingsError("已保存的 API Key 无法解密，请重新填写。")
            return self.value
        def write(self, value): self.value = value; self.broken = False
        def clear(self): self.value = None; self.broken = False

    store = RecoverableStore()
    settings = ModelSettingsManager(tmp_path, secret_store=store)
    dialog = ModelSettingsDialog(tk_root, settings, on_saved=lambda _: None)
    assert "无法读取" in dialog.key_status_var.get()

    dialog.provider_var.set("deepseek")
    dialog.api_key_var.set("sk-replacement")
    assert dialog.save()
    assert settings.deepseek_key() == "sk-replacement"


def test_main_window_shows_selected_model_and_blocks_settings_while_busy(
    tk_root, tmp_path, monkeypatch
):
    class Worker:
        def __init__(self):
            self.preview_events = queue.Queue()
            self.events = queue.Queue()

        def start(self):
            pass

    config = AppConfig(
        knowledge_root=tmp_path / "knowledge", subjects=("语文", "数学", "英语"),
        camera_vid=1, camera_pid=2, spool_root=tmp_path / "spool",
        app_data_root=tmp_path / "data",
    )
    config.knowledge_root.mkdir()
    settings = ModelSettingsManager(config.app_data_root, secret_store=MemorySecretStore())
    monkeypatch.setattr(LearningAssistantApp, "_schedule_poll", lambda self: None)
    app = LearningAssistantApp(tk_root, config, Worker(), model_settings=settings)

    assert app.model_var.get() == "当前模型：原生 Codex"
    assert app.model_settings_button.cget("state") == "normal"

    app.vm.begin_work("分析中", "op")
    app._refresh()
    assert app.model_settings_button.cget("state") == "disabled"
    app.open_model_settings()
    assert app._model_settings_dialog is None

    app.vm.finish_work("op")
    app._refresh()
    app.root.deiconify()
    app.buttons["more"].invoke()
    app.root.update()
    assert app.more_window.winfo_ismapped()
    assert app.model_settings_button.winfo_ismapped()
    app.model_settings_button.invoke()
    app.root.update()
    assert not app.more_window.winfo_ismapped()
    assert app._model_settings_dialog is not None
    app._model_settings_dialog.provider_var.set("deepseek")
    app._model_settings_dialog.api_key_var.set("sk-test")
    assert app._model_settings_dialog.save()
    assert app.model_var.get() == "当前模型：DeepSeek"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("deepseek_api_key_missing", "模型设置"),
        ("deepseek_auth_failed", "API Key"),
        ("deepseek_rate_limited", "额度"),
        ("deepseek_unavailable", "网络"),
        ("invalid_deepseek_response", "格式"),
    ],
)
def test_deepseek_analysis_errors_have_actionable_messages(code, expected):
    vm = CaptureViewModel()
    vm.on_workflow_outcome(
        WorkflowOutcome("job-1", "pending", None, error_code=code), CompletionSummary()
    )
    assert expected in vm.status
