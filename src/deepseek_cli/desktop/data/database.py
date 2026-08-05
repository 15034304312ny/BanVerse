"""SQLite 连接与版本化结构迁移。"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 7


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(self.path)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.connection.execute("PRAGMA journal_mode = WAL")
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._migrate()

    def _migrate(self) -> None:
        version = self.connection.execute("PRAGMA user_version").fetchone()[0]
        if version < 1:
            self._migrate_v1()
        if version < 2:
            self._migrate_v2()
        if version < 3:
            self._migrate_v3()
        if version < 4:
            self._migrate_v4()
        if version < 5:
            self._migrate_v5()
        if version < 6:
            self._migrate_v6()
        if version < 7:
            self._migrate_v7()

    def _migrate_v1(self) -> None:
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    title TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_preview TEXT NOT NULL DEFAULT ''
                );
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    user_content TEXT NOT NULL,
                    assistant_content TEXT NOT NULL DEFAULT '',
                    reasoning_content TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL,
                    status TEXT NOT NULL,
                    error_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id)
                        ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_turns_conversation_created
                    ON turns(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                PRAGMA user_version = 1;
                """
            )

    def _migrate_v2(self) -> None:
        columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(conversations)")
        }
        with self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS characters (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    avatar_path TEXT NOT NULL DEFAULT '',
                    card_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )
            if "character_id" not in columns:
                self.connection.execute(
                    """ALTER TABLE conversations ADD COLUMN character_id TEXT
                       REFERENCES characters(id) ON DELETE SET NULL"""
                )
            if "avatar_override_path" not in columns:
                self.connection.execute(
                    """ALTER TABLE conversations ADD COLUMN
                       avatar_override_path TEXT NOT NULL DEFAULT ''"""
                )
            if "opening_message" not in columns:
                self.connection.execute(
                    """ALTER TABLE conversations ADD COLUMN
                       opening_message TEXT NOT NULL DEFAULT ''"""
                )
            self.connection.execute("PRAGMA user_version = 2")

    def _migrate_v3(self) -> None:
        conversation_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(conversations)")
        }
        turn_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(turns)")
        }
        with self.connection:
            if "ai_summary" not in conversation_columns:
                self.connection.execute(
                    """ALTER TABLE conversations ADD COLUMN
                       ai_summary TEXT NOT NULL DEFAULT ''"""
                )
            if "summary_status" not in conversation_columns:
                self.connection.execute(
                    """ALTER TABLE conversations ADD COLUMN
                       summary_status TEXT NOT NULL DEFAULT 'none'"""
                )
            if "origin" not in turn_columns:
                self.connection.execute(
                    """ALTER TABLE turns ADD COLUMN
                       origin TEXT NOT NULL DEFAULT 'user'"""
                )
            self.connection.execute("PRAGMA user_version = 3")

    def _migrate_v4(self) -> None:
        turn_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(turns)")
        }
        with self.connection:
            for name in (
                "user_image_path",
                "user_image_description",
                "assistant_image_path",
            ):
                if name not in turn_columns:
                    self.connection.execute(
                        f"""ALTER TABLE turns ADD COLUMN {name}
                            TEXT NOT NULL DEFAULT ''"""
                    )
            self.connection.execute("PRAGMA user_version = 4")

    def _migrate_v5(self) -> None:
        turn_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(turns)")
        }
        with self.connection:
            if "user_sticker" not in turn_columns:
                self.connection.execute(
                    """ALTER TABLE turns ADD COLUMN
                       user_sticker TEXT NOT NULL DEFAULT ''"""
                )
            self.connection.execute("PRAGMA user_version = 5")

    def _migrate_v6(self) -> None:
        conversation_columns = {
            row["name"]
            for row in self.connection.execute(
                "PRAGMA table_info(conversations)"
            )
        }
        with self.connection:
            if "role_state_json" not in conversation_columns:
                self.connection.execute(
                    """ALTER TABLE conversations ADD COLUMN
                       role_state_json TEXT NOT NULL DEFAULT '{}'"""
                )
            self.connection.execute("PRAGMA user_version = 6")

    def _migrate_v7(self) -> None:
        turn_columns = {
            row["name"]
            for row in self.connection.execute("PRAGMA table_info(turns)")
        }
        with self.connection:
            if "assistant_segments_json" not in turn_columns:
                self.connection.execute(
                    """ALTER TABLE turns ADD COLUMN
                       assistant_segments_json TEXT NOT NULL DEFAULT '[]'"""
                )
            self.connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")

    @contextmanager
    def transaction(self, *, immediate: bool = False) -> Iterator[sqlite3.Connection]:
        """提供显式事务，供需要原子写入多个仓储的流程使用。"""

        self.connection.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
        try:
            yield self.connection
        except BaseException:
            self.connection.rollback()
            raise
        else:
            self.connection.commit()

    def close(self) -> None:
        self.connection.close()
