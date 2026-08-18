"""Non-blocking file dialogs for Android's Qt activity integration."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt, QTimer, QUrl
from PySide6.QtWidgets import QDialog, QFileDialog, QWidget


def open_mobile_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str,
    on_selected: Callable[[str], None],
    *,
    save: bool = False,
    initial_path: str = "",
    mime_types: tuple[str, ...] = (),
) -> QFileDialog:
    """Open a QFileDialog without entering a nested Android event loop."""

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

    def selected(path: str) -> None:
        nonlocal delivered
        if delivered or not path:
            return
        delivered = True
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
        else None
    )
    # Keep ownership with the parent. Explicit deleteLater() is unsafe for
    # native QFileDialog wrappers on some Qt/Android and headless backends.
    # The hidden dialog is reclaimed with its parent widget.
    # OriginOS may return from its external picker without emitting
    # QFileDialog.finished. A modal wrapper would then block the whole Qt
    # window indefinitely, so the Android picker must remain non-modal.
    dialog.setModal(False)
    dialog.setWindowModality(Qt.WindowModality.NonModal)
    dialog.show()
    return dialog
