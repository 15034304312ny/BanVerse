"""内置表情包网格选择器。"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ...stickers import STICKERS
from ...platform import is_android_platform


class StickerPickerDialog(QDialog):
    sticker_selected = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("选择表情")
        self.setModal(True)
        self._columns = 4 if is_android_platform() else 6
        if is_android_platform():
            self.resize(344, 480)
        else:
            self.setMinimumWidth(390)
        self.selected_sticker_id = ""

        root = QVBoxLayout(self)
        root.setContentsMargins(16, 14, 16, 16)
        root.setSpacing(10)
        title = QLabel("表情包")
        title.setObjectName("pageTitle")
        root.addWidget(title)
        hint = QLabel("点击一个表情即可直接发送")
        hint.setProperty("muted", True)
        root.addWidget(hint)

        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(8)
        emoji_font = QFont("Segoe UI Emoji")
        emoji_font.setPointSize(22)
        for index, sticker in enumerate(STICKERS):
            button = QPushButton(sticker.emoji)
            button.setObjectName("stickerButton")
            button.setFont(emoji_font)
            button.setFixedSize(64, 58)
            button.setToolTip(sticker.label)
            button.setAccessibleName(f"发送{sticker.label}表情")
            button.clicked.connect(
                lambda _checked=False, sticker_id=sticker.id: self._select(
                    sticker_id
                )
            )
            grid.addWidget(
                button,
                index // self._columns,
                index % self._columns,
                alignment=Qt.AlignmentFlag.AlignCenter,
            )
        root.addLayout(grid)

    def _select(self, sticker_id: str) -> None:
        self.selected_sticker_id = sticker_id
        self.sticker_selected.emit(sticker_id)
        self.accept()
