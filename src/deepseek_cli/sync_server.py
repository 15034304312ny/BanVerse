"""可自托管的 BanVerse 中央同步服务。

服务端只负责账户鉴权、统一修订号、冲突检测、删除墓碑和媒体对象。
默认监听回环地址；公开部署应置于 HTTPS 反向代理之后。
"""

import argparse
import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from ._version import __version__
from .sync_protocol import (
    MAX_SYNC_EVENTS,
    MAX_SYNC_MEDIA_BYTES,
    MAX_SYNC_PAYLOAD_BYTES,
    SYNC_PROTOCOL_VERSION,
    split_bearer_credential,
    utc_now,
    validate_event,
    validate_identifier,
    validate_sha256,
)


class SyncAuthenticationError(RuntimeError):
    pass


class SyncServerStore:
    """SQLite 元数据与按账户隔离的内容寻址媒体存储。"""

    def __init__(self, database_path: str | Path, media_root: str | Path) -> None:
        self.database_path = Path(database_path)
        self.media_root = Path(media_root)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.media_root.mkdir(parents=True, exist_ok=True)
        self._migrate()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        try:
            yield connection
        except BaseException:
            connection.rollback()
            raise
        else:
            connection.commit()
        finally:
            connection.close()

    def _migrate(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS accounts (
                    id TEXT PRIMARY KEY,
                    token_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS devices (
                    account_id TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    last_seen_at TEXT NOT NULL,
                    PRIMARY KEY(account_id, device_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS entities (
                    account_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    revision INTEGER NOT NULL,
                    deleted INTEGER NOT NULL DEFAULT 0,
                    payload_json TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_device_id TEXT NOT NULL,
                    PRIMARY KEY(account_id, entity_type, entity_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS changes (
                    revision INTEGER PRIMARY KEY AUTOINCREMENT,
                    account_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    source_device_id TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    operation TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    media_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(account_id, event_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_changes_account_revision
                    ON changes(account_id, revision);
                CREATE TABLE IF NOT EXISTS event_receipts (
                    account_id TEXT NOT NULL,
                    event_id TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    PRIMARY KEY(account_id, event_id),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                        ON DELETE CASCADE
                );
                CREATE TABLE IF NOT EXISTS leases (
                    account_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    lease_key TEXT NOT NULL,
                    device_id TEXT NOT NULL,
                    expires_at REAL NOT NULL,
                    PRIMARY KEY(account_id, scope, lease_key),
                    FOREIGN KEY(account_id) REFERENCES accounts(id)
                        ON DELETE CASCADE
                );
                """
            )

    @staticmethod
    def _token_hash(account_id: str, token: str) -> str:
        return hashlib.sha256(f"{account_id}.{token}".encode()).hexdigest()

    def create_account(self, display_name: str = "") -> dict[str, str]:
        account_id = secrets.token_urlsafe(18)
        token = secrets.token_urlsafe(32)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO accounts(id, token_hash, display_name, created_at)
                   VALUES (?, ?, ?, ?)""",
                (
                    account_id,
                    self._token_hash(account_id, token),
                    " ".join(display_name.split())[:80],
                    utc_now(),
                ),
            )
        return {"account_id": account_id, "token": token}

    def authenticate(self, credential: str) -> str:
        try:
            account_id, token = split_bearer_credential(credential)
        except ValueError as exc:
            raise SyncAuthenticationError("同步凭据无效。") from exc
        with self._connect() as connection:
            row = connection.execute(
                "SELECT token_hash FROM accounts WHERE id = ?", (account_id,)
            ).fetchone()
        expected = self._token_hash(account_id, token)
        if row is None or not hmac.compare_digest(row["token_hash"], expected):
            raise SyncAuthenticationError("同步凭据无效。")
        return account_id

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "revision": int(row["revision"]),
            "event_id": row["event_id"],
            "source_device_id": row["source_device_id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "operation": row["operation"],
            "payload": json.loads(row["payload_json"]),
            "media": json.loads(row["media_json"]),
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _canonical_event(row: sqlite3.Row | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "revision": int(row["revision"]),
            "source_device_id": row["source_device_id"],
            "entity_type": row["entity_type"],
            "entity_id": row["entity_id"],
            "operation": "delete" if row["deleted"] else "upsert",
            "payload": json.loads(row["payload_json"]),
            "media": json.loads(row["media_json"]),
            "updated_at": row["updated_at"],
        }

    @staticmethod
    def _same_entity_content(current: sqlite3.Row, event: dict) -> bool:
        if current["deleted"] or event["operation"] != "upsert":
            return False
        current_payload = json.loads(current["payload_json"])
        incoming_payload = dict(event["payload"])
        for timestamp_field in ("created_at", "updated_at"):
            current_payload.pop(timestamp_field, None)
            incoming_payload.pop(timestamp_field, None)
        return current_payload == incoming_payload and json.loads(
            current["media_json"]
        ) == event["media"]

    def push(
        self,
        account_id: str,
        device_id: str,
        device_name: str,
        events: list[dict],
    ) -> dict[str, Any]:
        device = validate_identifier(device_id, "设备 ID")
        if len(events) > MAX_SYNC_EVENTS:
            raise ValueError("单次同步事件数量过多。")
        normalized = [validate_event(event) for event in events]
        for event in normalized:
            for descriptor in event["media"].values():
                if not self._media_path(
                    account_id, descriptor["sha256"]
                ).is_file():
                    raise ValueError("同步事件引用的媒体尚未上传。")
        encoded_size = len(
            json.dumps(normalized, ensure_ascii=False, separators=(",", ":")).encode()
        )
        if encoded_size > MAX_SYNC_PAYLOAD_BYTES:
            raise ValueError("单次同步事件内容过大。")

        results: list[dict[str, Any]] = []
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """INSERT INTO devices(account_id, device_id, name, last_seen_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(account_id, device_id) DO UPDATE SET
                       name = excluded.name,
                       last_seen_at = excluded.last_seen_at""",
                (account_id, device, " ".join(device_name.split())[:120], utc_now()),
            )
            for event in normalized:
                receipt = connection.execute(
                    """SELECT result_json FROM event_receipts
                       WHERE account_id = ? AND event_id = ?""",
                    (account_id, event["event_id"]),
                ).fetchone()
                if receipt is not None:
                    results.append(json.loads(receipt["result_json"]))
                    continue

                current = connection.execute(
                    """SELECT * FROM entities WHERE account_id = ?
                       AND entity_type = ? AND entity_id = ?""",
                    (account_id, event["entity_type"], event["entity_id"]),
                ).fetchone()
                current_revision = int(current["revision"]) if current else 0
                conflict = (
                    event["operation"] != "delete"
                    and event["base_revision"] != current_revision
                )
                if conflict and current is not None and self._same_entity_content(
                    current, event
                ):
                    result = {
                        "event_id": event["event_id"],
                        "status": "accepted",
                        "revision": current_revision,
                    }
                elif conflict:
                    result = {
                        "event_id": event["event_id"],
                        "status": "conflict",
                        "revision": current_revision,
                        "current": self._canonical_event(current),
                    }
                else:
                    cursor = connection.execute(
                        """INSERT INTO changes(
                               account_id, event_id, source_device_id,
                               entity_type, entity_id, operation, payload_json,
                               media_json, updated_at, created_at
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (
                            account_id,
                            event["event_id"],
                            device,
                            event["entity_type"],
                            event["entity_id"],
                            event["operation"],
                            json.dumps(event["payload"], ensure_ascii=False),
                            json.dumps(event["media"], ensure_ascii=False),
                            event["updated_at"],
                            utc_now(),
                        ),
                    )
                    revision = int(cursor.lastrowid)
                    connection.execute(
                        """INSERT INTO entities(
                               account_id, entity_type, entity_id, revision,
                               deleted, payload_json, media_json, updated_at,
                               source_device_id
                           ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                           ON CONFLICT(account_id, entity_type, entity_id)
                           DO UPDATE SET
                               revision = excluded.revision,
                               deleted = excluded.deleted,
                               payload_json = excluded.payload_json,
                               media_json = excluded.media_json,
                               updated_at = excluded.updated_at,
                               source_device_id = excluded.source_device_id""",
                        (
                            account_id,
                            event["entity_type"],
                            event["entity_id"],
                            revision,
                            1 if event["operation"] == "delete" else 0,
                            json.dumps(event["payload"], ensure_ascii=False),
                            json.dumps(event["media"], ensure_ascii=False),
                            event["updated_at"],
                            device,
                        ),
                    )
                    result = {
                        "event_id": event["event_id"],
                        "status": "accepted",
                        "revision": revision,
                    }
                connection.execute(
                    "INSERT INTO event_receipts VALUES (?, ?, ?)",
                    (
                        account_id,
                        event["event_id"],
                        json.dumps(result, ensure_ascii=False),
                    ),
                )
                results.append(result)
            cursor = connection.execute(
                "SELECT COALESCE(MAX(revision), 0) FROM changes WHERE account_id = ?",
                (account_id,),
            ).fetchone()[0]
        return {"cursor": int(cursor), "results": results}

    def pull(
        self, account_id: str, cursor: int, limit: int = MAX_SYNC_EVENTS
    ) -> dict[str, Any]:
        safe_cursor = max(0, int(cursor))
        safe_limit = max(1, min(int(limit), MAX_SYNC_EVENTS))
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM changes WHERE account_id = ? AND revision > ?
                   ORDER BY revision ASC LIMIT ?""",
                (account_id, safe_cursor, safe_limit),
            ).fetchall()
        events: list[dict[str, Any]] = []
        encoded_size = 2
        for row in rows:
            event = self._event_from_row(row)
            event_size = len(
                json.dumps(
                    event, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
            )
            if events and encoded_size + event_size + 1 > MAX_SYNC_PAYLOAD_BYTES:
                break
            events.append(event)
            encoded_size += event_size + (1 if len(events) > 1 else 0)
        next_cursor = int(events[-1]["revision"]) if events else safe_cursor
        return {
            "cursor": next_cursor,
            "has_more": len(events) < len(rows) or len(rows) == safe_limit,
            "events": events,
        }

    def put_media(self, account_id: str, digest: str, content: bytes) -> None:
        sha256 = validate_sha256(digest)
        if not content or len(content) > MAX_SYNC_MEDIA_BYTES:
            raise ValueError("媒体文件为空或超过大小限制。")
        if not hmac.compare_digest(hashlib.sha256(content).hexdigest(), sha256):
            raise ValueError("媒体内容与 SHA-256 不匹配。")
        target = self._media_path(account_id, sha256)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            return
        partial = target.with_name(f".{digest}.{secrets.token_hex(8)}.part")
        try:
            partial.write_bytes(content)
            partial.replace(target)
        finally:
            partial.unlink(missing_ok=True)

    def get_media(self, account_id: str, digest: str) -> bytes | None:
        target = self._media_path(account_id, validate_sha256(digest))
        try:
            return target.read_bytes()
        except FileNotFoundError:
            return None

    def _media_path(self, account_id: str, digest: str) -> Path:
        account = validate_identifier(account_id, "同步账户 ID")
        return self.media_root / account / digest[:2] / digest

    def claim_lease(
        self,
        account_id: str,
        device_id: str,
        scope: str,
        lease_key: str,
        ttl_seconds: int,
    ) -> dict[str, Any]:
        device = validate_identifier(device_id, "设备 ID")
        safe_scope = validate_identifier(scope, "租约范围")
        safe_key = validate_identifier(lease_key, "租约键")
        ttl = max(15, min(int(ttl_seconds), 600))
        now = time.time()
        expires_at = now + ttl
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT device_id, expires_at FROM leases
                   WHERE account_id = ? AND scope = ? AND lease_key = ?""",
                (account_id, safe_scope, safe_key),
            ).fetchone()
            acquired = row is None or row["expires_at"] <= now or row["device_id"] == device
            if acquired:
                connection.execute(
                    """INSERT INTO leases VALUES (?, ?, ?, ?, ?)
                       ON CONFLICT(account_id, scope, lease_key) DO UPDATE SET
                           device_id = excluded.device_id,
                           expires_at = excluded.expires_at""",
                    (account_id, safe_scope, safe_key, device, expires_at),
                )
            else:
                expires_at = float(row["expires_at"])
        return {"acquired": acquired, "expires_at": expires_at}


def create_app(
    database_path: str | Path | None = None,
    media_root: str | Path | None = None,
):
    """创建 FastAPI 应用；导入客户端代码时不会强制安装服务端依赖。"""

    try:
        from fastapi import FastAPI, HTTPException, Request, Response
    except ImportError as exc:  # pragma: no cover - 仅缺少可选依赖时触发
        raise RuntimeError(
            "请先安装同步服务依赖：pip install '.[sync-server]'"
        ) from exc

    data_root = Path(
        os.environ.get("BANVERSE_SYNC_DATA", "")
        or Path.home() / ".banverse-sync"
    )
    store = SyncServerStore(
        database_path or data_root / "sync.db",
        media_root or data_root / "media",
    )
    registration_secret = os.environ.get(
        "BANVERSE_SYNC_REGISTRATION_SECRET", ""
    ).strip()
    app = FastAPI(title="BanVerse Sync", version=str(SYNC_PROTOCOL_VERSION))
    app.state.store = store

    def account_from_request(request: Request) -> str:
        header = request.headers.get("authorization", "")
        if not header.lower().startswith("bearer "):
            raise HTTPException(status_code=401, detail="缺少同步凭据。")
        try:
            return store.authenticate(header[7:].strip())
        except SyncAuthenticationError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc

    @app.get("/v1/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "protocol": SYNC_PROTOCOL_VERSION,
            "server_version": __version__,
        }

    @app.post("/v1/accounts")
    async def create_account(request: Request) -> dict[str, Any]:
        if registration_secret and not hmac.compare_digest(
            request.headers.get("x-registration-secret", ""),
            registration_secret,
        ):
            raise HTTPException(status_code=403, detail="账户注册已受保护。")
        try:
            payload = await request.json()
        except (ValueError, json.JSONDecodeError):
            payload = {}
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="请求格式无效。")
        return store.create_account(str(payload.get("display_name", "")))

    @app.post("/v1/sync/push")
    async def push(request: Request) -> dict[str, Any]:
        account_id = account_from_request(request)
        try:
            payload = await request.json()
            if not isinstance(payload, dict):
                raise ValueError("请求格式无效。")
            return store.push(
                account_id,
                str(payload.get("device_id", "")),
                str(payload.get("device_name", "")),
                payload.get("events", []),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/v1/sync/pull")
    async def pull(request: Request, cursor: int = 0, limit: int = MAX_SYNC_EVENTS):
        account_id = account_from_request(request)
        return store.pull(account_id, cursor, limit)

    @app.put("/v1/media/{digest}")
    async def put_media(digest: str, request: Request) -> dict[str, bool]:
        account_id = account_from_request(request)
        content = await request.body()
        try:
            store.put_media(account_id, digest, content)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {"stored": True}

    @app.get("/v1/media/{digest}")
    async def get_media(digest: str, request: Request):
        account_id = account_from_request(request)
        try:
            content = store.get_media(account_id, digest)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if content is None:
            raise HTTPException(status_code=404, detail="媒体不存在。")
        return Response(content=content, media_type="application/octet-stream")

    @app.post("/v1/leases/{scope}/{lease_key}")
    async def claim_lease(scope: str, lease_key: str, request: Request):
        account_id = account_from_request(request)
        try:
            payload = await request.json()
            return store.claim_lease(
                account_id,
                str(payload.get("device_id", "")),
                scope,
                lease_key,
                int(payload.get("ttl_seconds", 120)),
            )
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="运行 BanVerse 自托管同步服务")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--data", default=os.environ.get("BANVERSE_SYNC_DATA", ""))
    args = parser.parse_args(argv)
    if args.data:
        os.environ["BANVERSE_SYNC_DATA"] = str(Path(args.data).expanduser())
    try:
        import uvicorn
    except ImportError as exc:  # pragma: no cover - 可选依赖提示
        raise SystemExit(
            "请先安装同步服务依赖：pip install '.[sync-server]'"
        ) from exc
    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
