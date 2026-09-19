import json
import os

import pytest

from qingzi_learning.models.settings import (
    MemorySecretStore,
    ModelSettings,
    ModelSettingsError,
    ModelSettingsManager,
)


def manager(tmp_path, secret_store=None):
    return ModelSettingsManager(tmp_path, secret_store=secret_store or MemorySecretStore())


def test_defaults_to_codex_with_official_deepseek_defaults(tmp_path):
    settings = manager(tmp_path).snapshot()

    assert settings == ModelSettings(
        provider="codex",
        deepseek_base_url="https://api.deepseek.com",
        deepseek_model="deepseek-flash",
    )


def test_non_secret_settings_persist_but_api_key_does_not_enter_json(tmp_path):
    secrets = MemorySecretStore()
    first = manager(tmp_path, secrets)

    saved = first.save(
        provider="deepseek",
        deepseek_base_url="https://api.deepseek.com/v1",
        deepseek_model="deepseek-v4-pro",
        api_key="sk-test-secret",
    )

    assert saved.provider == "deepseek"
    second = manager(tmp_path, secrets)
    assert second.snapshot() == saved
    assert second.deepseek_key() == "sk-test-secret"
    assert second.has_deepseek_key()
    settings_text = (tmp_path / "model-settings.json").read_text("utf-8")
    assert "sk-test-secret" not in settings_text
    assert set(json.loads(settings_text)) == {
        "provider", "deepseek_base_url", "deepseek_model"
    }


def test_blank_api_key_preserves_saved_secret_and_explicit_clear_removes_it(tmp_path):
    secrets = MemorySecretStore()
    settings = manager(tmp_path, secrets)
    settings.save(provider="deepseek", api_key="sk-existing")

    settings.save(provider="codex", api_key="")
    assert settings.deepseek_key() == "sk-existing"

    settings.save(provider="codex", clear_api_key=True)
    assert not settings.has_deepseek_key()
    assert settings.deepseek_key() is None


def test_replacement_key_wins_when_clear_was_also_checked(tmp_path):
    secrets = MemorySecretStore("sk-old")
    settings = manager(tmp_path, secrets)

    settings.save(
        provider="deepseek", api_key="sk-replacement", clear_api_key=True
    )

    assert settings.deepseek_key() == "sk-replacement"
    assert settings.snapshot().provider == "deepseek"


@pytest.mark.parametrize("key", ["sk-secret\nX-Evil: yes", "sk-secret\rmore", "sk secret"])
def test_api_key_rejects_control_characters_and_whitespace_without_echo(tmp_path, key):
    with pytest.raises(ModelSettingsError) as caught:
        manager(tmp_path).save(provider="deepseek", api_key=key)
    assert str(caught.value) == "DeepSeek API Key 格式无效，请重新填写。"
    assert "sk-secret" not in str(caught.value)


@pytest.mark.parametrize("provider", ["openai", "gemini", "", "DeepSeek"])
def test_only_codex_and_deepseek_are_valid_providers(tmp_path, provider):
    with pytest.raises(ModelSettingsError, match="模型"):
        manager(tmp_path).save(provider=provider)


@pytest.mark.parametrize(
    "url",
    [
        "http://api.deepseek.com",
        "https://user:pass@api.deepseek.com",
        "https://api.deepseek.com?key=secret",
        "https://api.deepseek.com/#fragment",
        "not-a-url",
    ],
)
def test_deepseek_endpoint_must_be_a_clean_https_url(tmp_path, url):
    with pytest.raises(ModelSettingsError, match="HTTPS"):
        manager(tmp_path).save(deepseek_base_url=url)


def test_selecting_deepseek_requires_a_new_or_saved_key(tmp_path):
    settings = manager(tmp_path)

    with pytest.raises(ModelSettingsError, match="API Key"):
        settings.save(provider="deepseek")


def test_empty_model_name_is_rejected(tmp_path):
    with pytest.raises(ModelSettingsError, match="模型名称"):
        manager(tmp_path).save(deepseek_model="  ")


@pytest.mark.skipif(os.name != "nt", reason="Windows DPAPI integration")
def test_default_windows_store_encrypts_secret_at_rest(tmp_path):
    first = ModelSettingsManager(tmp_path)
    first.save(provider="deepseek", api_key="sk-dpapi-plaintext")

    assert ModelSettingsManager(tmp_path).deepseek_key() == "sk-dpapi-plaintext"
    assert b"sk-dpapi-plaintext" not in b"".join(
        path.read_bytes() for path in tmp_path.iterdir() if path.is_file()
    )
