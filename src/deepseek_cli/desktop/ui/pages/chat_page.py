"""聊天详情页。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ...data.repositories import Conversation, Turn
from ...ai_features import deserialize_reply_segments
from ...platform import is_android_platform
from ....model_catalog import MODELS, ModelInfo
from ..mobile import enable_touch_scrolling
from ..widgets.chat_composer import ChatComposer
from ..widgets.message_bubble import MessageBubble


_ERROR_MESSAGES = {
    "authentication": "API Key 无效，请前往设置检查。",
    "timeout": "请求超时，请稍后重试。",
    "network": "网络连接失败，请检查网络后重试。",
    "rate_limit": "请求过于频繁，请稍后重试。",
    "empty_response": "没有收到有效回答。",
    "text_endpoint_invalid": "文本 API 地址无效，请前往设置检查基础地址。",
    "text_model_unavailable": "当前文本模型不可用，请前往设置检查模型名。",
    "text_bad_request": "文本平台拒绝了本次请求，请检查模型与平台配置。",
    "service_error": "服务暂时不可用，请稍后重试。",
    "image_authentication": "图片 AI API Key 无效，请前往设置检查。",
    "image_timeout": "图片服务请求超时，请稍后重试。",
    "image_network": "无法连接图片服务，请检查网络。",
    "image_rate_limit": "图片生成请求过于频繁，请稍后重试。",
    "image_quota": (
        "当前图片平台的额度或计费状态不可用，请前往所选平台检查余额与配额。"
    ),
    "image_model_unavailable": (
        "当前图片平台无法使用所选模型，请在设置中检查模型名或账号权限。"
    ),
    "image_service_error": "图片服务暂时不可用，请稍后重试。",
}


class ChatPage(QWidget):
    send_requested = Signal(str, str)
    sticker_requested = Signal(str)
    stop_requested = Signal()
    retry_requested = Signal(str)
    model_changed = Signal(str)
    delete_requested = Signal()
    edit_requested = Signal()
    speech_requested = Signal(str, str)
    speech_stop_requested = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("chatPage")
        self._stream_bubble: MessageBubble | None = None
        self._conversation: Conversation | None = None
        self._speech_bubbles: dict[str, MessageBubble] = {}
        self._available = False
        self._generating = False
        self._model_options_editable = True
        self._mobile = is_android_platform()

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QFrame()
        header.setObjectName("header")
        header.setMinimumHeight(112 if self._mobile else 68)
        header_layout = (
            QVBoxLayout(header) if self._mobile else QHBoxLayout(header)
        )
        header_layout.setContentsMargins(
            12 if self._mobile else 20,
            8 if self._mobile else 10,
            12 if self._mobile else 16,
            8 if self._mobile else 10,
        )
        header_layout.setSpacing(6 if self._mobile else 8)
        self.title = QLabel("选择或新建一个对话")
        self.title.setObjectName("pageTitle")
        self.model_combo = QComboBox()
        self.model_combo.setAccessibleName("当前会话模型")
        self.model_combo.setMinimumHeight(44 if self._mobile else 42)
        for model in MODELS:
            self.model_combo.addItem(model.label, model.id)
        self.model_combo.currentIndexChanged.connect(self._model_selected)
        self.edit_button = QPushButton("编辑")
        self.edit_button.setAccessibleName("编辑当前会话名称、头像和角色")
        self.edit_button.setMinimumHeight(44 if self._mobile else 42)
        self.edit_button.clicked.connect(self.edit_requested)
        self.delete_button = QPushButton("删除")
        self.delete_button.setAccessibleName("删除当前会话")
        self.delete_button.setMinimumHeight(44 if self._mobile else 42)
        self.delete_button.clicked.connect(self.delete_requested)
        if self._mobile:
            title_row = QHBoxLayout()
            title_row.setContentsMargins(0, 0, 0, 0)
            title_row.setSpacing(6)
            title_row.addWidget(self.title)
            title_row.addStretch(1)
            title_row.addWidget(self.edit_button)
            title_row.addWidget(self.delete_button)
            header_layout.addLayout(title_row)
            header_layout.addWidget(self.model_combo)
        else:
            header_layout.addWidget(self.title)
            header_layout.addStretch(1)
            header_layout.addWidget(self.model_combo)
            header_layout.addWidget(self.edit_button)
            header_layout.addWidget(self.delete_button)
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        enable_touch_scrolling(self.scroll)
        self.messages = QWidget()
        self.messages_layout = QVBoxLayout(self.messages)
        self.messages_layout.setContentsMargins(0, 18, 0, 18)
        self.messages_layout.setSpacing(0)
        self.messages_layout.addStretch(1)
        self.scroll.setWidget(self.messages)
        self.scroll.viewport().installEventFilter(self)
        layout.addWidget(self.scroll, 1)

        self.composer = ChatComposer()
        self.composer.send_requested.connect(self.send_requested)
        self.composer.sticker_requested.connect(self.sticker_requested)
        self.composer.stop_requested.connect(self.stop_requested)
        layout.addWidget(self.composer)
        self.set_available(False)

    def set_model_options(
        self,
        models: tuple[ModelInfo, ...],
        selected_model: str = "",
    ) -> None:
        """Show the actual models used by the current text provider."""

        selected = selected_model or self.model_combo.currentData() or ""
        self.model_combo.blockSignals(True)
        self.model_combo.clear()
        for model in models:
            self.model_combo.addItem(model.label, model.id)
        index = self.model_combo.findData(selected)
        self.model_combo.setCurrentIndex(index if index >= 0 else 0)
        self.model_combo.blockSignals(False)
        self._model_options_editable = len(models) > 1
        self.model_combo.setToolTip(
            "选择当前会话使用的 DeepSeek 实际模型。"
            if self._model_options_editable
            else "当前模型由所选文本平台的设置决定。"
        )
        self._update_model_combo_enabled()

    @property
    def conversation_id(self) -> str | None:
        return self._conversation.id if self._conversation is not None else None

    def load(self, conversation: Conversation, turns: list[Turn]) -> None:
        self._conversation = conversation
        self.title.setText(conversation.title)
        self.model_combo.blockSignals(True)
        index = self.model_combo.findData(conversation.model)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._clear_messages()
        if conversation.opening_message:
            self._add_bubble(
                "assistant",
                conversation.opening_message,
                message_key=f"opening:{conversation.id}",
                speech_enabled=True,
            )
        for turn in turns:
            if turn.origin == "user":
                self._add_bubble(
                    "user",
                    "" if turn.user_sticker else turn.user_content,
                    image_path=turn.user_image_path,
                    sticker_id=turn.user_sticker,
                )
            elif turn.origin == "image_generation":
                self._add_bubble(
                    "user", f"生成图片：{turn.user_content}"
                )
            if turn.status == "completed":
                segments = deserialize_reply_segments(
                    turn.assistant_segments_json
                )
                if segments:
                    reasoning_pending = turn.reasoning_content
                    segment_has_image = False
                    for index, segment in enumerate(segments):
                        if segment.kind == "image":
                            if segment.image_path:
                                segment_has_image = True
                                self._add_bubble(
                                    "assistant",
                                    "",
                                    image_path=segment.image_path,
                                )
                            continue
                        self._add_bubble(
                            "assistant",
                            segment.text,
                            reasoning=reasoning_pending,
                            message_key=f"turn:{turn.id}:segment:{index}",
                            speech_enabled=segment.kind == "dialogue",
                            narration=segment.kind == "narration",
                        )
                        reasoning_pending = ""
                    if turn.assistant_image_path and not segment_has_image:
                        self._add_bubble(
                            "assistant",
                            "",
                            image_path=turn.assistant_image_path,
                        )
                else:
                    self._add_bubble(
                        "assistant",
                        turn.assistant_content,
                        reasoning=turn.reasoning_content,
                        message_key=f"turn:{turn.id}",
                        speech_enabled=True,
                        image_path=turn.assistant_image_path,
                    )
            elif turn.origin == "user":
                self._add_bubble(
                    "assistant",
                    "本次回答未完成",
                    status=turn.status,
                    error_text=_ERROR_MESSAGES.get(
                        turn.error_code,
                        "回答未完成，可以重新发送。",
                    ),
                    retry_text=turn.user_content,
                )
            elif turn.origin == "image_generation":
                self._add_bubble(
                    "assistant",
                    "图片生成未完成",
                    status=turn.status,
                    error_text=_ERROR_MESSAGES.get(
                        turn.error_code,
                        "该旧版图片任务未完成；当前版本由角色自主决定是否分享图片。",
                    ),
                    retry_enabled=False,
                )
        self.set_available(True)
        self._scroll_to_bottom()

    def add_user_message(
        self,
        text: str,
        image_path: str = "",
        sticker_id: str = "",
    ) -> None:
        self._add_bubble(
            "user",
            "" if sticker_id else text,
            image_path=image_path,
            sticker_id=sticker_id,
        )
        self._stream_bubble = self._add_bubble(
            "assistant",
            "正在理解图片…" if image_path else "",
        )
        self._scroll_to_bottom()

    def add_assistant_stream(self) -> None:
        """显示一条没有用户前置消息的角色主动回复。"""

        self._stream_bubble = self._add_bubble("assistant", "")
        self._scroll_to_bottom()

    def append_content(self, text: str) -> None:
        if self._stream_bubble is not None:
            self._stream_bubble.append_content(text)
            self._scroll_if_near_bottom()

    def append_reasoning(self, text: str) -> None:
        if self._stream_bubble is not None:
            self._stream_bubble.append_reasoning(text)

    def discard_stream(self) -> None:
        """移除等待气泡，但保持输入区锁定直到分段投递结束。"""

        if self._stream_bubble is not None:
            self.messages_layout.removeWidget(self._stream_bubble)
            self._stream_bubble.deleteLater()
            self._stream_bubble = None

    def show_typing_indicator(self) -> None:
        """在两条分段消息之间显示轻量输入状态。"""

        self.discard_stream()
        self._stream_bubble = self._add_bubble(
            "assistant",
            "对方正在输入…",
            typing=True,
        )
        self._scroll_to_bottom()

    def add_assistant_segment(
        self,
        text: str,
        *,
        message_key: str = "",
        speech_enabled: bool = False,
        narration: bool = False,
        image_path: str = "",
        reasoning: str = "",
    ) -> None:
        self._add_bubble(
            "assistant",
            text,
            message_key=message_key,
            speech_enabled=speech_enabled,
            narration=narration,
            image_path=image_path,
            reasoning=reasoning,
        )
        self._scroll_to_bottom()

    def add_image_error(self, error_code: str) -> None:
        message = _ERROR_MESSAGES.get(
            error_code,
            "图片没有发送成功，请检查当前图片平台配置后重试。",
        )
        self.add_assistant_segment(
            f"（图片发送失败：{message}）",
            narration=True,
        )

    def finish_stream(self) -> None:
        self._stream_bubble = None
        self.composer.set_generating(False)

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        self.composer.set_generating(generating)
        self._update_model_combo_enabled()
        self.edit_button.setEnabled(not generating)
        self.delete_button.setEnabled(not generating)

    def set_available(self, available: bool) -> None:
        self._available = available
        self.composer.set_available(available)
        self._update_model_combo_enabled()
        self.edit_button.setEnabled(available)
        self.delete_button.setEnabled(available)

    def _update_model_combo_enabled(self) -> None:
        self.model_combo.setEnabled(
            self._available
            and not self._generating
            and self._model_options_editable
        )

    def eventFilter(self, watched, event) -> bool:
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._update_bubble_widths()
        return super().eventFilter(watched, event)

    def _add_bubble(self, role: str, text: str, **kwargs) -> MessageBubble:
        bubble = MessageBubble(role, text, **kwargs)
        bubble.retry_requested.connect(self.retry_requested)
        bubble.speech_requested.connect(self.speech_requested)
        bubble.speech_stop_requested.connect(self.speech_stop_requested)
        message_key = kwargs.get("message_key", "")
        if message_key:
            self._speech_bubbles[message_key] = bubble
        bubble.set_chat_width(self.scroll.viewport().width())
        self.messages_layout.insertWidget(self.messages_layout.count() - 1, bubble)
        return bubble

    def set_speech_state(self, message_key: str, state: str) -> None:
        bubble = self._speech_bubbles.get(message_key)
        if bubble is not None:
            bubble.set_speech_state(state)

    def _update_bubble_widths(self) -> None:
        viewport_width = self.scroll.viewport().width()
        for index in range(self.messages_layout.count() - 1):
            widget = self.messages_layout.itemAt(index).widget()
            if isinstance(widget, MessageBubble):
                widget.set_chat_width(viewport_width)

    def _clear_messages(self) -> None:
        self.scroll.verticalScrollBar().setValue(0)
        self._speech_bubbles.clear()
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                # Keep deferred widgets out of Android's compositor while Qt
                # destroys them. Reparenting to None is intentionally avoided:
                # Qt Android promotes those children to native top-level
                # windows and can collapse replacement bubbles to 0x0.
                widget.hide()
                widget.deleteLater()
        self.messages.updateGeometry()
        self.messages.update()

    def _model_selected(self, index: int) -> None:
        model = self.model_combo.itemData(index)
        if model and self._conversation is not None:
            self.model_changed.emit(model)

    def _scroll_if_near_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() - bar.value() < 120:
            self._scroll_to_bottom()

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        bar.setValue(bar.maximum())
