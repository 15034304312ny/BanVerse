"""Non-blocking file dialogs for Android's Qt activity integration."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal, qWarning
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget


def _native_android_bridge_available() -> bool:
    """Return whether the p4a private directory is available at runtime."""

    return bool(os.environ.get("ANDROID_PRIVATE", "").strip())


def _picker_kind(name_filter: str, mime_types: tuple[str, ...]) -> str:
    values = tuple(value.lower() for value in mime_types)
    lowered_filter = name_filter.lower()
    if (
        values and all(value.startswith("image/") for value in values)
    ) or any(
        marker in lowered_filter
        for marker in ("图片", "*.png", "*.jpg", "*.jpeg", "*.webp")
    ):
        return "image"
    if "json" in lowered_filter or "application/json" in values:
        return "json"
    return "file"


class AndroidDocumentPicker(QObject):
    """Bridge OriginOS's document picker result through app-private storage."""

    finished = Signal(int)

    def __init__(
        self,
        parent: QWidget,
        on_selected: Callable[[str], None],
        *,
        kind: str,
    ) -> None:
        super().__init__(parent)
        self._on_selected = on_selected
        self._token = uuid4().hex
        private_root = Path(os.environ["ANDROID_PRIVATE"])
        self._result_file = (
            private_root / "banverse-picker" / f"{self._token}.result"
        )
        self._poll = QTimer(self)
        self._poll.setInterval(150)
        self._poll.timeout.connect(self._poll_result)
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(10 * 60 * 1000)
        self._timeout.timeout.connect(self._cancel)
        self._url = QUrl(
            f"banverse-picker://open?token={self._token}&kind={kind}"
        )
        QTimer.singleShot(0, self._launch)

    def _launch(self) -> None:
        try:
            self._result_file.unlink(missing_ok=True)
        except OSError as exc:
            qWarning(f"BanVerse could not prepare Android picker: {exc}")
            self._finish(False)
            return
        if not QDesktopServices.openUrl(self._url):
            qWarning("BanVerse Android document picker activity is unavailable")
            self._finish(False)
            return
        self._poll.start()
        self._timeout.start()

    def _poll_result(self) -> None:
        if not self._result_file.is_file():
            return
        try:
            lines = self._result_file.read_text(encoding="utf-8").splitlines()
            self._result_file.unlink(missing_ok=True)
        except OSError:
            return
        if len(lines) >= 2 and lines[0] == "ok" and lines[1]:
            path = lines[1]
            if Path(path).is_file():
                self._on_selected(path)
                self._finish(True)
                return
        if len(lines) >= 2 and lines[0] == "error":
            qWarning(f"BanVerse Android document picker failed: {lines[1]}")
        self._finish(False)

    def _cancel(self) -> None:
        qWarning("BanVerse Android document picker timed out")
        self._finish(False)

    def _finish(self, accepted: bool) -> None:
        self._poll.stop()
        self._timeout.stop()
        result = (
            QDialog.DialogCode.Accepted.value
            if accepted
            else QDialog.DialogCode.Rejected.value
        )
        self.finished.emit(result)


def open_mobile_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str,
    on_selected: Callable[[str], None],
    *,
    save: bool = False,
    initial_path: str = "",
    mime_types: tuple[str, ...] = (),
) -> QFileDialog | AndroidDocumentPicker:
    """Open a QFileDialog without entering a nested Android event loop."""

    if _native_android_bridge_available() and not save:
        return AndroidDocumentPicker(
            parent,
            on_selected,
            kind=_picker_kind(name_filter, mime_types),
        )

    dialog = QFileDialog(parent, title)
    dialog.setNameFilter(name_filter)
    if mime_types:
        dialog.setMimeTypeFilters(list(mime_types))
    if save:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if initial_path:
            dialog.selectFile(initial_path)
    else:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    delivered = False
    pending_path = ""
    picker_left_foreground = False
    recovery_generation = 0
    selection_poll: QTimer | None = None

    application = QGuiApplication.instance()

    def disconnect_application_state() -> None:
        if application is None:
            return
        try:
            application.applicationStateChanged.disconnect(
                application_state_changed
            )
        except (RuntimeError, TypeError):
            pass

    def selected(path: str) -> None:
        nonlocal delivered
        if delivered or not path:
            return
        delivered = True
        if selection_poll is not None:
            selection_poll.stop()
        disconnect_application_state()
        on_selected(path)
        dialog.hide()

    def selected_url(url: QUrl) -> None:
        selected(url.toLocalFile() if url.isLocalFile() else url.toString())

    def deliver_pending_path() -> None:
        if not delivered:
            selected(pending_path)

    def selected_path(path: str) -> None:
        nonlocal pending_path
        if delivered or not path:
            return
        if path.startswith("content:/") and not path.startswith("content://"):
            path = "content://" + path.removeprefix("content:/")
        pending_path = path
        # Android can emit both fileSelected("content:/...") and the proper
        # urlSelected(content://...) for one result. Defer the string signal
        # by one event-loop turn so the lossless QUrl always wins.
        QTimer.singleShot(0, deliver_pending_path)

    def accepted() -> None:
        urls = dialog.selectedUrls()
        if urls:
            selected_url(urls[0])
            return
        files = dialog.selectedFiles()
        if files:
            selected_path(files[0])

    def recover_after_picker_return(generation: int) -> None:
        if delivered or generation != recovery_generation:
            return
        accepted()

    def poll_native_selection() -> None:
        if delivered:
            return
        # Some OriginOS builds update the native helper's selected URL but
        # omit every QFileDialog completion signal. Only accept Android
        # content URIs here, so a default directory can never become an
        # attachment while the external picker is still open.
        for url in dialog.selectedUrls():
            if url.scheme().lower() == "content":
                selected_url(url)
                return
        for path in dialog.selectedFiles():
            if path.lower().startswith("content:"):
                selected_path(path)
                return

    def application_state_changed(state: Qt.ApplicationState) -> None:
        nonlocal picker_left_foreground, recovery_generation
        if state != Qt.ApplicationState.ApplicationActive:
            picker_left_foreground = True
            return
        if not picker_left_foreground or delivered:
            return
        picker_left_foreground = False
        recovery_generation += 1
        generation = recovery_generation
        QTimer.singleShot(0, lambda: recover_after_picker_return(generation))
        QTimer.singleShot(
            180, lambda: recover_after_picker_return(generation)
        )
        QTimer.singleShot(
            600, lambda: recover_after_picker_return(generation)
        )

    dialog.fileSelected.connect(selected_path)
    dialog.filesSelected.connect(
        lambda paths: selected_path(paths[0] if paths else "")
    )
    dialog.urlSelected.connect(selected_url)
    dialog.urlsSelected.connect(
        lambda urls: selected_url(urls[0]) if urls else None
    )
    # OriginOS 6 的 ACTION_OPEN_DOCUMENT 返回后只触发 accepted，未必触发
    # fileSelected/urlSelected。accepted 时主动读取 selectedUrls，避免用户
    # 已完成选图但附件仍为空。
    dialog.accepted.connect(accepted)
    # Some OriginOS file-manager builds close Qt's native wrapper with only
    # finished(Accepted). Read the selected URL once more before the caller's
    # finished handler drops its dialog reference.
    dialog.finished.connect(
        lambda result: accepted()
        if result == QDialog.DialogCode.Accepted.value
        else disconnect_application_state()
    )
    # Keep ownership with the parent. Explicit deleteLater() is unsafe for
    # native QFileDialog wrappers on some Qt/Android and headless backends.
    # The hidden dialog is reclaimed with its parent widget.
    # OriginOS may return from its external picker without emitting
    # QFileDialog.finished. Keep the Qt wrapper non-modal so a missed result
    # cannot block the whole chat window.
    if application is not None:
        application.applicationStateChanged.connect(
            application_state_changed
        )
    selection_poll = QTimer(dialog)
    selection_poll.setInterval(200)
    selection_poll.timeout.connect(poll_native_selection)
    selection_poll.start()
    dialog.setModal(False)
    dialog.setWindowModality(Qt.WindowModality.NonModal)
    dialog.show()
    return dialog
