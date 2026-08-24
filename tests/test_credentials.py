from __future__ import annotations

import pytest
from PySide6.QtCore import QSettings

from deepseek_cli.desktop.security.credentials import (
    ACCOUNT_NAME,
    GOOGLE_IMAGE_ACCOUNT_NAME,
    GRSAI_IMAGE_ACCOUNT_NAME,
    GRSAI_TEXT_ACCOUNT_NAME,
    IMAGE_ACCOUNT_NAME,
    SERVICE_NAME,
    SILICONFLOW_ACCOUNT_NAME,
    SILICONFLOW_IMAGE_ACCOUNT_NAME,
    SILICONFLOW_TTS_ACCOUNT_NAME,
    SYNC_TOKEN_ACCOUNT_NAME,
    XFYUN_TTS_API_KEY_ACCOUNT_NAME,
    XFYUN_TTS_API_SECRET_ACCOUNT_NAME,
    XFYUN_TTS_PASSWORD_ACCOUNT_NAME,
    CredentialStore,
    QtSettingsCredentialBackend,
)


class FakeKeyring:
    def __init__(self):
        self.values = {}

    def get_password(self, service, account):
        assert service == SERVICE_NAME
        assert account in {
            ACCOUNT_NAME,
            IMAGE_ACCOUNT_NAME,
            GOOGLE_IMAGE_ACCOUNT_NAME,
            SILICONFLOW_ACCOUNT_NAME,
            GRSAI_TEXT_ACCOUNT_NAME,
            SILICONFLOW_IMAGE_ACCOUNT_NAME,
            GRSAI_IMAGE_ACCOUNT_NAME,
            SILICONFLOW_TTS_ACCOUNT_NAME,
            XFYUN_TTS_API_KEY_ACCOUNT_NAME,
            XFYUN_TTS_API_SECRET_ACCOUNT_NAME,
            XFYUN_TTS_PASSWORD_ACCOUNT_NAME,
            SYNC_TOKEN_ACCOUNT_NAME,
        }
        return self.values.get(account)

    def set_password(self, service, account, value):
        assert service == SERVICE_NAME
        assert account in {
            ACCOUNT_NAME,
            IMAGE_ACCOUNT_NAME,
            GOOGLE_IMAGE_ACCOUNT_NAME,
            SILICONFLOW_ACCOUNT_NAME,
            GRSAI_TEXT_ACCOUNT_NAME,
            SILICONFLOW_IMAGE_ACCOUNT_NAME,
            GRSAI_IMAGE_ACCOUNT_NAME,
            SILICONFLOW_TTS_ACCOUNT_NAME,
            XFYUN_TTS_API_KEY_ACCOUNT_NAME,
            XFYUN_TTS_API_SECRET_ACCOUNT_NAME,
            XFYUN_TTS_PASSWORD_ACCOUNT_NAME,
            SYNC_TOKEN_ACCOUNT_NAME,
        }
        self.values[account] = value

    def delete_password(self, service, account):
        assert service == SERVICE_NAME
        assert account in {
            ACCOUNT_NAME,
            IMAGE_ACCOUNT_NAME,
            GOOGLE_IMAGE_ACCOUNT_NAME,
            SILICONFLOW_ACCOUNT_NAME,
            GRSAI_TEXT_ACCOUNT_NAME,
            SILICONFLOW_IMAGE_ACCOUNT_NAME,
            GRSAI_IMAGE_ACCOUNT_NAME,
            SILICONFLOW_TTS_ACCOUNT_NAME,
            XFYUN_TTS_API_KEY_ACCOUNT_NAME,
            XFYUN_TTS_API_SECRET_ACCOUNT_NAME,
            XFYUN_TTS_PASSWORD_ACCOUNT_NAME,
            SYNC_TOKEN_ACCOUNT_NAME,
        }
        self.values.pop(account, None)


def test_credentials_save_and_clear():
    backend = FakeKeyring()
    store = CredentialStore(backend)

    store.save_api_key("  secret  ")
    assert store.get_api_key() == "secret"
    store.clear_api_key()
    assert store.get_api_key() == ""

    store.save_image_api_key(" image-secret ")
    assert store.get_image_api_key() == "image-secret"
    assert store.get_api_key() == ""
    store.clear_image_api_key()
    assert store.get_image_api_key() == ""

    store.save_google_image_api_key(" google-secret ")
    assert store.get_google_image_api_key() == "google-secret"
    store.clear_google_image_api_key()
    assert store.get_google_image_api_key() == ""

    store.save_siliconflow_api_key(" siliconflow-secret ")
    assert store.get_siliconflow_api_key() == "siliconflow-secret"
    store.clear_siliconflow_api_key()
    assert store.get_siliconflow_api_key() == ""

    store.save_grsai_text_api_key(" grs-text-secret ")
    store.save_siliconflow_image_api_key(" sf-image-secret ")
    store.save_grsai_image_api_key(" grs-image-secret ")
    store.save_siliconflow_tts_api_key(" sf-tts-secret ")
    assert store.get_grsai_text_api_key() == "grs-text-secret"
    assert store.get_siliconflow_image_api_key() == "sf-image-secret"
    assert store.get_grsai_image_api_key() == "grs-image-secret"
    assert store.get_siliconflow_tts_api_key() == "sf-tts-secret"
    store.clear_grsai_text_api_key()
    store.clear_siliconflow_image_api_key()
    store.clear_grsai_image_api_key()
    store.clear_siliconflow_tts_api_key()
    assert store.get_grsai_text_api_key() == ""
    assert store.get_siliconflow_image_api_key() == ""
    assert store.get_grsai_image_api_key() == ""
    assert store.get_siliconflow_tts_api_key() == ""

    store.save_sync_token(" pairing-token ")
    assert store.get_sync_token() == "pairing-token"
    store.clear_sync_token()
    assert store.get_sync_token() == ""


def test_legacy_siliconflow_key_migrates_by_read_fallback():
    store = CredentialStore(FakeKeyring())

    store.save_siliconflow_api_key("legacy-shared-key")

    assert store.get_siliconflow_image_api_key() == "legacy-shared-key"
    assert store.get_siliconflow_tts_api_key() == "legacy-shared-key"
    store.clear_siliconflow_image_api_key()
    assert store.get_siliconflow_image_api_key() == ""
    assert store.get_siliconflow_tts_api_key() == "legacy-shared-key"

    store.save_xfyun_tts_api_password(" xfyun-password ")
    assert store.get_xfyun_tts_api_password() == "xfyun-password"
    store.clear_xfyun_tts_api_password()
    assert store.get_xfyun_tts_api_password() == ""

    store.save_xfyun_tts_api_key(" xfyun-key ")
    store.save_xfyun_tts_api_secret(" xfyun-secret ")
    assert store.get_xfyun_tts_api_key() == "xfyun-key"
    assert store.get_xfyun_tts_api_secret() == "xfyun-secret"
    store.clear_xfyun_tts_api_key()
    store.clear_xfyun_tts_api_secret()
    assert store.get_xfyun_tts_api_key() == ""
    assert store.get_xfyun_tts_api_secret() == ""


def test_credentials_reject_empty_key():
    with pytest.raises(ValueError):
        CredentialStore(FakeKeyring()).save_api_key("  ")


def test_credentials_keep_session_key_when_system_store_fails():
    class BrokenKeyring(FakeKeyring):
        def set_password(self, *_args):
            raise RuntimeError("credential backend failed")

    store = CredentialStore(BrokenKeyring())

    with pytest.raises(RuntimeError, match="本次运行期间有效"):
        store.save_api_key("session-secret")

    assert store.get_api_key() == "session-secret"
    store.clear_api_key()
    assert store.get_api_key() == ""


def test_qsettings_backend_persists_and_clears_credentials(tmp_path):
    path = tmp_path / "credentials.ini"
    backend = QtSettingsCredentialBackend(
        QSettings(str(path), QSettings.Format.IniFormat)
    )
    store = CredentialStore(backend)

    store.save_api_key("android-private-key")
    restored = CredentialStore(
        QtSettingsCredentialBackend(
            QSettings(str(path), QSettings.Format.IniFormat)
        )
    )

    assert restored.get_api_key() == "android-private-key"
    restored.clear_api_key()
    assert restored.get_api_key() == ""
