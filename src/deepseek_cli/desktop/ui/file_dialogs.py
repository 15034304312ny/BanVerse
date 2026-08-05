"""Non-blocking file dialogs for Android's Qt activity integration."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QWidget


def open_mobile_file_dialog(
    parent: QWidget,
    title: str,
    name_filter: str,
    on_selected: Callable[[str], None],
    *,
    save: bool = False,
    initial_path: str = "",
) -> QFileDialog:
    """Open a QFileDialog without entering a nested Android event loop."""

    dialog = QFileDialog(parent, title)
    dialog.setNameFilter(name_filter)
    if save:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptSave)
        dialog.setFileMode(QFileDialog.FileMode.AnyFile)
        if initial_path:
            dialog.selectFile(initial_path)
    else:
        dialog.setAcceptMode(QFileDialog.AcceptMode.AcceptOpen)
        dialog.setFileMode(QFileDialog.FileMode.ExistingFile)
    def selected(path: str) -> None:
        on_selected(path)
        dialog.hide()

    dialog.fileSelected.connect(selected)
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
