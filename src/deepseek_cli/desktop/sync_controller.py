"""Qt 后台同步控制器：定时推拉、账户创建和主动消息租约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QSysInfo, QThread, QTimer, Signal, Slot

from ..sync_protocol import (
    DEFAULT_SYNC_URL,
    bearer_credential,
    normalize_sync_url,
)
from .data.database import Database
from .data.repositories import SettingsRepository
from .security.credentials import CredentialStore
from .sync_client import (
    SyncEngine,
    SyncHttpClient,
    SyncRepository,
    SyncResult,
)


@dataclass(frozen=True, slots=True)
class SyncConfig:
    server_url: str
    account_id: str
    token: str
    device_id: str
    device_name: str


class SyncCycleWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        config: SyncConfig,
        database_path: str | Path,
        media_root: str | Path,
    ) -> None:
        super().__init__()
        self._config = config
        self._database_path = Path(database_path)
        self._media_root = Path(media_root)

    @Slot()
    def run(self) -> None:
        database = None
        try:
            database = Database(self._database_path)
            repository = SyncRepository(database)
            transport = SyncHttpClient(
                self._config.server_url,
                self._config.account_id,
                self._config.token,
            )
            result = SyncEngine(
                repository,
                transport,
                device_id=self._config.device_id,
                device_name=self._config.device_name,
                media_root=self._media_root,
            ).sync_once()
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc)[:800])
        finally:
            if database is not None:
                database.close()
            self.finished.emit()


class SyncAccountWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self, server_url: str, display_name: str, registration_secret: str = ""
    ) -> None:
        super().__init__()
        self._server_url = server_url
        self._display_name = display_name
        self._registration_secret = registration_secret

    @Slot()
    def run(self) -> None:
        try:
            result = SyncHttpClient.create_account(
                self._server_url,
                self._display_name,
                registration_secret=self._registration_secret,
            )
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc)[:800])
        finally:
            self.finished.emit()


class SyncLeaseWorker(QObject):
    completed = Signal(str, bool)
    failed = Signal(str, str)
    finished = Signal(str)

    def __init__(self, config: SyncConfig, conversation_id: str) -> None:
        super().__init__()
        self._config = config
        self._conversation_id = conversation_id

    @Slot()
    def run(self) -> None:
        try:
            acquired = SyncHttpClient(
                self._config.server_url,
                self._config.account_id,
                self._config.token,
                timeout=8,
            ).claim_lease(
                self._config.device_id,
                "proactive",
                self._conversation_id,
                180,
            )
            self.completed.emit(self._conversation_id, acquired)
        except Exception as exc:
            self.failed.emit(self._conversation_id, str(exc)[:500])
        finally:
            self.finished.emit(self._conversation_id)


class SyncController(QObject):
    status_changed = Signal(str)
    data_changed = Signal()
    account_created = Signal(str)
    proactive_claimed = Signal(str, bool)

    def __init__(
        self,
        settings: SettingsRepository,
        credentials: CredentialStore,
        database_path: str | Path,
        media_root: str | Path,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._credentials = credentials
        self._database_path = Path(database_path)
        self._media_root = Path(media_root)
        self._timer = QTimer(self)
        self._timer.setInterval(15_000)
        self._timer.timeout.connect(self.sync_now)
        self._debounce_timer = QTimer(self)
        self._debounce_timer.setSingleShot(True)
        self._debounce_timer.setInterval(1_200)
        self._debounce_timer.timeout.connect(self.sync_now)
        self._sync_thread: QThread | None = None
        self._sync_worker: SyncCycleWorker | None = None
        self._sync_pending = False
        self._account_thread: QThread | None = None
        self._account_worker: SyncAccountWorker | None = None
        self._lease_tasks: dict[str, tuple[QThread, SyncLeaseWorker]] = {}
        self._stopping = False
        if not self._settings.get("sync_device_id"):
            self._settings.set("sync_device_id", uuid4().hex)
        if not self._settings.get("sync_device_name"):
            self._settings.set(
                "sync_device_name", QSysInfo.prettyProductName()[:120] or "BanVerse 设备"
            )

    @property
    def enabled(self) -> bool:
        return self._settings.get_bool("sync_enabled", False) and self._config() is not None

    def start(self) -> None:
        self.reload()
        if self.enabled:
            QTimer.singleShot(800, self.sync_now)

    @Slot()
    def reload(self) -> None:
        if self.enabled and not self._stopping:
            self._timer.start()
            self.status_changed.emit("双端同步已启用，等待下一次同步。")
        else:
            self._timer.stop()
            self._debounce_timer.stop()
            self._sync_pending = False
            if not self._settings.get("sync_account_id"):
                self.status_changed.emit("尚未连接同步账户。")
            else:
                self.status_changed.emit("双端同步已暂停。")

    @Slot()
    def sync_now(self) -> None:
        if self._stopping:
            return
        if self._sync_thread is not None:
            self._sync_pending = True
            return
        config = self._config()
        if not self._settings.get_bool("sync_enabled", False) or config is None:
            self.reload()
            return
        self.status_changed.emit("正在同步消息、角色和图片……")
        worker = SyncCycleWorker(config, self._database_path, self._media_root)
        thread = QThread(self)
        self._sync_worker, self._sync_thread = worker, thread
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._sync_completed)
        worker.failed.connect(self._sync_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._sync_finished)
        thread.start()

    @Slot()
    def schedule_sync(self) -> None:
        """本地数据变化后合并短时间内的写入，并尽快开始后台同步。"""

        if self._stopping or not self.enabled:
            return
        self._debounce_timer.start()

    @Slot(str, str, str)
    def create_account(
        self, server_url: str, display_name: str = "", registration_secret: str = ""
    ) -> None:
        if self._stopping or self._account_thread is not None:
            return
        try:
            normalized = normalize_sync_url(server_url)
        except ValueError as exc:
            self.status_changed.emit(str(exc))
            return
        self.status_changed.emit("正在创建同步账户……")
        worker = SyncAccountWorker(normalized, display_name, registration_secret)
        thread = QThread(self)
        self._account_worker, self._account_thread = worker, thread
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._account_completed)
        worker.failed.connect(self._account_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._account_finished)
        thread.start()

    @Slot()
    def disconnect_account(self) -> None:
        if self._sync_thread is not None or self._account_thread is not None:
            self.status_changed.emit("请等待当前同步任务结束后再断开账户。")
            return
        database = Database(self._database_path)
        try:
            SyncRepository(database).reset_link()
        finally:
            database.close()
        self._credentials.clear_sync_token()
        self._settings.set("sync_enabled", "false")
        self._settings.set("sync_account_id", "")
        self._timer.stop()
        self._debounce_timer.stop()
        self._sync_pending = False
        self.status_changed.emit("已断开同步账户；本机聊天数据保持不变。")

    def claim_proactive(self, conversation_id: str) -> None:
        config = self._config()
        if not self.enabled or config is None:
            self.proactive_claimed.emit(conversation_id, True)
            return
        if conversation_id in self._lease_tasks:
            self.proactive_claimed.emit(conversation_id, False)
            return
        worker = SyncLeaseWorker(config, conversation_id)
        thread = QThread(self)
        self._lease_tasks[conversation_id] = (thread, worker)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self.proactive_claimed)
        worker.failed.connect(self._lease_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(
            lambda conversation_id=conversation_id: self._lease_tasks.pop(
                conversation_id, None
            )
        )
        thread.start()

    def _config(self) -> SyncConfig | None:
        account_id = self._settings.get("sync_account_id").strip()
        token = self._credentials.get_sync_token().strip()
        if not account_id or not token:
            return None
        try:
            server_url = normalize_sync_url(
                self._settings.get("sync_server_url", DEFAULT_SYNC_URL)
            )
            bearer_credential(account_id, token)
        except ValueError:
            return None
        return SyncConfig(
            server_url=server_url,
            account_id=account_id,
            token=token,
            device_id=self._settings.get("sync_device_id"),
            device_name=self._settings.get("sync_device_name", "BanVerse 设备"),
        )

    @Slot(object)
    def _sync_completed(self, result: SyncResult) -> None:
        self._settings.set(
            "sync_last_success_at",
            datetime.now().astimezone().isoformat(timespec="seconds"),
        )
        text = f"同步完成：上传 {result.pushed}，接收 {result.pulled}。"
        if result.conflicts:
            text += f" 有 {result.conflicts} 项并发冲突已保留。"
        self.status_changed.emit(text)
        if result.pulled:
            self.data_changed.emit()

    @Slot(str)
    def _sync_failed(self, error: str) -> None:
        self.status_changed.emit(f"同步失败：{error}")

    @Slot()
    def _sync_finished(self) -> None:
        self._sync_worker = None
        self._sync_thread = None
        if self._sync_pending and not self._stopping:
            self._sync_pending = False
            QTimer.singleShot(0, self.sync_now)

    @Slot(object)
    def _account_completed(self, result: dict) -> None:
        account_id = str(result.get("account_id", "")).strip()
        token = str(result.get("token", "")).strip()
        if not account_id or not token:
            self.status_changed.emit("同步服务未返回有效账户凭据。")
            return
        warning = ""
        try:
            self._credentials.save_sync_token(token)
        except RuntimeError as exc:
            warning = str(exc)
        self._settings.set("sync_account_id", account_id)
        self._settings.set("sync_enabled", "true")
        self.account_created.emit(account_id)
        self.status_changed.emit(
            warning or "同步账户已创建；令牌已保存，请妥善备份后连接另一端。"
        )
        self.reload()
        QTimer.singleShot(0, self.sync_now)

    @Slot(str)
    def _account_failed(self, error: str) -> None:
        self.status_changed.emit(f"创建同步账户失败：{error}")

    @Slot()
    def _account_finished(self) -> None:
        self._account_worker = None
        self._account_thread = None

    @Slot(str, str)
    def _lease_failed(self, conversation_id: str, error: str) -> None:
        self.status_changed.emit(f"主动消息租约失败：{error}")
        self.proactive_claimed.emit(conversation_id, False)

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        self._timer.stop()
        self._debounce_timer.stop()
        self._sync_pending = False
        threads = [self._sync_thread, self._account_thread]
        threads.extend(thread for thread, _worker in self._lease_tasks.values())
        for thread in threads:
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(5_000)
