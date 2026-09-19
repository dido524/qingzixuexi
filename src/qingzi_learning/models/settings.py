from __future__ import annotations

from dataclasses import asdict, dataclass
import ctypes
from ctypes import wintypes
import json
import os
from pathlib import Path
import tempfile
import threading
from typing import Protocol
from urllib.parse import urlsplit


DEFAULT_DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_DEEPSEEK_MODEL = "deepseek-flash"


class ModelSettingsError(ValueError):
    """A safe, user-presentable settings validation error."""


@dataclass(frozen=True)
class ModelSettings:
    provider: str = "codex"
    deepseek_base_url: str = DEFAULT_DEEPSEEK_BASE_URL
    deepseek_model: str = DEFAULT_DEEPSEEK_MODEL


class SecretStore(Protocol):
    def read(self) -> str | None: ...

    def write(self, value: str) -> None: ...

    def clear(self) -> None: ...


class MemorySecretStore:
    """Small injectable store used by tests and non-Windows embedding."""

    def __init__(self, value: str | None = None):
        self._value = value

    def read(self) -> str | None:
        return self._value

    def write(self, value: str) -> None:
        self._value = value

    def clear(self) -> None:
        self._value = None


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _windows_dpapi():
    """Return 64-bit-safe Win32 functions with explicit pointer signatures."""
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    blob_pointer = ctypes.POINTER(_DataBlob)
    crypt32.CryptProtectData.argtypes = [
        blob_pointer, wintypes.LPCWSTR, blob_pointer, wintypes.LPVOID,
        wintypes.LPVOID, wintypes.DWORD, blob_pointer,
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    crypt32.CryptUnprotectData.argtypes = [
        blob_pointer, wintypes.LPVOID, blob_pointer, wintypes.LPVOID,
        wintypes.LPVOID, wintypes.DWORD, blob_pointer,
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [wintypes.HLOCAL]
    kernel32.LocalFree.restype = wintypes.HLOCAL
    return crypt32, kernel32


class DpapiSecretStore:
    """Protect a single secret for the current Windows user with DPAPI."""

    _CRYPTPROTECT_UI_FORBIDDEN = 0x1

    def __init__(self, path: Path):
        self._path = Path(path)

    @staticmethod
    def _blob(data: bytes) -> tuple[_DataBlob, ctypes.Array]:
        buffer = ctypes.create_string_buffer(data)
        blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_byte)))
        return blob, buffer

    @classmethod
    def _protect(cls, data: bytes) -> bytes:
        if os.name != "nt":
            raise ModelSettingsError("当前系统不支持安全保存 API Key。")
        source, source_buffer = cls._blob(data)
        output = _DataBlob()
        crypt32, kernel32 = _windows_dpapi()
        ok = crypt32.CryptProtectData(
            ctypes.byref(source),
            "Qingzi DeepSeek API Key",
            None,
            None,
            None,
            cls._CRYPTPROTECT_UI_FORBIDDEN,
            ctypes.byref(output),
        )
        del source_buffer
        if not ok:
            raise ModelSettingsError("API Key 加密保存失败。")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    @classmethod
    def _unprotect(cls, data: bytes) -> bytes:
        if os.name != "nt":
            raise ModelSettingsError("当前系统不支持读取安全保存的 API Key。")
        source, source_buffer = cls._blob(data)
        output = _DataBlob()
        crypt32, kernel32 = _windows_dpapi()
        ok = crypt32.CryptUnprotectData(
            ctypes.byref(source), None, None, None, None,
            cls._CRYPTPROTECT_UI_FORBIDDEN, ctypes.byref(output),
        )
        del source_buffer
        if not ok:
            raise ModelSettingsError("已保存的 API Key 无法解密，请重新填写。")
        try:
            return ctypes.string_at(output.pbData, output.cbData)
        finally:
            kernel32.LocalFree(output.pbData)

    def read(self) -> str | None:
        if not self._path.exists():
            return None
        try:
            value = self._unprotect(self._path.read_bytes()).decode("utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            raise ModelSettingsError("已保存的 API Key 无法读取，请重新填写。") from exc
        return value or None

    def write(self, value: str) -> None:
        protected = self._protect(value.encode("utf-8"))
        self._path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write_bytes(self._path, protected)

    def clear(self) -> None:
        try:
            self._path.unlink()
        except FileNotFoundError:
            pass


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_temp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temp = Path(raw_temp)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def _validate(settings: ModelSettings) -> ModelSettings:
    provider = settings.provider.strip()
    endpoint = settings.deepseek_base_url.strip().rstrip("/")
    model = settings.deepseek_model.strip()
    if provider not in {"codex", "deepseek"}:
        raise ModelSettingsError("模型只能选择 Codex 或 DeepSeek。")
    parsed = urlsplit(endpoint)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ModelSettingsError("DeepSeek 接口地址必须是无账号、参数或片段的 HTTPS 地址。")
    if not model:
        raise ModelSettingsError("DeepSeek 模型名称不能为空。")
    return ModelSettings(provider=provider, deepseek_base_url=endpoint, deepseek_model=model)


class ModelSettingsManager:
    def __init__(self, app_data_root: Path, *, secret_store: SecretStore | None = None):
        root = Path(app_data_root)
        self._path = root / "model-settings.json"
        self._secret_store = secret_store or DpapiSecretStore(root / "deepseek-key.dpapi")
        self._lock = threading.RLock()
        self._settings = self._load()

    def _load(self) -> ModelSettings:
        if not self._path.exists():
            return ModelSettings()
        try:
            payload = json.loads(self._path.read_text("utf-8"))
            allowed = {"provider", "deepseek_base_url", "deepseek_model"}
            if not isinstance(payload, dict) or not set(payload).issubset(allowed):
                raise ValueError
            return _validate(ModelSettings(**payload))
        except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError) as exc:
            raise ModelSettingsError("模型设置文件损坏，请重新保存设置。") from exc

    def snapshot(self) -> ModelSettings:
        with self._lock:
            return self._settings

    def has_deepseek_key(self) -> bool:
        return self.deepseek_key() is not None

    def deepseek_key(self) -> str | None:
        with self._lock:
            value = self._secret_store.read()
            return value.strip() if value and value.strip() else None

    def save(
        self,
        *,
        provider: str | None = None,
        deepseek_base_url: str | None = None,
        deepseek_model: str | None = None,
        api_key: str | None = None,
        clear_api_key: bool = False,
    ) -> ModelSettings:
        with self._lock:
            current = self._settings
            candidate = _validate(ModelSettings(
                provider=current.provider if provider is None else provider,
                deepseek_base_url=(
                    current.deepseek_base_url if deepseek_base_url is None else deepseek_base_url
                ),
                deepseek_model=current.deepseek_model if deepseek_model is None else deepseek_model,
            ))
            submitted_key = (api_key or "").strip()
            if submitted_key and any(ord(char) < 33 or ord(char) > 126 for char in submitted_key):
                raise ModelSettingsError("DeepSeek API Key 格式无效，请重新填写。")
            if submitted_key:
                has_key_after_save = True
            elif clear_api_key:
                has_key_after_save = False
            elif candidate.provider == "deepseek":
                has_key_after_save = self.deepseek_key() is not None
            else:
                # Codex does not depend on the saved DeepSeek secret. This also
                # leaves the settings screen usable when an old DPAPI blob is bad.
                has_key_after_save = False
            if candidate.provider == "deepseek" and not has_key_after_save:
                raise ModelSettingsError("使用 DeepSeek 前请填写 API Key。")

            if submitted_key:
                self._secret_store.write(submitted_key)
            elif clear_api_key:
                self._secret_store.clear()

            payload = json.dumps(asdict(candidate), ensure_ascii=False, indent=2).encode("utf-8")
            _atomic_write_bytes(self._path, payload)
            self._settings = candidate
            return candidate
