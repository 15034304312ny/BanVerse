"""会话列表侧栏。"""

from __future__ import annotations

from PySide6.QtCore import QSignalBlocker, QSize, Signal
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...data.repositories import ChatRepository, Conversation
from ..mobile import enable_touch_scrolling
from ..widgets.avatar_widget import AvatarWidget


class ConversationRow(QWidget):
    MINIMUM_HEIGHT = 92
    PREVIEW_LIMIT = 90

    def __init__(self, conversation: Conversation) -> None:
        super().__init__()
        self.setMinimumHeight(self.MINIMUM_HEIGHT)
        self.setAccessibleName(
            f"与{conversation.display_name}的对话，{self._summary_text(conversation)}"
        )
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 10, 8, 10)
        layout.setSpacing(12)

        self.avatar = AvatarWidget(48)
        self.avatar.set_avatar(
            conversation.display_name, conversation.effective_avatar_path
        )
        layout.addWidget(self.avatar)

        content = QVBoxLayout()
        content.setContentsMargins(0, 0, 0, 0)
        content.setSpacing(4)
        self.name = QLabel(conversation.display_name)
        self.name.setObjectName("conversationName")
        self.name.setToolTip(conversation.display_name)
        content.addWidget(self.name)

        full_preview = self._summary_text(conversation)
        normalized = " ".join(full_preview.split())
        visible = (
            normalized
            if len(normalized) <= self.PREVIEW_LIMIT
            else f"{normalized[: self.PREVIEW_LIMIT]}…"
        )
        self.preview = QLabel(visible)
        self.preview.setObjectName("conversationPreview")
        self.preview.setProperty("muted", True)
        self.preview.setWordWrap(True)
        self.preview.setToolTip(full_preview)
        self.preview.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.preview.setMinimumWidth(0)
        content.addWidget(self.preview)
        layout.addLayout(content, 1)

    @staticmethod
    def _summary_text(conversation: Conversation) -> str:
        if conversation.summary_status == "ready" and conversation.ai_summary:
            return conversation.ai_summary
        if conversation.summary_status == "pending":
            return "AI 正在生成摘要…"
        if conversation.summary_status == "failed":
            return "AI 摘要暂时生成失败"
        return "尚无 AI 回复摘要"


class ConversationsPage(QWidget):
    conversation_selected = Signal(str)
    new_requested = Signal()
    edit_requested = Signal(str)

    def __init__(self, repository: ChatRepository) -> None:
        super().__init__()
        self.setObjectName("sidebar")
        self.setFixedWidth(292)
        self._repository = repository
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 14, 12, 0)
        layout.setSpacing(10)
        header = QHBoxLayout()
        title = QLabel("消息")
        title.setObjectName("pageTitle")
        header.addWidget(title)
        header.addStretch(1)
        new_button = QPushButton("新建")
        new_button.setMinimumHeight(40)
        new_button.clicked.connect(self.new_requested)
        header.addWidget(new_button)
        layout.addLayout(header)
        self.search = QLineEdit()
        self.search.setPlaceholderText("搜索会话")
        self.search.setMinimumHeight(42)
        self.search.textChanged.connect(self.refresh)
        layout.addWidget(self.search)
        self.list = QListWidget()
        self.list.setAccessibleName("会话列表")
        enable_touch_scrolling(self.list)
        self.list.currentItemChanged.connect(self._selected)
        self.list.itemDoubleClicked.connect(lambda item: self.edit_requested.emit(item.data(256)))
        layout.addWidget(self.list, 1)
        self.refresh()

    def refresh(self, *_args, select_id: str | None = None) -> None:
        current = select_id
        if current is None and self.list.currentItem() is not None:
            current = self.list.currentItem().data(256)
        # Rebuilding the sidebar must not re-open the completed turn while its
        # delayed bubbles are being delivered.  That race used to show every
        # segment at once and then append the same segments a second time.
        blocker = QSignalBlocker(self.list)
        try:
            self.list.clear()
            for conversation in self._repository.list_conversations(
                self.search.text()
            ):
                row = ConversationRow(conversation)
                item = QListWidgetItem()
                item.setData(256, conversation.id)
                item.setToolTip(row.preview.toolTip())
                item.setSizeHint(
                    QSize(row.sizeHint().width(), row.MINIMUM_HEIGHT)
                )
                self.list.addItem(item)
                self.list.setItemWidget(item, row)
                if conversation.id == current:
                    self.list.setCurrentItem(item)
        finally:
            del blocker

    def select(self, conversation_id: str) -> None:
        for index in range(self.list.count()):
            item = self.list.item(index)
            if item.data(256) == conversation_id:
                self.list.setCurrentItem(item)
                return

    def _selected(self, current, _previous) -> None:
        if current is not None:
            self.conversation_selected.emit(current.data(256))
