"""BanVerse 本地优先同步客户端、SQLite 适配和媒体传输。"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen
from uuid import uuid4

from ..sync_protocol import (
    MAX_SYNC_EVENTS,
    MAX_SYNC_MEDIA_BYTES,
    MAX_SYNC_PAYLOAD_BYTES,
    bearer_credential,
    normalize_sync_url,
    utc_now,
)
from .data.database import Database


class SyncError(RuntimeError):
    pass


class SyncAuthenticationError(SyncError):
    pass


@dataclass(frozen=True, slots=True)
class SyncResult:
    pushed: int
    pulled: int
    conflicts: int
    cursor: int


class SyncHttpClient:
    """只使用 Python 标准库的 HTTPS/HTTP 同步传输。"""

    def __init__(
        self,
        base_url: str,
        account_id: str,
        token: str,
        *,
        timeout: float = 20,
    ) -> None:
        self.base_url = normalize_sync_url(base_url)
        self._credential = bearer_credential(account_id, token)
        self.timeout = max(1.0, float(timeout))

    @staticmethod
    def create_account(
        base_url: str,
        display_name: str = "",
        *,
        registration_secret: str = "",
        timeout: float = 20,
    ) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if registration_secret.strip():
            headers["X-Registration-Secret"] = registration_secret.strip()
        return SyncHttpClient._request_json_static(
            normalize_sync_url(base_url) + "/v1/accounts",
            method="POST",
            payload={"display_name": display_name},
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    def register_account(
        base_url: str,
        username: str,
        password: str,
        *,
        display_name: str = "",
        device_name: str = "",
        registration_secret: str = "",
        timeout: float = 20,
    ) -> dict:
        headers = {"Content-Type": "application/json", "Accept": "application/json"}
        if registration_secret.strip():
            headers["X-Registration-Secret"] = registration_secret.strip()
        return SyncHttpClient._request_json_static(
            normalize_sync_url(base_url) + "/v1/auth/register",
            method="POST",
            payload={
                "username": username,
                "password": password,
                "display_name": display_name,
                "device_name": device_name,
            },
            headers=headers,
            timeout=timeout,
        )

    @staticmethod
    def login_account(
        base_url: str,
        username: str,
        password: str,
        *,
        device_name: str = "",
        timeout: float = 20,
    ) -> dict:
        return SyncHttpClient._request_json_static(
            normalize_sync_url(base_url) + "/v1/auth/login",
            method="POST",
            payload={
                "username": username,
                "password": password,
                "device_name": device_name,
            },
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            timeout=timeout,
        )

    def health(self) -> dict:
        return self._request_json("/v1/health", authenticated=False)

    def profile(self) -> dict:
        return self._request_json("/v1/auth/me")

    def upgrade_account(
        self,
        username: str,
        password: str,
        *,
        display_name: str = "",
        device_name: str = "",
    ) -> dict:
        return self._request_json(
            "/v1/auth/upgrade",
            method="POST",
            payload={
                "username": username,
                "password": password,
                "display_name": display_name,
                "device_name": device_name,
            },
        )

    def logout(self) -> dict:
        return self._request_json("/v1/auth/logout", method="POST", payload={})

    def push(self, device_id: str, device_name: str, events: list[dict]) -> dict:
        return self._request_json(
            "/v1/sync/push",
            method="POST",
            payload={
                "device_id": device_id,
                "device_name": device_name,
                "events": events,
            },
        )

    def pull(self, cursor: int, limit: int = MAX_SYNC_EVENTS) -> dict:
        query = urlencode({"cursor": max(0, int(cursor)), "limit": limit})
        return self._request_json(f"/v1/sync/pull?{query}")

    def upload_media(self, digest: str, content: bytes) -> None:
        self._request_bytes(
            f"/v1/media/{quote(digest, safe='')}",
            method="PUT",
            content=content,
        )

    def download_media(self, digest: str) -> bytes:
        return self._request_bytes(f"/v1/media/{quote(digest, safe='')}")

    def claim_lease(
        self,
        device_id: str,
        scope: str,
        lease_key: str,
        ttl_seconds: int = 120,
    ) -> bool:
        result = self._request_json(
            f"/v1/leases/{quote(scope, safe='')}/{quote(lease_key, safe='')}",
            method="POST",
            payload={"device_id": device_id, "ttl_seconds": ttl_seconds},
        )
        return bool(result.get("acquired"))

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._credential}",
            "Accept": "application/json",
        }

    def _request_json(
        self,
        path: str,
        *,
        method: str = "GET",
        payload: dict | None = None,
        authenticated: bool = True,
    ) -> dict:
        headers = self._headers() if authenticated else {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        return self._request_json_static(
            self.base_url + path,
            method=method,
            payload=payload,
            headers=headers,
            timeout=self.timeout,
        )

    @staticmethod
    def _request_json_static(
        url: str,
        *,
        method: str,
        payload: dict | None,
        headers: dict[str, str],
        timeout: float,
    ) -> dict:
        data = (
            json.dumps(payload, ensure_ascii=False).encode("utf-8")
            if payload is not None
            else None
        )
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with urlopen(request, timeout=timeout) as response:
                body = response.read(4 * 1024 * 1024)
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            if exc.code in {401, 403}:
                raise SyncAuthenticationError(detail or "同步凭据无效。") from exc
            raise SyncError(f"同步服务 HTTP {exc.code}：{detail}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise SyncError(f"无法连接同步服务：{getattr(exc, 'reason', exc)}") from exc
        try:
            result = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SyncError("同步服务返回了无效 JSON。") from exc
        if not isinstance(result, dict):
            raise SyncError("同步服务返回格式无效。")
        return result

    def _request_bytes(
        self,
        path: str,
        *,
        method: str = "GET",
        content: bytes | None = None,
    ) -> bytes:
        headers = self._headers()
        headers["Accept"] = "application/octet-stream"
        if content is not None:
            headers["Content-Type"] = "application/octet-stream"
        request = Request(
            self.base_url + path,
            data=content,
            headers=headers,
            method=method,
        )
        try:
            with urlopen(request, timeout=max(self.timeout, 60)) as response:
                body = response.read(MAX_SYNC_MEDIA_BYTES + 1)
        except HTTPError as exc:
            detail = _http_error_detail(exc)
            if exc.code in {401, 403}:
                raise SyncAuthenticationError(detail or "同步凭据无效。") from exc
            raise SyncError(f"媒体同步 HTTP {exc.code}：{detail}") from exc
        except (TimeoutError, URLError, OSError) as exc:
            raise SyncError(f"媒体同步失败：{getattr(exc, 'reason', exc)}") from exc
        if len(body) > MAX_SYNC_MEDIA_BYTES:
            raise SyncError("同步媒体超过大小限制。")
        return body


def _http_error_detail(exc: HTTPError) -> str:
    try:
        body = exc.read(16_384).decode("utf-8", errors="replace")
        payload = json.loads(body)
        if isinstance(payload, dict):
            return str(payload.get("detail", body))[:500]
        return body[:500]
    except (OSError, ValueError):
        return str(exc.reason)[:500]


class SyncRepository:
    """把现有 conversations/turns/characters 映射为同步实体。"""

    _TABLES = {
        "character": "characters",
        "conversation": "conversations",
        "turn": "turns",
    }
    _UPSERT_ORDER = {"character": 0, "conversation": 1, "turn": 2}
    _DELETE_ORDER = {"turn": 3, "conversation": 4, "character": 5}
    _MEDIA_FIELDS = {
        "character": ("avatar_path",),
        "conversation": ("avatar_override_path",),
        "turn": ("user_image_path", "assistant_image_path"),
    }

    def __init__(self, database: Database) -> None:
        self.database = database
        self._db = database.connection

    def enable_capture(self) -> None:
        row = self._db.execute(
            "SELECT capture_enabled FROM sync_runtime WHERE id = 1"
        ).fetchone()
        if row and row["capture_enabled"]:
            return
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE sync_runtime SET suppress_outbox = 1 WHERE id = 1"
            )
            connection.execute("DELETE FROM sync_outbox")
            for entity_type in ("character", "conversation", "turn"):
                table = self._TABLES[entity_type]
                rows = connection.execute(f"SELECT id FROM {table}").fetchall()
                connection.executemany(
                    "INSERT INTO sync_outbox VALUES (?, ?, ?, 'upsert', ?)",
                    [
                        (uuid4().hex, entity_type, row["id"], utc_now())
                        for row in rows
                    ],
                )
            connection.execute(
                """UPDATE sync_runtime SET capture_enabled = 1,
                   suppress_outbox = 0 WHERE id = 1"""
            )

    def reset_link(self) -> None:
        with self.database.transaction(immediate=True) as connection:
            connection.execute("DELETE FROM sync_outbox")
            connection.execute("DELETE FROM sync_entities")
            connection.execute("DELETE FROM sync_conflicts")
            connection.execute("DELETE FROM sync_state")
            connection.execute(
                """UPDATE sync_runtime SET capture_enabled = 0,
                   suppress_outbox = 0 WHERE id = 1"""
            )

    def pending_events(self, limit: int = MAX_SYNC_EVENTS) -> list[dict]:
        rows = self._db.execute(
            "SELECT rowid AS queue_rowid, * FROM sync_outbox ORDER BY rowid"
        ).fetchall()
        latest: dict[tuple[str, str], sqlite3.Row] = {}
        for row in rows:
            latest[(row["entity_type"], row["entity_id"])] = row
        ordered = sorted(
            latest.values(),
            key=lambda row: (
                (
                    self._DELETE_ORDER
                    if row["operation"] == "delete"
                    else self._UPSERT_ORDER
                )[row["entity_type"]],
                row["queue_rowid"],
            ),
        )[: max(1, min(limit, MAX_SYNC_EVENTS))]
        events: list[dict] = []
        encoded_size = 2
        for row in ordered:
            event = self._serialize_outbox_row(row)
            event_size = len(
                json.dumps(
                    _public_event(event),
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            if event_size + 2 > MAX_SYNC_PAYLOAD_BYTES:
                raise SyncError(
                    f"单条 {event['entity_type']} 同步内容超过大小限制。"
                )
            if events and encoded_size + event_size + 1 > MAX_SYNC_PAYLOAD_BYTES:
                break
            events.append(event)
            encoded_size += event_size + (1 if len(events) > 1 else 0)
        return events

    def _serialize_outbox_row(self, row: sqlite3.Row) -> dict:
        entity_type = row["entity_type"]
        entity_id = row["entity_id"]
        operation = row["operation"]
        payload: dict = {}
        media: dict = {}
        media_paths: dict[str, str] = {}
        if operation == "upsert":
            entity = self._db.execute(
                f"SELECT * FROM {self._TABLES[entity_type]} WHERE id = ?",
                (entity_id,),
            ).fetchone()
            if entity is None:
                operation = "delete"
            else:
                payload = dict(entity)
                for field in self._MEDIA_FIELDS[entity_type]:
                    path = str(payload.get(field, "") or "")
                    payload[field] = ""
                    descriptor = self._media_descriptor(path)
                    if descriptor is not None:
                        media[field] = descriptor
                        media_paths[field] = path
                if entity_type == "turn":
                    self._strip_segment_paths(payload)
        revision = self._db.execute(
            """SELECT server_revision FROM sync_entities
               WHERE entity_type = ? AND entity_id = ?""",
            (entity_type, entity_id),
        ).fetchone()
        return {
            "event_id": row["event_id"],
            "entity_type": entity_type,
            "entity_id": entity_id,
            "operation": operation,
            "base_revision": int(revision["server_revision"]) if revision else 0,
            "updated_at": str(payload.get("updated_at", row["created_at"])),
            "payload": payload,
            "media": media,
            "_media_paths": media_paths,
            "_queue_rowid": int(row["queue_rowid"]),
        }

    @staticmethod
    def _media_descriptor(path: str) -> dict | None:
        if not path:
            return None
        source = Path(path)
        try:
            size = source.stat().st_size
            if size <= 0 or size > MAX_SYNC_MEDIA_BYTES:
                return None
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
        except OSError:
            return None
        return {
            "sha256": digest,
            "bytes": size,
            "filename": source.name[:180],
            "mime": mimetypes.guess_type(source.name)[0]
            or "application/octet-stream",
        }

    @staticmethod
    def _strip_segment_paths(payload: dict) -> None:
        try:
            segments = json.loads(payload.get("assistant_segments_json", "[]"))
        except (TypeError, json.JSONDecodeError):
            return
        if not isinstance(segments, list):
            return
        changed = False
        for segment in segments:
            if isinstance(segment, dict) and segment.get("image_path"):
                segment["image_path"] = ""
                changed = True
        if changed:
            payload["assistant_segments_json"] = json.dumps(
                segments, ensure_ascii=False, separators=(",", ":")
            )

    def accept_push_results(
        self, pending: list[dict], results: list[dict]
    ) -> list[dict]:
        by_event = {event["event_id"]: event for event in pending}
        canonical: list[dict] = []
        with self.database.transaction(immediate=True) as connection:
            for result in results:
                event = by_event.get(str(result.get("event_id", "")))
                if event is None:
                    continue
                status = result.get("status")
                if status not in {"accepted", "conflict"}:
                    continue
                connection.execute(
                    """DELETE FROM sync_outbox WHERE entity_type = ?
                       AND entity_id = ? AND rowid <= ?""",
                    (
                        event["entity_type"],
                        event["entity_id"],
                        event["_queue_rowid"],
                    ),
                )
                if status == "accepted":
                    connection.execute(
                        """INSERT INTO sync_entities VALUES (?, ?, ?)
                           ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                               server_revision = excluded.server_revision""",
                        (
                            event["entity_type"],
                            event["entity_id"],
                            int(result.get("revision", 0)),
                        ),
                    )
                else:
                    current = result.get("current")
                    connection.execute(
                        """INSERT INTO sync_conflicts(
                               entity_type, entity_id, local_event_json,
                               server_event_json, created_at
                           ) VALUES (?, ?, ?, ?, ?)""",
                        (
                            event["entity_type"],
                            event["entity_id"],
                            json.dumps(_public_event(event), ensure_ascii=False),
                            json.dumps(current or {}, ensure_ascii=False),
                            utc_now(),
                        ),
                    )
                    if isinstance(current, dict):
                        canonical.append(current)
        return canonical

    def apply_remote_events(
        self,
        events: list[dict],
        *,
        local_device_id: str,
        skip_own: bool = True,
    ) -> int:
        applied = 0
        with self.database.transaction(immediate=True) as connection:
            connection.execute(
                "UPDATE sync_runtime SET suppress_outbox = 1 WHERE id = 1"
            )
            try:
                for event in events:
                    entity_type = str(event.get("entity_type", ""))
                    entity_id = str(event.get("entity_id", ""))
                    revision = int(event.get("revision", 0))
                    if entity_type not in self._TABLES or not entity_id or revision <= 0:
                        raise SyncError("远端同步事件格式无效。")
                    known = connection.execute(
                        """SELECT server_revision FROM sync_entities
                           WHERE entity_type = ? AND entity_id = ?""",
                        (entity_type, entity_id),
                    ).fetchone()
                    if known and int(known["server_revision"]) >= revision:
                        continue
                    own = event.get("source_device_id") == local_device_id
                    if not (skip_own and own):
                        self._apply_event(connection, event)
                        applied += 1
                    connection.execute(
                        """INSERT INTO sync_entities VALUES (?, ?, ?)
                           ON CONFLICT(entity_type, entity_id) DO UPDATE SET
                               server_revision = excluded.server_revision""",
                        (entity_type, entity_id, revision),
                    )
            finally:
                connection.execute(
                    "UPDATE sync_runtime SET suppress_outbox = 0 WHERE id = 1"
                )
        return applied

    @staticmethod
    def _apply_event(connection: sqlite3.Connection, event: dict) -> None:
        entity_type = event["entity_type"]
        entity_id = event["entity_id"]
        if event.get("operation") == "delete":
            connection.execute(
                f"DELETE FROM {SyncRepository._TABLES[entity_type]} WHERE id = ?",
                (entity_id,),
            )
            return
        payload = event.get("payload")
        if not isinstance(payload, dict) or payload.get("id") != entity_id:
            raise SyncError("远端实体内容无效。")
        if entity_type == "character":
            _upsert_character(connection, payload)
        elif entity_type == "conversation":
            _upsert_conversation(connection, payload)
        else:
            _upsert_turn(connection, payload)

    def cursor(self) -> int:
        row = self._db.execute(
            "SELECT value FROM sync_state WHERE key = 'cursor'"
        ).fetchone()
        try:
            return max(0, int(row["value"])) if row else 0
        except (TypeError, ValueError):
            return 0

    def set_cursor(self, cursor: int) -> None:
        with self._db:
            self._db.execute(
                """INSERT INTO sync_state VALUES ('cursor', ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (str(max(0, int(cursor))),),
            )

    def conflict_count(self) -> int:
        return int(
            self._db.execute(
                "SELECT COUNT(*) FROM sync_conflicts WHERE resolved = 0"
            ).fetchone()[0]
        )


class SyncEngine:
    def __init__(
        self,
        repository: SyncRepository,
        transport: SyncHttpClient,
        *,
        device_id: str,
        device_name: str,
        media_root: str | Path,
    ) -> None:
        self.repository = repository
        self.transport = transport
        self.device_id = device_id
        self.device_name = device_name
        self.media_root = Path(media_root)

    def sync_once(self) -> SyncResult:
        self.repository.enable_capture()
        pushed = 0
        pending = self.repository.pending_events()
        if pending:
            for event in pending:
                self._upload_event_media(event)
            public_events = [_public_event(event) for event in pending]
            response = self.transport.push(
                self.device_id, self.device_name, public_events
            )
            results = response.get("results", [])
            if not isinstance(results, list):
                raise SyncError("同步服务 push 响应无效。")
            canonical = self.repository.accept_push_results(pending, results)
            if canonical:
                canonical = [self._materialize_media(event) for event in canonical]
                self.repository.apply_remote_events(
                    canonical, local_device_id=self.device_id, skip_own=False
                )
            pushed = sum(
                1 for result in results if result.get("status") == "accepted"
            )

        pulled = 0
        cursor = self.repository.cursor()
        while True:
            response = self.transport.pull(cursor)
            events = response.get("events", [])
            if not isinstance(events, list):
                raise SyncError("同步服务 pull 响应无效。")
            materialized = [self._materialize_media(event) for event in events]
            pulled += self.repository.apply_remote_events(
                materialized, local_device_id=self.device_id
            )
            next_cursor = int(response.get("cursor", cursor))
            if next_cursor < cursor:
                raise SyncError("同步服务游标发生倒退。")
            cursor = next_cursor
            self.repository.set_cursor(cursor)
            if not response.get("has_more"):
                break
        return SyncResult(
            pushed=pushed,
            pulled=pulled,
            conflicts=self.repository.conflict_count(),
            cursor=cursor,
        )

    def _upload_event_media(self, event: dict) -> None:
        for field, path in event.get("_media_paths", {}).items():
            descriptor = event.get("media", {}).get(field, {})
            try:
                content = Path(path).read_bytes()
            except OSError as exc:
                raise SyncError(f"无法读取待同步媒体：{path}") from exc
            digest = hashlib.sha256(content).hexdigest()
            if digest != descriptor.get("sha256"):
                raise SyncError("待同步媒体在上传前发生变化。")
            self.transport.upload_media(digest, content)

    def _materialize_media(self, event: dict) -> dict:
        payload = dict(event.get("payload", {}))
        media = event.get("media", {})
        if not isinstance(media, dict):
            raise SyncError("远端媒体索引无效。")
        installed: dict[str, str] = {}
        for field, descriptor in media.items():
            if not isinstance(descriptor, dict):
                raise SyncError("远端媒体描述无效。")
            digest = str(descriptor.get("sha256", "")).lower()
            content = self.transport.download_media(digest)
            if hashlib.sha256(content).hexdigest() != digest:
                raise SyncError("下载媒体的 SHA-256 校验失败。")
            expected = int(descriptor.get("bytes", len(content)))
            if len(content) != expected:
                raise SyncError("下载媒体大小与索引不一致。")
            installed[field] = self._install_media(
                digest, str(descriptor.get("filename", "media.bin")), content
            )
            payload[field] = installed[field]
        if event.get("entity_type") == "turn" and installed.get(
            "assistant_image_path"
        ):
            _restore_segment_image_path(
                payload, installed["assistant_image_path"]
            )
        result = dict(event)
        result["payload"] = payload
        return result

    def _install_media(self, digest: str, filename: str, content: bytes) -> str:
        suffix = Path(filename).suffix.lower()
        if not suffix or len(suffix) > 10 or not suffix[1:].isalnum():
            suffix = ".bin"
        target = self.media_root / "media" / "synced" / f"{digest}{suffix}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() == digest:
            return str(target)
        partial = target.with_suffix(target.suffix + ".part")
        partial.write_bytes(content)
        partial.replace(target)
        return str(target)


def _public_event(event: dict) -> dict:
    return {key: value for key, value in event.items() if not key.startswith("_")}


def _restore_segment_image_path(payload: dict, path: str) -> None:
    try:
        segments = json.loads(payload.get("assistant_segments_json", "[]"))
    except (TypeError, json.JSONDecodeError):
        return
    if not isinstance(segments, list):
        return
    changed = False
    for segment in segments:
        if isinstance(segment, dict) and segment.get("kind") == "image":
            segment["image_path"] = path
            changed = True
    if changed:
        payload["assistant_segments_json"] = json.dumps(
            segments, ensure_ascii=False, separators=(",", ":")
        )


def _upsert_character(connection: sqlite3.Connection, payload: dict) -> None:
    columns = ("id", "name", "avatar_path", "card_json", "created_at", "updated_at")
    _upsert(connection, "characters", columns, payload)


def _upsert_conversation(connection: sqlite3.Connection, payload: dict) -> None:
    columns = (
        "id",
        "title",
        "model",
        "created_at",
        "updated_at",
        "last_preview",
        "character_id",
        "avatar_override_path",
        "opening_message",
        "ai_summary",
        "summary_status",
        "role_state_json",
    )
    _upsert(connection, "conversations", columns, payload)


def _upsert_turn(connection: sqlite3.Connection, payload: dict) -> None:
    columns = (
        "id",
        "conversation_id",
        "user_content",
        "assistant_content",
        "reasoning_content",
        "model",
        "status",
        "error_code",
        "created_at",
        "updated_at",
        "origin",
        "user_image_path",
        "user_image_description",
        "assistant_image_path",
        "user_sticker",
        "assistant_segments_json",
    )
    _upsert(connection, "turns", columns, payload)


def _upsert(
    connection: sqlite3.Connection,
    table: str,
    columns: tuple[str, ...],
    payload: dict,
) -> None:
    missing = [column for column in columns if column not in payload]
    if missing:
        raise SyncError(f"远端 {table} 缺少字段：{', '.join(missing)}")
    assignments = ", ".join(
        f"{column} = excluded.{column}" for column in columns if column != "id"
    )
    placeholders = ", ".join("?" for _ in columns)
    connection.execute(
        f"""INSERT INTO {table}({', '.join(columns)}) VALUES ({placeholders})
            ON CONFLICT(id) DO UPDATE SET {assignments}""",
        tuple(payload[column] for column in columns),
    )
