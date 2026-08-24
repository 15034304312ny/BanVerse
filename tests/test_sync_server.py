from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from deepseek_cli._version import __version__
from deepseek_cli.sync_protocol import bearer_credential
from deepseek_cli.sync_server import (
    SyncAccountExistsError,
    SyncAuthenticationError,
    SyncServerStore,
    create_app,
)


def _event(
    entity_id: str,
    *,
    event_id: str | None = None,
    operation: str = "upsert",
    base_revision: int = 0,
    title: str = "第一台设备",
) -> dict:
    payload = {"id": entity_id, "title": title} if operation == "upsert" else {}
    return {
        "event_id": event_id or uuid4().hex,
        "entity_type": "conversation",
        "entity_id": entity_id,
        "operation": operation,
        "base_revision": base_revision,
        "updated_at": "2026-08-24T12:00:00+00:00",
        "payload": payload,
        "media": {},
    }


def test_account_auth_push_pull_idempotency_conflict_and_tombstone(tmp_path):
    store = SyncServerStore(tmp_path / "sync.db", tmp_path / "media")
    account = store.create_account("测试账户")
    credential = bearer_credential(account["account_id"], account["token"])

    assert store.authenticate(credential) == account["account_id"]
    with pytest.raises(SyncAuthenticationError):
        store.authenticate(f"{account['account_id']}.invalid-token")

    entity_id = str(uuid4())
    first = _event(entity_id)
    accepted = store.push(account["account_id"], "device-one", "电脑", [first])
    assert accepted["results"][0]["status"] == "accepted"
    first_revision = accepted["results"][0]["revision"]

    repeated = store.push(account["account_id"], "device-one", "电脑", [first])
    assert repeated["results"] == accepted["results"]
    assert len(store.pull(account["account_id"], 0)["events"]) == 1

    same_content = _event(entity_id)
    same_content["payload"]["created_at"] = "另一台设备的本地创建时间"
    deduplicated = store.push(
        account["account_id"], "device-two", "手机", [same_content]
    )
    assert deduplicated["results"][0] == {
        "event_id": same_content["event_id"],
        "status": "accepted",
        "revision": first_revision,
    }
    assert len(store.pull(account["account_id"], 0)["events"]) == 1

    stale = _event(entity_id, title="第二台设备的离线修改")
    conflict = store.push(account["account_id"], "device-two", "手机", [stale])
    result = conflict["results"][0]
    assert result["status"] == "conflict"
    assert result["revision"] == first_revision
    assert result["current"]["payload"]["title"] == "第一台设备"

    deletion = _event(entity_id, operation="delete", base_revision=0)
    deleted = store.push(
        account["account_id"], "device-two", "手机", [deletion]
    )
    assert deleted["results"][0]["status"] == "accepted"
    changes = store.pull(account["account_id"], 0)["events"]
    assert [event["operation"] for event in changes] == ["upsert", "delete"]
    assert changes[-1]["revision"] > first_revision


def test_media_is_hash_verified_and_isolated_by_account(tmp_path):
    store = SyncServerStore(tmp_path / "sync.db", tmp_path / "media")
    first = store.create_account()
    second = store.create_account()
    content = b"banverse-image-content"
    digest = hashlib.sha256(content).hexdigest()

    store.put_media(first["account_id"], digest, content)

    assert store.get_media(first["account_id"], digest) == content
    assert store.get_media(second["account_id"], digest) is None
    with pytest.raises(ValueError, match="SHA-256"):
        store.put_media(first["account_id"], "0" * 64, content)


def test_proactive_message_lease_allows_only_one_device(tmp_path):
    store = SyncServerStore(tmp_path / "sync.db", tmp_path / "media")
    account_id = store.create_account()["account_id"]

    first = store.claim_lease(
        account_id, "device-one", "proactive", "conversation-1", 180
    )
    second = store.claim_lease(
        account_id, "device-two", "proactive", "conversation-1", 180
    )
    renewed = store.claim_lease(
        account_id, "device-one", "proactive", "conversation-1", 180
    )

    assert first["acquired"] is True
    assert second["acquired"] is False
    assert renewed["acquired"] is True


def test_password_account_login_logout_and_legacy_upgrade(tmp_path):
    store = SyncServerStore(
        tmp_path / "auth.db", tmp_path / "auth-media", session_days=30
    )
    registered = store.register_account(
        "BanVerse用户",
        "safe-password-2026",
        "测试用户",
        device_name="电脑",
    )
    credential = bearer_credential(
        registered["account_id"], registered["token"]
    )

    assert registered["username"] == "BanVerse用户"
    assert registered["expires_at"] > 0
    assert store.authenticate(credential) == registered["account_id"]
    profile = store.account_profile(registered["account_id"])
    assert profile["display_name"] == "测试用户"
    with pytest.raises(SyncAccountExistsError):
        store.register_account("banverse用户", "another-password")
    with pytest.raises(SyncAuthenticationError, match="用户名或密码"):
        store.login_account("BanVerse用户", "wrong-password")

    logged_in = store.login_account(
        "banverse用户", "safe-password-2026", device_name="手机"
    )
    login_credential = bearer_credential(
        logged_in["account_id"], logged_in["token"]
    )
    assert store.authenticate(login_credential) == registered["account_id"]
    assert store.logout(login_credential) == {"revoked": True}
    with pytest.raises(SyncAuthenticationError, match="登录已失效"):
        store.authenticate(login_credential)

    legacy = store.create_account("旧版账户")
    legacy_credential = bearer_credential(legacy["account_id"], legacy["token"])
    upgraded = store.upgrade_account(
        legacy["account_id"],
        "旧账户用户",
        "upgraded-password",
        device_name="电脑",
    )
    assert upgraded["account_id"] == legacy["account_id"]
    with pytest.raises(SyncAuthenticationError, match="登录已失效"):
        store.authenticate(legacy_credential)
    relogin = store.login_account("旧账户用户", "upgraded-password")
    assert relogin["account_id"] == legacy["account_id"]

    with store._connect() as connection:
        row = connection.execute(
            "SELECT password_hash, password_salt FROM accounts WHERE id = ?",
            (registered["account_id"],),
        ).fetchone()
    assert row["password_hash"] != "safe-password-2026"
    assert len(row["password_hash"]) == 64
    assert len(row["password_salt"]) == 32


def test_fastapi_health_account_and_authenticated_pull(tmp_path, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    monkeypatch.delenv("BANVERSE_SYNC_REGISTRATION_SECRET", raising=False)
    app = create_app(tmp_path / "http.db", tmp_path / "http-media")
    with TestClient(app) as client:
        health = client.get("/v1/health")
        account = client.post("/v1/accounts", json={"display_name": "双端测试"})
        unauthorized = client.get("/v1/sync/pull")
        credential = bearer_credential(
            account.json()["account_id"], account.json()["token"]
        )
        pulled = client.get(
            "/v1/sync/pull",
            headers={"Authorization": f"Bearer {credential}"},
        )

    assert health.status_code == 200
    assert health.json()["protocol"] == 1
    assert health.json()["server_version"] == __version__
    assert health.json()["password_auth"] is True
    assert health.json()["registration_requires_invite"] is False
    assert account.status_code == 200
    assert unauthorized.status_code == 401
    assert pulled.status_code == 200
    assert pulled.json()["events"] == []


def test_fastapi_password_registration_login_profile_and_logout(
    tmp_path, monkeypatch
):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient

    monkeypatch.setenv("BANVERSE_SYNC_REGISTRATION_SECRET", "invite-2026")
    app = create_app(tmp_path / "auth-http.db", tmp_path / "auth-http-media")
    with TestClient(app) as client:
        denied = client.post(
            "/v1/auth/register",
            json={"username": "用户一号", "password": "password-2026"},
        )
        registered = client.post(
            "/v1/auth/register",
            headers={"X-Registration-Secret": "invite-2026"},
            json={
                "username": "用户一号",
                "password": "password-2026",
                "display_name": "用户",
                "device_name": "电脑",
            },
        )
        bad_login = client.post(
            "/v1/auth/login",
            json={"username": "用户一号", "password": "wrong-pass"},
        )
        logged_in = client.post(
            "/v1/auth/login",
            json={"username": "用户一号", "password": "password-2026"},
        )
        body = logged_in.json()
        auth = {
            "Authorization": (
                f"Bearer {bearer_credential(body['account_id'], body['token'])}"
            )
        }
        profile = client.get("/v1/auth/me", headers=auth)
        logged_out = client.post("/v1/auth/logout", headers=auth, json={})
        expired = client.get("/v1/auth/me", headers=auth)

    assert denied.status_code == 403
    assert registered.status_code == 200
    assert bad_login.status_code == 401
    assert logged_in.status_code == 200
    assert profile.json()["username"] == "用户一号"
    assert logged_out.json() == {"revoked": True}
    assert expired.status_code == 401
