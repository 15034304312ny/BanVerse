"""会话记忆治理：查看、确认、编辑、固定与删除。"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
)

_CATEGORY_LABELS = {
    "user_fact": "用户事实",
    "shared_experience": "共同经历",
    "preference_boundary": "偏好与边界",
    "open_thread": "未完话题",
    "character_commitment": "角色承诺",
}
_STATUS_LABELS = {
    "candidate": "待确认",
    "active": "有效",
    "corrected": "已纠正",
    "superseded": "已取代",
    "deleted": "已删除",
}
_SOURCE_LABELS = {
    "user_explicit": "用户明确陈述",
    "user_managed": "用户手动管理",
    "assistant_inferred": "模型候选（未确认）",
    "role_state": "本轮共同事件",
    "image_analysis": "图片识别（不作为确认事实）",
    "imported": "外部导入（不受信任）",
    "sync": "另一设备同步",
}


class MemoryManagerDialog(QDialog):
    def __init__(self, chats, settings, conversation_id: str, parent=None) -> None:
        super().__init__(parent)
        self._chats = chats
        self._settings = settings
        self._conversation_id = conversation_id
        self._conversation = chats.get_conversation(conversation_id)
        self.setWindowTitle("记忆管理")
        self.resize(720, 560)

        root = QVBoxLayout(self)
        intro = QLabel(
            "记忆与消息分开管理。待确认候选不会进入角色上下文；删除会清空正文并同步删除状态。"
        )
        intro.setWordWrap(True)
        intro.setProperty("muted", True)
        root.addWidget(intro)

        self.character_enabled = QCheckBox("为当前角色启用连续性记忆")
        character_id = self._character_id()
        global_enabled = settings.get_bool("role_memory_enabled", True)
        self.character_enabled.setVisible(bool(character_id))
        self.character_enabled.setEnabled(global_enabled)
        self.character_enabled.setChecked(
            global_enabled
            and settings.get_bool(
                f"role_memory_character_{character_id}", True
            )
        )
        self.character_enabled.toggled.connect(self._set_character_enabled)
        root.addWidget(self.character_enabled)

        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索记忆内容")
        self.search.setAccessibleName("搜索当前会话记忆")
        self.search.textChanged.connect(self.refresh)
        root.addWidget(self.search)

        self.list = QListWidget()
        self.list.setAccessibleName("当前会话记忆列表")
        self.list.currentItemChanged.connect(self._selection_changed)
        root.addWidget(self.list, 1)

        self.details = QLabel("选择一条记忆查看来源与状态。")
        self.details.setWordWrap(True)
        self.details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        self.details.setProperty("muted", True)
        root.addWidget(self.details)

        actions = QHBoxLayout()
        self.confirm_button = QPushButton("确认有效")
        self.edit_button = QPushButton("编辑")
        self.pin_button = QPushButton("固定/取消固定")
        self.delete_button = QPushButton("删除")
        self.confirm_button.clicked.connect(self._confirm_selected)
        self.edit_button.clicked.connect(self._edit_selected)
        self.pin_button.clicked.connect(self._toggle_pin)
        self.delete_button.clicked.connect(self._delete_selected)
        for button in (
            self.confirm_button,
            self.edit_button,
            self.pin_button,
            self.delete_button,
        ):
            actions.addWidget(button)
        actions.addStretch(1)
        root.addLayout(actions)

        destructive = QHBoxLayout()
        clear = QPushButton("清空当前会话记忆")
        reset = QPushButton("重置角色连续性")
        clear.clicked.connect(self._clear_memories)
        reset.clicked.connect(self._reset_continuity)
        destructive.addWidget(clear)
        destructive.addWidget(reset)
        destructive.addStretch(1)
        root.addLayout(destructive)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)
        self.refresh()

    def _character_id(self) -> str:
        if self._conversation is None:
            return ""
        return self._conversation.character_id or ""

    def _set_character_enabled(self, checked: bool) -> None:
        character_id = self._character_id()
        if character_id:
            self._settings.set(
                f"role_memory_character_{character_id}",
                "true" if checked else "false",
            )

    def refresh(self, *_args) -> None:
        selected_id = self._selected_id()
        records = self._chats.list_memories(
            self._conversation_id,
            query=self.search.text(),
            include_inactive=True,
        )
        self.list.clear()
        for record in records:
            if record.status == "deleted":
                continue
            prefix = "📌 " if record.pinned else ""
            item = QListWidgetItem(
                f"{prefix}[{_CATEGORY_LABELS.get(record.category, record.category)} · "
                f"{_STATUS_LABELS.get(record.status, record.status)}] {record.content}"
            )
            item.setData(Qt.ItemDataRole.UserRole, record.id)
            self.list.addItem(item)
            if record.id == selected_id:
                self.list.setCurrentItem(item)
        if self.list.currentItem() is None and self.list.count():
            self.list.setCurrentRow(0)
        self._selection_changed(self.list.currentItem(), None)

    def _selected_id(self) -> str:
        item = self.list.currentItem()
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _selected(self):
        memory_id = self._selected_id()
        return self._chats.get_memory(memory_id) if memory_id else None

    def _selection_changed(self, current, _previous) -> None:
        record = self._selected() if current is not None else None
        enabled = record is not None
        for button in (
            self.confirm_button,
            self.edit_button,
            self.pin_button,
            self.delete_button,
        ):
            button.setEnabled(enabled)
        self.confirm_button.setEnabled(
            bool(record and record.status == "candidate")
        )
        if record is None:
            self.details.setText("选择一条记忆查看来源与状态。")
            return
        source = _SOURCE_LABELS.get(record.source_type, record.source_type)
        source_turn = record.source_turn_id or "无来源轮次"
        last_used = record.last_used_at or "尚未召回"
        confirmed = record.confirmed_at or "尚未由用户确认"
        self.details.setText(
            f"来源：{source} · 轮次：{source_turn}\n"
            f"置信度：{record.confidence:.0%} · 显著性：{record.salience:.0%} · "
            f"创建：{record.created_at} · 用户确认：{confirmed} · 最后使用：{last_used}"
        )

    def _confirm_selected(self) -> None:
        record = self._selected()
        if record is None:
            return
        self._chats.update_memory(record.id, status="active")
        self.refresh()

    def _edit_selected(self) -> None:
        record = self._selected()
        if record is None:
            return
        text, accepted = QInputDialog.getMultiLineText(
            self,
            "编辑记忆",
            "内容",
            record.content,
        )
        if not accepted:
            return
        try:
            self._chats.update_memory(record.id, content=text)
        except ValueError as exc:
            QMessageBox.warning(self, "无法保存", str(exc))
            return
        self.refresh()

    def _toggle_pin(self) -> None:
        record = self._selected()
        if record is None:
            return
        self._chats.update_memory(record.id, pinned=not record.pinned)
        self.refresh()

    def _delete_selected(self) -> None:
        record = self._selected()
        if record is None:
            return
        answer = QMessageBox.question(
            self,
            "删除记忆",
            "删除后正文会被清空，并向其他已登录设备同步删除状态。是否继续？",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._chats.delete_memory(record.id)
        self.refresh()

    def _clear_memories(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空记忆",
            "只清空当前会话的记忆记录，不删除聊天消息。是否继续？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._chats.clear_memories(self._conversation_id)
            self.refresh()

    def _reset_continuity(self) -> None:
        answer = QMessageBox.question(
            self,
            "重置角色连续性",
            "将清空当前会话记忆和隐藏角色状态，但保留全部消息。是否继续？",
        )
        if answer == QMessageBox.StandardButton.Yes:
            self._chats.reset_role_continuity(self._conversation_id)
            self.refresh()
