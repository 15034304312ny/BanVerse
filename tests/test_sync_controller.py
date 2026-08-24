from __future__ import annotations

from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import ChatRepository, SettingsRepository
from deepseek_cli.desktop.sync_client import SyncRepository
from deepseek_cli.desktop.sync_controller import SyncController


class Credentials:
    def __init__(self, token: str = "") -> None:
        self.token = token

    def get_sync_token(self) -> str:
        return self.token

    def clear_sync_token(self) -> None:
        self.token = ""


def test_controller_config_and_disconnect_preserve_local_chats(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    conversation = chats.create_conversation(title="只保留在本机也不能丢失")
    settings = SettingsRepository(database)
    credentials = Credentials("token-12345678")
    settings.set("sync_enabled", "true")
    settings.set("sync_server_url", "https://sync.example.test")
    settings.set("sync_account_id", "account-123456")
    controller = SyncController(
        settings, credentials, database.path, tmp_path / "data", parent=qapp
    )
    SyncRepository(database).enable_capture()

    assert controller.enabled is True
    assert settings.get("sync_device_id")
    assert settings.get("sync_device_name")

    controller.disconnect_account()

    assert chats.get_conversation(conversation.id) is not None
    assert credentials.token == ""
    assert settings.get_bool("sync_enabled") is False
    assert settings.get("sync_account_id") == ""
    assert database.connection.execute(
        "SELECT COUNT(*) FROM sync_outbox"
    ).fetchone()[0] == 0
    database.close()


def test_disabled_controller_grants_local_proactive_lease(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    controller = SyncController(
        SettingsRepository(database),
        Credentials(),
        database.path,
        tmp_path / "data",
        parent=qapp,
    )
    claims = []
    controller.proactive_claimed.connect(
        lambda conversation_id, acquired: claims.append(
            (conversation_id, acquired)
        )
    )

    controller.claim_proactive("conversation-123")

    assert claims == [("conversation-123", True)]
    database.close()


def test_enabled_controller_debounces_local_changes(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    settings = SettingsRepository(database)
    settings.set("sync_enabled", "true")
    settings.set("sync_server_url", "https://sync.example.test")
    settings.set("sync_account_id", "account-123456")
    controller = SyncController(
        settings,
        Credentials("token-12345678"),
        database.path,
        tmp_path / "data",
        parent=qapp,
    )

    controller.schedule_sync()

    assert controller._timer.interval() == 15_000
    assert controller._debounce_timer.isActive()
    controller.shutdown()
    database.close()
