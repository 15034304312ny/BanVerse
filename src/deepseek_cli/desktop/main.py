"""Windows 与 Android 图形应用入口。"""

from __future__ import annotations

import os
import sys
import logging
import traceback
from importlib import resources
from logging.handlers import RotatingFileHandler
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QStandardPaths, QTimer
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import QApplication, QMessageBox

from ..branding import PRODUCT_NAME
from .platform import is_android_platform

LOGGER = logging.getLogger("banverse.startup")


def app_data_path() -> Path:
    location = QStandardPaths.writableLocation(
        QStandardPaths.StandardLocation.AppDataLocation
    )
    return Path(location)


def application_icon() -> QIcon:
    try:
        data = (
            resources.files("deepseek_cli.desktop")
            .joinpath("resources", "app_icon.png")
            .read_bytes()
        )
        pixmap = QPixmap()
        if pixmap.loadFromData(data, "PNG"):
            return QIcon(pixmap)
    except (FileNotFoundError, OSError):
        pass
    return QIcon()


def configure_startup_logging(data_root: Path) -> Path:
    """Persist Python startup failures in the application-private data folder."""

    data_root.mkdir(parents=True, exist_ok=True)
    log_path = data_root / "startup.log"
    if not any(
        isinstance(handler, RotatingFileHandler)
        and Path(handler.baseFilename) == log_path
        for handler in LOGGER.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=2,
            encoding="utf-8",
        )
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s: %(message)s"
            )
        )
        LOGGER.addHandler(handler)
        LOGGER.setLevel(logging.INFO)
        LOGGER.propagate = False

    def record_unhandled(
        exception_type: type[BaseException],
        exception: BaseException,
        exception_traceback,
    ) -> None:
        LOGGER.critical(
            "Unhandled exception",
            exc_info=(exception_type, exception, exception_traceback),
        )
        sys.__excepthook__(exception_type, exception, exception_traceback)

    sys.excepthook = record_unhandled
    LOGGER.info("Starting %s; platform=%s", PRODUCT_NAME, sys.platform)
    return log_path


def _initialize_optional_audio(
    application: QApplication,
    window,
    settings,
    credentials,
) -> None:
    """Initialize TTS and notification audio without risking the chat window."""

    speech = None
    notification_sound = None
    try:
        from .tts import SpeechController

        speech = SpeechController(
            application,
            settings=settings,
            credentials=credentials,
        )
        application.aboutToQuit.connect(speech.shutdown)
    except Exception:
        LOGGER.exception("TTS initialization failed; continuing without speech")
    try:
        from .notification_sound import NotificationSound

        notification_sound = NotificationSound(application)
        application.aboutToQuit.connect(notification_sound.shutdown)
    except Exception:
        LOGGER.exception(
            "Notification audio initialization failed; continuing silently"
        )
    window.set_audio_services(
        speech=speech,
        notification_sound=notification_sound,
    )


def _show_startup_failure(
    application: QApplication,
    error: BaseException,
    log_path: Path,
) -> int:
    LOGGER.exception("Core application startup failed")
    message = QMessageBox()
    message.setIcon(QMessageBox.Icon.Critical)
    message.setWindowTitle("启动失败")
    message.setText(f"{PRODUCT_NAME} 无法完成启动，但崩溃信息已经保存。")
    message.setInformativeText(f"诊断日志：{log_path}")
    message.setDetailedText(
        "".join(traceback.format_exception(type(error), error, error.__traceback__))
    )
    message.setStandardButtons(QMessageBox.StandardButton.Close)
    message.show()
    return application.exec()


def main() -> int:
    android = is_android_platform()
    if android:
        os.environ.setdefault("QT_LOGGING_TO_CONSOLE", "1")
    # Keep legacy storage identifiers so renaming does not orphan chat.db,
    # avatars, generated images, or credentials from previous releases.
    QCoreApplication.setOrganizationName("DeepSeekChat")
    QCoreApplication.setApplicationName(
        "DeepSeekChatAndroid" if android else "DeepSeekChatDesktop"
    )
    application = QApplication(sys.argv)
    application.setApplicationDisplayName(PRODUCT_NAME)
    application.setWindowIcon(application_icon())

    database = None
    data_root = app_data_path()
    log_path = configure_startup_logging(data_root)
    try:
        # Delay application-specific and optional native module imports until a
        # QApplication and persistent crash log are both available.
        from .builtin_characters import BuiltinCharacterManager
        from .data.database import Database
        from .data.repositories import (
            CharacterRepository,
            ChatRepository,
            SettingsRepository,
        )
        from .security.credentials import CredentialStore
        from .ui.main_window import MainWindow

        database = Database(data_root / "chat.db")
        chats = ChatRepository(database)
        characters = CharacterRepository(database)
        settings = SettingsRepository(database)
        credentials = CredentialStore()
        builtins = BuiltinCharacterManager(
            database,
            characters,
            settings,
            app_data_root=data_root,
        )
        builtins.seed_on_startup()
        window = MainWindow(
            chats,
            characters,
            settings,
            credentials,
            builtins=builtins,
            speech=None,
            notification_sound=None,
            media_root=data_root,
        )
        if android:
            window.showMaximized()
        else:
            window.show()
        QTimer.singleShot(
            350,
            lambda: _initialize_optional_audio(
                application,
                window,
                settings,
                credentials,
            ),
        )
        if (
            os.environ.get("BANVERSE_SMOKE_TEST") == "1"
            or os.environ.get("DEEPSEEK_CHAT_SMOKE_TEST") == "1"
        ):
            QTimer.singleShot(500, application.quit)
        return application.exec()
    except Exception as error:
        return _show_startup_failure(application, error, log_path)
    finally:
        if database is not None:
            database.close()


if __name__ == "__main__":
    raise SystemExit(main())
