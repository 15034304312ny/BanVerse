"""通过系统凭据库按文本、图片和 TTS 能力分别保存凭据。"""

from __future__ import annotations

import importlib
from typing import Any

from PySide6.QtCore import QSettings

SERVICE_NAME = "DeepSeekChatDesktop"
ACCOUNT_NAME = "deepseek-api-key"
IMAGE_ACCOUNT_NAME = "openai-image-api-key"
GOOGLE_IMAGE_ACCOUNT_NAME = "google-gemini-image-api-key"
SILICONFLOW_ACCOUNT_NAME = "siliconflow-multimedia-api-key"
GRSAI_TEXT_ACCOUNT_NAME = "grsai-text-api-key"
SILICONFLOW_IMAGE_ACCOUNT_NAME = "siliconflow-image-api-key"
GRSAI_IMAGE_ACCOUNT_NAME = "grsai-image-api-key"
SILICONFLOW_TTS_ACCOUNT_NAME = "siliconflow-tts-api-key"
XFYUN_TTS_PASSWORD_ACCOUNT_NAME = "xfyun-super-tts-api-password"
XFYUN_TTS_API_KEY_ACCOUNT_NAME = "xfyun-super-tts-api-key"
XFYUN_TTS_API_SECRET_ACCOUNT_NAME = "xfyun-super-tts-api-secret"


class QtSettingsCredentialBackend:
    """Android/无 keyring 环境下使用应用私有设置目录持久化密钥。"""

    def __init__(self, settings: QSettings | None = None) -> None:
        self._settings = settings or QSettings(
            "DeepSeekChat", "DeepSeekChatCredentials"
        )

    @staticmethod
    def _key(service: str, account: str) -> str:
        return f"credentials/{service}/{account}"

    def get_password(self, service: str, account: str) -> str | None:
        value = self._settings.value(self._key(service, account), "")
        return str(value or "") or None

    def set_password(self, service: str, account: str, value: str) -> None:
        self._settings.setValue(self._key(service, account), value)
        self._settings.sync()
        if self._settings.status() != QSettings.Status.NoError:
            raise RuntimeError("无法写入应用私有凭据存储")

    def delete_password(self, service: str, account: str) -> None:
        self._settings.remove(self._key(service, account))
        self._settings.sync()


class CredentialStore:
    def __init__(self, backend: Any | None = None) -> None:
        if backend is None:
            try:
                backend = importlib.import_module("keyring")
            except ImportError:
                backend = QtSettingsCredentialBackend()
        self._backend = backend
        self._session_keys: dict[str, str] = {}

    def get_api_key(self) -> str:
        return self._get(ACCOUNT_NAME)

    def get_image_api_key(self) -> str:
        return self._get(IMAGE_ACCOUNT_NAME)

    def get_google_image_api_key(self) -> str:
        return self._get(GOOGLE_IMAGE_ACCOUNT_NAME)

    def get_siliconflow_api_key(self) -> str:
        return self._get(SILICONFLOW_ACCOUNT_NAME)

    def get_grsai_text_api_key(self) -> str:
        return self._get(GRSAI_TEXT_ACCOUNT_NAME)

    def get_siliconflow_image_api_key(self) -> str:
        return self._get(SILICONFLOW_IMAGE_ACCOUNT_NAME) or self._get(
            SILICONFLOW_ACCOUNT_NAME
        )

    def get_grsai_image_api_key(self) -> str:
        return self._get(GRSAI_IMAGE_ACCOUNT_NAME)

    def get_siliconflow_tts_api_key(self) -> str:
        return self._get(SILICONFLOW_TTS_ACCOUNT_NAME) or self._get(
            SILICONFLOW_ACCOUNT_NAME
        )

    def get_xfyun_tts_api_password(self) -> str:
        return self._get(XFYUN_TTS_PASSWORD_ACCOUNT_NAME)

    def get_xfyun_tts_api_key(self) -> str:
        return self._get(XFYUN_TTS_API_KEY_ACCOUNT_NAME)

    def get_xfyun_tts_api_secret(self) -> str:
        return self._get(XFYUN_TTS_API_SECRET_ACCOUNT_NAME)

    def _get(self, account: str) -> str:
        try:
            value = self._backend.get_password(SERVICE_NAME, account)
        except Exception:
            return self._session_keys.get(account, "")
        return (value or self._session_keys.get(account, "")).strip()

    def save_api_key(self, api_key: str) -> None:
        self._save(ACCOUNT_NAME, api_key)

    def save_image_api_key(self, api_key: str) -> None:
        self._save(IMAGE_ACCOUNT_NAME, api_key)

    def save_google_image_api_key(self, api_key: str) -> None:
        self._save(GOOGLE_IMAGE_ACCOUNT_NAME, api_key)

    def save_siliconflow_api_key(self, api_key: str) -> None:
        self._save(SILICONFLOW_ACCOUNT_NAME, api_key)

    def save_grsai_text_api_key(self, api_key: str) -> None:
        self._save(GRSAI_TEXT_ACCOUNT_NAME, api_key)

    def save_siliconflow_image_api_key(self, api_key: str) -> None:
        self._save(SILICONFLOW_IMAGE_ACCOUNT_NAME, api_key)

    def save_grsai_image_api_key(self, api_key: str) -> None:
        self._save(GRSAI_IMAGE_ACCOUNT_NAME, api_key)

    def save_siliconflow_tts_api_key(self, api_key: str) -> None:
        self._save(SILICONFLOW_TTS_ACCOUNT_NAME, api_key)

    def save_xfyun_tts_api_password(self, value: str) -> None:
        self._save(XFYUN_TTS_PASSWORD_ACCOUNT_NAME, value)

    def save_xfyun_tts_api_key(self, value: str) -> None:
        self._save(XFYUN_TTS_API_KEY_ACCOUNT_NAME, value)

    def save_xfyun_tts_api_secret(self, value: str) -> None:
        self._save(XFYUN_TTS_API_SECRET_ACCOUNT_NAME, value)

    def _save(self, account: str, api_key: str) -> None:
        value = api_key.strip()
        if not value:
            raise ValueError("API Key 不能为空")
        try:
            self._backend.set_password(SERVICE_NAME, account, value)
            self._session_keys.pop(account, None)
        except Exception as exc:
            self._session_keys[account] = value
            raise RuntimeError(
                "系统凭据库不可用；密钥仅在本次运行期间有效。"
            ) from exc

    def clear_api_key(self) -> None:
        self._clear(ACCOUNT_NAME)

    def clear_image_api_key(self) -> None:
        self._clear(IMAGE_ACCOUNT_NAME)

    def clear_google_image_api_key(self) -> None:
        self._clear(GOOGLE_IMAGE_ACCOUNT_NAME)

    def clear_siliconflow_api_key(self) -> None:
        self._clear(SILICONFLOW_ACCOUNT_NAME)

    def clear_grsai_text_api_key(self) -> None:
        self._clear(GRSAI_TEXT_ACCOUNT_NAME)

    def clear_siliconflow_image_api_key(self) -> None:
        self._preserve_legacy_siliconflow_key(SILICONFLOW_TTS_ACCOUNT_NAME)
        self._clear(SILICONFLOW_IMAGE_ACCOUNT_NAME)

    def clear_grsai_image_api_key(self) -> None:
        self._clear(GRSAI_IMAGE_ACCOUNT_NAME)

    def clear_siliconflow_tts_api_key(self) -> None:
        self._preserve_legacy_siliconflow_key(SILICONFLOW_IMAGE_ACCOUNT_NAME)
        self._clear(SILICONFLOW_TTS_ACCOUNT_NAME)

    def clear_xfyun_tts_api_password(self) -> None:
        self._clear(XFYUN_TTS_PASSWORD_ACCOUNT_NAME)

    def clear_xfyun_tts_api_key(self) -> None:
        self._clear(XFYUN_TTS_API_KEY_ACCOUNT_NAME)

    def clear_xfyun_tts_api_secret(self) -> None:
        self._clear(XFYUN_TTS_API_SECRET_ACCOUNT_NAME)

    def _clear(self, account: str) -> None:
        self._session_keys.pop(account, None)
        try:
            self._backend.delete_password(SERVICE_NAME, account)
        except Exception:
            pass

    def _preserve_legacy_siliconflow_key(self, account: str) -> None:
        """Split an old shared key before one capability clears its value."""

        legacy = self._get(SILICONFLOW_ACCOUNT_NAME)
        if not legacy:
            return
        if not self._get(account):
            try:
                self._save(account, legacy)
            except RuntimeError:
                # _save retained the value in the session fallback.
                pass
        self._clear(SILICONFLOW_ACCOUNT_NAME)
