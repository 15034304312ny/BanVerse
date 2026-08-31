"""编辑会话名称、头像和绑定角色。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
)

from ..assets import AvatarError, import_avatar
from ..data.repositories import Character, Conversation
from ..platform import is_android_platform
from .file_dialogs import open_mobile_file_dialog
from .mobile import configure_mobile_form
from .widgets.avatar_widget import AvatarWidget


class ConversationEditDialog(QDialog):
    def __init__(
        self,
        conversation: Conversation,
        characters: list[Character],
        parent=None,
        *,
        director_enabled: bool = True,
        director_available: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("编辑会话")
        if is_android_platform():
            self.resize(348, 480)
        else:
            self.setMinimumWidth(440)
        self.avatar_path = conversation.avatar_override_path
        self._file_dialog: QFileDialog | None = None
        layout = QFormLayout(self)
        configure_mobile_form(layout)
        self.name = QLineEdit(conversation.title)
        self.name.setMaxLength(80)
        layout.addRow("会话名称", self.name)

        avatar_row = QHBoxLayout()
        self.avatar = AvatarWidget(56)
        self.avatar.set_avatar(conversation.title, conversation.effective_avatar_path)
        choose = QPushButton("选择图片")
        choose.clicked.connect(self._choose_avatar)
        clear = QPushButton("恢复角色头像")
        clear.clicked.connect(self._clear_avatar)
        avatar_row.addWidget(self.avatar)
        avatar_row.addWidget(choose)
        avatar_row.addWidget(clear)
        avatar_row.addStretch(1)
        layout.addRow("会话头像", avatar_row)

        self.character = QComboBox()
        self.character.addItem("不绑定角色", None)
        for item in characters:
            self.character.addItem(item.name, item.id)
        index = self.character.findData(conversation.character_id)
        self.character.setCurrentIndex(max(0, index))
        layout.addRow("绑定角色", self.character)
        self.director = QCheckBox("允许该会话在关键轮次使用一次隐藏规划")
        self.director.setChecked(director_enabled)
        self.director.setEnabled(director_available)
        if not director_available:
            self.director.setToolTip("全局关键轮次 Director 当前未启用")
        layout.addRow("关键轮次规划", self.director)
        note = QLabel("更换角色只影响后续请求，不会改写已有消息或插入开场白。")
        note.setWordWrap(True)
        note.setProperty("muted", True)
        layout.addRow("", note)
        self.error = QLabel()
        self.error.setProperty("muted", True)
        layout.addRow("", self.error)
        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._validate)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("保存")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("取消")
        layout.addRow(buttons)

    def _choose_avatar(self) -> None:
        if is_android_platform():
            self._file_dialog = open_mobile_file_dialog(
                self,
                "选择头像",
                "图片 (*.png *.jpg *.jpeg *.webp)",
                self._avatar_selected,
            )
            self._file_dialog.finished.connect(self._file_dialog_finished)
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "选择头像", "", "图片 (*.png *.jpg *.jpeg *.webp)"
        )
        if path:
            self._avatar_selected(path)

    def _avatar_selected(self, path: str) -> None:
        try:
            self.avatar_path = import_avatar(path)
        except AvatarError as exc:
            self.error.setText(str(exc))
            return
        self.avatar.set_avatar(self.name.text(), self.avatar_path)

    def _file_dialog_finished(self, _result: int) -> None:
        self._file_dialog = None

    def _clear_avatar(self) -> None:
        self.avatar_path = ""
        self.avatar.set_avatar(self.name.text())

    def _validate(self) -> None:
        if not self.name.text().strip():
            self.error.setText("会话名称不能为空。")
            self.name.setFocus()
            return
        self.accept()
