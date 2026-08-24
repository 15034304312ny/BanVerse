from __future__ import annotations

import hashlib
from uuid import uuid4

import pytest

from deepseek_cli._version import __version__
from deepseek_cli.sync_protocol import bearer_credential
from deepseek_cli.sync_server import (
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
    assert account.status_code == 200
    assert unauthorized.status_code == 401
    assert pulled.status_code == 200
    assert pulled.json()["events"] == []
