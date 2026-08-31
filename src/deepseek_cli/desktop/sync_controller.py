"""Qt 后台同步控制器：定时推拉、账户创建和主动消息租约。"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from time import monotonic
from uuid import uuid4

from PySide6.QtCore import QObject, QSysInfo, QThread, QTimer, Signal, Slot

from ..diagnostics import DiagnosticRecorder
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


class SyncAuthWorker(QObject):
    completed = Signal(object)
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        action: str,
        server_url: str,
        *,
        username: str = "",
        password: str = "",
        display_name: str = "",
        registration_secret: str = "",
        device_name: str = "",
        config: SyncConfig | None = None,
    ) -> None:
        super().__init__()
        self._action = action
        self._server_url = server_url
        self._username = username
        self._password = password
        self._display_name = display_name
        self._registration_secret = registration_secret
        self._device_name = device_name
        self._config = config

    @Slot()
    def run(self) -> None:
        try:
            if self._action == "register":
                result = SyncHttpClient.register_account(
                    self._server_url,
                    self._username,
                    self._password,
                    display_name=self._display_name,
                    device_name=self._device_name,
                    registration_secret=self._registration_secret,
                )
            elif self._action == "login":
                result = SyncHttpClient.login_account(
                    self._server_url,
                    self._username,
                    self._password,
                    device_name=self._device_name,
                )
            elif self._action == "upgrade" and self._config is not None:
                result = SyncHttpClient(
                    self._config.server_url,
                    self._config.account_id,
                    self._config.token,
                ).upgrade_account(
                    self._username,
                    self._password,
                    display_name=self._display_name,
                    device_name=self._device_name,
                )
            elif self._action == "logout" and self._config is not None:
                result = SyncHttpClient(
                    self._config.server_url,
                    self._config.account_id,
                    self._config.token,
                ).logout()
            elif self._action == "legacy-create":
                result = SyncHttpClient.create_account(
                    self._server_url,
                    self._display_name,
                    registration_secret=self._registration_secret,
                )
            else:
                raise ValueError("不支持的同步账户操作。")
            result = dict(result)
            result["auth_action"] = self._action
            self.completed.emit(result)
        except Exception as exc:
            self.failed.emit(str(exc)[:800])
        finally:
            self.finished.emit()


class SyncLeaseWorker(QObject):
    completed = Signal(str, bool)
    failed = Signal(str, str)
    finished = Signal(str)

    def __init__(
        self,
        config: SyncConfig,
        conversation_id: str,
        lease_key: str,
        ttl_seconds: int,
    ) -> None:
        super().__init__()
        self._config = config
        self._conversation_id = conversation_id
        self._lease_key = lease_key
        self._ttl_seconds = ttl_seconds

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
                self._lease_key,
                self._ttl_seconds,
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
    account_authenticated = Signal(object)
    proactive_claimed = Signal(str, bool)

    def __init__(
        self,
        settings: SettingsRepository,
        credentials: CredentialStore,
        database_path: str | Path,
        media_root: str | Path,
        diagnostics: DiagnosticRecorder | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._credentials = credentials
        self._database_path = Path(database_path)
        self._media_root = Path(media_root)
        self._diagnostics = diagnostics
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
        self._sync_task_id = ""
        self._sync_started_at = 0.0
        self._link_reset_pending = False
        self._auth_thread: QThread | None = None
        self._auth_worker: SyncAuthWorker | None = None
        self._auth_action = ""
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
        self._sync_task_id = (
            self._diagnostics.new_task_id("sync")
            if self._diagnostics is not None
            else ""
        )
        self._sync_started_at = monotonic()
        self._record_sync_diagnostic("cycle_started", outcome="started")
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

    @Slot()
    def reset_link_state(self) -> None:
        """切换兼容账户时重置游标，并在下次同步重新生成本机快照。"""

        if self._sync_thread is not None:
            self._link_reset_pending = True
            self.status_changed.emit("当前同步结束后将切换账户并重建本机快照。")
            return
        self._reset_link_state_now()

    def _reset_link_state_now(self) -> None:
        database = Database(self._database_path)
        try:
            SyncRepository(database).reset_link()
        finally:
            database.close()
        self._link_reset_pending = False

    @Slot(str, str, str)
    def create_account(
        self, server_url: str, display_name: str = "", registration_secret: str = ""
    ) -> None:
        """兼容 1.3.0 的令牌式账户创建入口。"""

        try:
            normalized = normalize_sync_url(server_url)
        except ValueError as exc:
            self.status_changed.emit(str(exc))
            return
        self._start_auth(
            SyncAuthWorker(
                "legacy-create",
                normalized,
                display_name=display_name,
                registration_secret=registration_secret,
                device_name=self._device_name(),
            ),
            "正在创建兼容同步账户……",
        )

    @Slot(str, str, str, str, str)
    def register_account(
        self,
        server_url: str,
        username: str,
        password: str,
        display_name: str = "",
        registration_secret: str = "",
    ) -> None:
        try:
            normalized = normalize_sync_url(server_url)
        except ValueError as exc:
            self.status_changed.emit(str(exc))
            return
        self._start_auth(
            SyncAuthWorker(
                "register",
                normalized,
                username=username,
                password=password,
                display_name=display_name,
                registration_secret=registration_secret,
                device_name=self._device_name(),
            ),
            "正在注册并登录同步账户……",
        )

    @Slot(str, str, str)
    def login_account(self, server_url: str, username: str, password: str) -> None:
        try:
            normalized = normalize_sync_url(server_url)
        except ValueError as exc:
            self.status_changed.emit(str(exc))
            return
        self._start_auth(
            SyncAuthWorker(
                "login",
                normalized,
                username=username,
                password=password,
                device_name=self._device_name(),
            ),
            "正在登录同步账户……",
        )

    @Slot(str, str, str)
    def upgrade_account(
        self, username: str, password: str, display_name: str = ""
    ) -> None:
        config = self._config()
        if config is None:
            self.status_changed.emit("请先使用账户 ID 和同步令牌连接旧版账户。")
            return
        self._start_auth(
            SyncAuthWorker(
                "upgrade",
                config.server_url,
                username=username,
                password=password,
                display_name=display_name,
                device_name=self._device_name(),
                config=config,
            ),
            "正在为旧版账户设置用户名和密码……",
        )

    def _start_auth(self, worker: SyncAuthWorker, status: str) -> bool:
        if self._stopping or self._auth_thread is not None:
            self.status_changed.emit("请等待当前账户操作结束。")
            return False
        if self._sync_thread is not None:
            self.status_changed.emit("请等待当前同步完成后再进行登录或注册。")
            return False
        self.status_changed.emit(status)
        thread = QThread(self)
        self._auth_worker, self._auth_thread = worker, thread
        self._auth_action = worker._action
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(self._auth_completed)
        worker.failed.connect(self._auth_failed)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._auth_finished)
        thread.start()
        return True

    @Slot()
    def disconnect_account(self) -> None:
        if self._sync_thread is not None or self._auth_thread is not None:
            self.status_changed.emit("请等待当前同步或账户操作结束后再退出。")
            return
        config = self._config()
        password_account = bool(self._settings.get("sync_username", "").strip())
        if config is not None and password_account:
            self._start_auth(
                SyncAuthWorker(
                    "logout", config.server_url, config=config
                ),
                "正在退出同步账户……",
            )
        self._clear_local_account()
        if config is None or not password_account:
            self.status_changed.emit("本机已退出登录；聊天数据保持不变。")

    def _clear_local_account(self) -> None:
        database = Database(self._database_path)
        try:
            SyncRepository(database).reset_link()
        finally:
            database.close()
        self._credentials.clear_sync_token()
        self._settings.set("sync_enabled", "false")
        self._settings.set("sync_account_id", "")
        self._settings.set("sync_username", "")
        self._settings.set("sync_session_expires_at", "")
        self._timer.stop()
        self._debounce_timer.stop()
        self._sync_pending = False

    def _device_name(self) -> str:
        return self._settings.get("sync_device_name", "BanVerse 设备")

    def claim_proactive(
        self,
        conversation_id: str,
        *,
        event_id: str = "",
        ttl_seconds: int = 600,
    ) -> None:
        config = self._config()
        if not self.enabled or config is None:
            self.proactive_claimed.emit(conversation_id, True)
            return
        if conversation_id in self._lease_tasks:
            self.proactive_claimed.emit(conversation_id, False)
            return
        worker = SyncLeaseWorker(
            config,
            conversation_id,
            event_id or conversation_id,
            max(600, min(int(ttl_seconds), 172_800)),
        )
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
        self._record_sync_diagnostic(
            "cycle_completed",
            duration_ms=(monotonic() - self._sync_started_at) * 1000,
            details={
                "pushed": result.pushed,
                "pulled": result.pulled,
                "conflicts": result.conflicts,
            },
        )
        if result.pulled:
            self.data_changed.emit()

    @Slot(str)
    def _sync_failed(self, error: str) -> None:
        self.status_changed.emit(f"同步失败：{error}")
        self._record_sync_diagnostic(
            "cycle_completed",
            outcome="error",
            error_code="sync_service_error",
            duration_ms=(monotonic() - self._sync_started_at) * 1000,
        )

    @Slot()
    def _sync_finished(self) -> None:
        self._sync_worker = None
        self._sync_thread = None
        self._sync_task_id = ""
        self._sync_started_at = 0.0
        if self._link_reset_pending:
            self._reset_link_state_now()
        if self._sync_pending and not self._stopping:
            self._sync_pending = False
            QTimer.singleShot(0, self.sync_now)

    def _record_sync_diagnostic(
        self,
        stage: str,
        *,
        outcome: str = "ok",
        error_code: str = "",
        duration_ms: float | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        self._diagnostics.record(
            "sync",
            stage,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            request_kind="background",
            task_id=self._sync_task_id,
            details=details,
        )

    @Slot(object)
    def _auth_completed(self, result: dict) -> None:
        action = str(result.get("auth_action", self._auth_action))
        if action == "logout":
            suffix = "服务器会话已撤销。" if result.get("revoked") else "本机凭据已清除。"
            self.status_changed.emit(f"已退出登录；本机聊天数据保持不变，{suffix}")
            self.account_authenticated.emit({"account_id": "", "username": ""})
            return
        account_id = str(result.get("account_id", "")).strip()
        token = str(result.get("token", "")).strip()
        if not account_id or not token:
            self.status_changed.emit("同步服务未返回有效登录凭据。")
            return
        warning = ""
        try:
            self._credentials.save_sync_token(token)
        except ValueError as exc:
            self.status_changed.emit(str(exc))
            return
        except RuntimeError as exc:
            warning = str(exc)
        previous_account = self._settings.get("sync_account_id", "").strip()
        if previous_account != account_id:
            database = Database(self._database_path)
            try:
                SyncRepository(database).reset_link()
            finally:
                database.close()
        self._settings.set("sync_account_id", account_id)
        self._settings.set("sync_username", str(result.get("username", "")).strip())
        self._settings.set(
            "sync_session_expires_at", str(result.get("expires_at", ""))
        )
        self._settings.set("sync_enabled", "true")
        self.account_created.emit(account_id)
        self.account_authenticated.emit(result)
        action_text = {
            "register": "注册并登录成功",
            "login": "登录成功",
            "upgrade": "旧版账户升级成功",
            "legacy-create": "兼容同步账户已创建",
        }.get(action, "同步账户已连接")
        self.status_changed.emit(
            warning or f"{action_text}；正在进行首次增量同步。"
        )
        self.reload()
        QTimer.singleShot(0, self.sync_now)

    @Slot(str)
    def _auth_failed(self, error: str) -> None:
        if self._auth_action == "logout":
            self.status_changed.emit(
                f"本机已退出登录，但服务器会话撤销失败：{error}"
            )
            self.account_authenticated.emit({"account_id": "", "username": ""})
            return
        labels = {
            "register": "注册失败",
            "login": "登录失败",
            "upgrade": "账户升级失败",
            "legacy-create": "创建同步账户失败",
        }
        self.status_changed.emit(f"{labels.get(self._auth_action, '账户操作失败')}：{error}")

    @Slot()
    def _auth_finished(self) -> None:
        self._auth_worker = None
        self._auth_thread = None
        self._auth_action = ""

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
        threads = [self._sync_thread, self._auth_thread]
        threads.extend(thread for thread, _worker in self._lease_tasks.values())
        for thread in threads:
            if thread is not None and thread.isRunning():
                thread.quit()
                thread.wait(5_000)
