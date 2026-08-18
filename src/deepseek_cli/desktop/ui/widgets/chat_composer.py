"""多行聊天输入栏。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal, qWarning
from PySide6.QtGui import QKeyEvent, QPixmap
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
)

from ...assets import AvatarError, load_chat_image
from ...platform import is_android_platform
from ..file_dialogs import open_mobile_file_dialog
from .sticker_picker import StickerPickerDialog


class ChatComposer(QFrame):
    send_requested = Signal(str, str)
    sticker_requested = Signal(str)
    stop_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("composer")
        self._generating = False
        self._attachment_path = ""
        self._mobile = is_android_platform()
        self._attachment_dialog: QFileDialog | None = None
        self._sticker_dialog: StickerPickerDialog | None = None
        root = QVBoxLayout(self)
        root.setContentsMargins(
            12 if self._mobile else 20,
            8 if self._mobile else 10,
            12 if self._mobile else 20,
            8 if self._mobile else 12,
        )
        root.setSpacing(8)

        self.attachment_row = QFrame()
        attachment_layout = QHBoxLayout(self.attachment_row)
        attachment_layout.setContentsMargins(0, 0, 0, 0)
        attachment_layout.setSpacing(8)
        self.attachment_preview = QLabel()
        self.attachment_preview.setFixedSize(56, 56)
        self.attachment_preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        attachment_layout.addWidget(self.attachment_preview)
        self.attachment_name = QLabel()
        self.attachment_name.setProperty("muted", True)
        attachment_layout.addWidget(self.attachment_name)
        attachment_layout.addStretch(1)
        remove = QPushButton("移除")
        remove.setAccessibleName("移除待发送图片")
        remove.clicked.connect(self.clear_attachment)
        attachment_layout.addWidget(remove)
        root.addWidget(self.attachment_row)
        self.attachment_row.hide()

        self.attach_button = QPushButton("图片")
        self.attach_button.setAccessibleName("选择要发送的图片")
        self.attach_button.setMinimumSize(64 if self._mobile else 62, 44)
        self.attach_button.clicked.connect(self._choose_attachment)
        self.sticker_button = QPushButton("表情")
        self.sticker_button.setAccessibleName("打开表情包")
        self.sticker_button.setToolTip("选择并直接发送表情")
        self.sticker_button.setMinimumSize(64 if self._mobile else 62, 44)
        self.sticker_button.clicked.connect(self._choose_sticker)

        self.editor = QTextEdit()
        self.editor.setPlaceholderText(
            "输入消息…"
            if self._mobile
            else "输入消息，Enter 发送，Shift+Enter 换行"
        )
        self.editor.setAccessibleName("消息输入框")
        self.editor.setFixedHeight(52 if self._mobile else 72)
        self.editor.installEventFilter(self)
        self.editor.textChanged.connect(self._update_button)

        self.action = QPushButton("发送")
        self.action.setObjectName("primaryButton")
        self.action.setMinimumSize(80 if self._mobile else 72, 44)
        self.action.setAccessibleName("发送消息")
        self.action.clicked.connect(self._activate)
        if self._mobile:
            root.addWidget(self.editor)
            actions = QHBoxLayout()
            actions.setContentsMargins(0, 0, 0, 0)
            actions.setSpacing(8)
            actions.addWidget(self.attach_button)
            actions.addWidget(self.sticker_button)
            actions.addStretch(1)
            actions.addWidget(self.action)
            root.addLayout(actions)
        else:
            layout = QHBoxLayout()
            layout.setContentsMargins(0, 0, 0, 0)
            layout.setSpacing(8)
            layout.addWidget(
                self.attach_button,
                alignment=Qt.AlignmentFlag.AlignBottom,
            )
            layout.addWidget(
                self.sticker_button,
                alignment=Qt.AlignmentFlag.AlignBottom,
            )
            layout.addWidget(self.editor, 1)
            layout.addWidget(
                self.action,
                alignment=Qt.AlignmentFlag.AlignBottom,
            )
            root.addLayout(layout)
        self._update_button()

    def eventFilter(self, watched, event) -> bool:
        if watched is self.editor and event.type() == QEvent.Type.KeyPress:
            key_event = event
            if isinstance(key_event, QKeyEvent) and key_event.key() in {
                Qt.Key.Key_Return,
                Qt.Key.Key_Enter,
            }:
                if key_event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
                    return False
                if (
                    not self._generating
                    and (
                        self.editor.toPlainText().strip()
                        or self._attachment_path
                    )
                ):
                    self._activate()
                return True
        return super().eventFilter(watched, event)

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        self.editor.setEnabled(not generating)
        self.attach_button.setEnabled(not generating)
        self.sticker_button.setEnabled(not generating)
        self.action.setText("停止" if generating else "发送")
        self.action.setAccessibleName("停止生成" if generating else "发送消息")
        self._update_button()

    def set_available(self, available: bool) -> None:
        self.editor.setEnabled(available and not self._generating)
        self.attach_button.setEnabled(available and not self._generating)
        self.sticker_button.setEnabled(available and not self._generating)
        self.action.setEnabled(
            available
            and (
                self._generating
                or bool(self.editor.toPlainText().strip())
                or bool(self._attachment_path)
            )
        )

    def _activate(self) -> None:
        if self._generating:
            self.stop_requested.emit()
            return
        text = self.editor.toPlainText().strip()
        if not text and not self._attachment_path:
            return
        attachment = self._attachment_path
        self.editor.clear()
        self.clear_attachment()
        self.send_requested.emit(text, attachment)

    def _update_button(self) -> None:
        self.action.setEnabled(
            self._generating
            or bool(self.editor.toPlainText().strip())
            or bool(self._attachment_path)
        )

    def set_attachment(self, path: str) -> None:
        try:
            image = load_chat_image(path)
        except AvatarError as exc:
            qWarning(f"BanVerse could not read the selected image: {exc}")
            self.attachment_preview.setText("!")
            self.attachment_name.setText(str(exc))
            self.attachment_row.show()
            return
        pixmap = QPixmap.fromImage(image)
        self._attachment_path = path
        self.attachment_preview.setPixmap(
            pixmap.scaled(
                52,
                52,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        self.attachment_name.setText(path.replace("\\", "/").rsplit("/", 1)[-1])
        self.attachment_name.setToolTip(path)
        self.attachment_row.show()
        self._update_button()

    def clear_attachment(self) -> None:
        self._attachment_path = ""
        self.attachment_preview.clear()
        self.attachment_name.clear()
        self.attachment_row.hide()
        self._update_button()

    def _choose_attachment(self) -> None:
        if self._mobile:
            self._attachment_dialog = open_mobile_file_dialog(
                self,
                "发送图片",
                "图片 (*.png *.jpg *.jpeg *.webp)",
                self.set_attachment,
                mime_types=("image/png", "image/jpeg", "image/webp"),
            )
            self._attachment_dialog.finished.connect(
                self._attachment_dialog_finished
            )
            return
        path, _ = QFileDialog.getOpenFileName(
            self,
            "发送图片",
            "",
            "图片 (*.png *.jpg *.jpeg *.webp)",
        )
        if path:
            self.set_attachment(path)

    def _attachment_dialog_finished(self, _result: int) -> None:
        self._attachment_dialog = None

    def _choose_sticker(self) -> None:
        dialog = StickerPickerDialog(self)
        if self._mobile:
            self._sticker_dialog = dialog
            dialog.sticker_selected.connect(self.sticker_requested)
            dialog.finished.connect(self._sticker_dialog_finished)
            dialog.open()
            return
        if dialog.exec() and dialog.selected_sticker_id:
            self.sticker_requested.emit(dialog.selected_sticker_id)

    def _sticker_dialog_finished(self, _result: int) -> None:
        dialog = self._sticker_dialog
        self._sticker_dialog = None
        if dialog is not None:
            dialog.deleteLater()
