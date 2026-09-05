"""聊天详情页。"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMenu,
    QPushButton,
    QScrollArea,
    QScroller,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ....model_catalog import MODELS, ModelInfo
from ...ai_features import deserialize_reply_segments
from ...data.repositories import Conversation, Turn
from ...platform import is_android_platform
from ..icons import line_icon
from ..mobile import enable_touch_scrolling
from ..widgets.avatar_widget import AvatarWidget
from ..widgets.chat_composer import ChatComposer
from ..widgets.message_bubble import MessageBubble
from ..widgets.model_selector import ModelSelector

_ERROR_MESSAGES = {
    "authentication": "API Key 无效，请前往设置检查。",
    "quota": "当前文本平台额度或计费状态不可用，请检查余额与配额。",
    "timeout": "请求超时，请稍后重试。",
    "network": "网络连接失败，请检查网络后重试。",
    "rate_limit": "请求过于频繁，请稍后重试。",
    "empty_response": "没有收到有效回答。",
    "empty_message": "消息内容为空，请重新输入。",
    "invalid_model": "当前对话模型无效，请在聊天页或设置中重新选择。",
    "text_endpoint_invalid": "文本 API 地址无效，请前往设置检查基础地址。",
    "text_model_unavailable": "当前文本模型不可用，请前往设置检查模型名。",
    "text_bad_request": "文本平台拒绝了本次请求，请检查模型与平台配置。",
    "service_error": "文本生成服务暂时不可用，请稍后重试或检查平台状态。",
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
    "image_bad_request": "图片平台拒绝了本次请求，请检查模型、尺寸和平台配置。",
    "image_service_error": "图片服务暂时不可用，请稍后重试。",
    "image_daily_limit": "今天的角色发图额度已经用完，可在图片设置中调整每日上限。",
    "image_cooldown": "距离上一次角色发图太近，本轮已停止重复生成。",
    "image_duplicate": "图片内容与近期生成请求重复，本轮已停止生成。",
    "image_boundary": "当前角色或用户边界不允许自主发送图片。",
}


class ChatPage(QWidget):
    back_requested = Signal()
    send_requested = Signal(str, str)
    sticker_requested = Signal(str)
    stop_requested = Signal()
    retry_requested = Signal(str)
    image_retry_requested = Signal(str, str)  # turn_id, image event_id
    model_changed = Signal(str)
    delete_requested = Signal()
    edit_requested = Signal()
    memory_requested = Signal()
    speech_requested = Signal(str, str)
    speech_stop_requested = Signal(str)

    # 距底部的"贴底"判定阈值：仅当几乎贴底时才自动跟随新内容，避免用户
    # 上滑阅读历史时被新消息反复拉回底部。
    _BOTTOM_THRESHOLD = 40

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
        header.setMinimumHeight(68 if self._mobile else 72)
        header_body = QVBoxLayout(header)
        header_body.setContentsMargins(
            8 if self._mobile else 20,
            8,
            8 if self._mobile else 14,
            8,
        )
        header_body.setSpacing(6)
        header_layout = QHBoxLayout()
        header_layout.setSpacing(6 if self._mobile else 8)
        header_body.addLayout(header_layout)
        self.back_button = QPushButton()
        self.back_button.setObjectName("headerBackButton")
        self.back_button.setIcon(line_icon("back"))
        self.back_button.setIconSize(QSize(24, 24))
        self.back_button.setFixedSize(44, 44)
        self.back_button.setAccessibleName("返回消息列表")
        self.back_button.setToolTip("返回消息列表")
        self.back_button.setVisible(self._mobile)
        self.back_button.clicked.connect(self.back_requested)
        if self._mobile:
            header_layout.addWidget(self.back_button)
        self.header_avatar = AvatarWidget(42 if self._mobile else 40, header)
        self.header_avatar.set_avatar("伴界")
        header_layout.addWidget(self.header_avatar)
        self.title = QLabel("选择或新建一个对话")
        self.title.setObjectName("pageTitle")
        self.title.setMinimumWidth(0)
        self.title.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        self.subtitle = QLabel("让每一次对话都有温度")
        self.subtitle.setObjectName("pageSubtitle")
        self.subtitle.setSizePolicy(
            QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred
        )
        title_block = QVBoxLayout()
        title_block.setContentsMargins(0, 0, 0, 0)
        title_block.setSpacing(1)
        title_block.addWidget(self.title)
        title_block.addWidget(self.subtitle)
        self.model_combo = ModelSelector()
        self.model_combo.setObjectName("modelSelector")
        self.model_combo.setAccessibleName("当前会话模型")
        self.model_combo.setMinimumHeight(44 if self._mobile else 42)
        if self._mobile:
            self.model_combo.setMinimumWidth(0)
            self.model_combo.setSizePolicy(
                QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed
            )
        else:
            self.model_combo.setMinimumWidth(210)
            self.model_combo.setMaximumWidth(270)
        for model in MODELS:
            self.model_combo.addItem(model.label, model.id)
        self.model_combo.currentIndexChanged.connect(self._model_selected)
        self.model_combo.currentIndexChanged.connect(self._update_model_hint)
        self.memory_action = QAction("管理角色记忆", self)
        self.memory_action.triggered.connect(
            lambda _checked=False: self.memory_requested.emit()
        )
        self.edit_action = QAction("编辑当前会话", self)
        self.edit_action.triggered.connect(
            lambda _checked=False: self.edit_requested.emit()
        )
        self.delete_action = QAction("删除当前会话", self)
        self.delete_action.triggered.connect(
            lambda _checked=False: self.delete_requested.emit()
        )
        self.more_menu = QMenu(self)
        self.more_menu.addAction(self.memory_action)
        self.more_menu.addAction(self.edit_action)
        self.more_menu.addSeparator()
        self.more_menu.addAction(self.delete_action)
        self.more_button = QPushButton()
        self.more_button.setObjectName("headerMenuButton")
        self.more_button.setIcon(line_icon("more"))
        self.more_button.setIconSize(QSize(24, 24))
        self.more_button.setFixedSize(44, 44)
        self.more_button.setAccessibleName("更多会话操作")
        self.more_button.setToolTip("更多会话操作")
        self.more_button.setMenu(self.more_menu)
        header_layout.addLayout(title_block, 1)
        if not self._mobile:
            header_layout.addWidget(self.model_combo)
        header_layout.addWidget(self.more_button)
        if self._mobile:
            header_body.addWidget(self.model_combo)
        layout.addWidget(header)

        self.scroll = QScrollArea()
        self.scroll.setObjectName("messageScroll")
        # widgetResizable 的自动 resize 在 Android 上偶尔不把内容 widget 收缩
        # 到真实内容高度，导致最下方残留可滚动的"幽灵空白"：滚动条被撑大，
        # 钉底后视口里只有空白，最新气泡被顶到视口之外。改由 _relayout_messages
        # 手动精确控制内容 widget 尺寸。
        self.scroll.setWidgetResizable(False)
        self.scroll.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        enable_touch_scrolling(self.scroll)
        self.messages = QWidget()
        self.messages_layout = QVBoxLayout(self.messages)
        self.messages_layout.setContentsMargins(0, 18, 0, 18)
        self.messages_layout.setSpacing(0)
        # 顶部伸展占位：内容不足一屏时气泡贴底排列（最新消息紧贴输入框
        # 上方），内容增长时向上扩展，最新消息始终锚定在输入框上方、下方
        # 不残留空白。
        self.messages_layout.insertStretch(0, 1)
        self.scroll.setWidget(self.messages)
        # 布局激活（气泡插入、移除、宽度变化）会更新滚动条 range；在
        # maximum 变真后若正钉在底部则补滚到真实最大，避免"旧值欠滚"
        # 导致新回复落到视口下方（QTBUG-35250，Android 上被放大）。
        self._pin_to_bottom = False
        self.scroll.verticalScrollBar().rangeChanged.connect(
            self._on_scroll_range_changed
        )
        # 延迟到下次事件循环迭代再滚到底部：打开/切换会话时页面可能尚未
        # 完成布局激活，直接滚动会停在旧最大值（QTBUG-35250），延迟后
        # 布局已生效，滚动位置准确。
        self._latest_timer = QTimer(self)
        self._latest_timer.setSingleShot(True)
        self._latest_timer.timeout.connect(self._scroll_to_bottom)
        self.scroll.verticalScrollBar().valueChanged.connect(
            self._on_scroll_value_changed
        )
        if self._mobile:
            # 手势/惯性结束后补判：不打断用户的甩动，等动画停下再决定
            # 补滚到底部或浮出未读按钮。
            QScroller.scroller(self.scroll.viewport()).stateChanged.connect(
                self._on_scroller_state_changed
            )
        self.scroll.viewport().installEventFilter(self)
        # 收到新消息而用户上滑阅读历史时，浮出"最新消息"按钮用于跳回底部。
        self._new_message_count = 0
        self.new_message_button = QPushButton("最新消息")
        self.new_message_button.setObjectName("newMessageButton")
        self.new_message_button.setAccessibleName("滚动到最新消息")
        self.new_message_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.new_message_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.new_message_button.setParent(self.scroll.viewport())
        self.new_message_button.hide()
        self.new_message_button.clicked.connect(self._jump_to_latest)
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
        self._update_model_hint()
        self._update_model_combo_enabled()

    def _update_model_hint(self, *_args) -> None:
        hint = (
            "选择当前会话使用的模型。"
            if self._model_options_editable
            else "当前模型由所选文本平台的设置决定。"
        )
        self.model_combo.setToolTip(
            f"{self.model_combo.currentText()}\n{hint}"
        )
        self.model_combo.setAccessibleDescription(self.model_combo.currentText())

    @property
    def conversation_id(self) -> str | None:
        return self._conversation.id if self._conversation is not None else None

    def load(
        self,
        conversation: Conversation,
        turns: list[Turn],
        *,
        defer_opening: bool = False,
    ) -> None:
        """加载会话；defer_opening=True 时暂不显示预设开场白模板。

        新建角色会话改由 AI 主动生成开场白；在请求进行中先不显示角色卡
        first_mes 模板，避免与 AI 生成的首条消息重复。
        """

        self._conversation = conversation
        self.title.setText(conversation.display_name)
        self.title.setToolTip(conversation.display_name)
        self.subtitle.setText(
            conversation.title
            if conversation.title != conversation.display_name
            else "AI 角色对话"
        )
        self.header_avatar.set_avatar(
            conversation.display_name,
            conversation.effective_avatar_path,
        )
        self.model_combo.blockSignals(True)
        index = self.model_combo.findData(conversation.model)
        if index >= 0:
            self.model_combo.setCurrentIndex(index)
        self.model_combo.blockSignals(False)
        self._update_model_hint()
        self._clear_messages()
        if conversation.opening_message and not defer_opening:
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
                            elif segment.status in {"failed", "cancelled"}:
                                message = _ERROR_MESSAGES.get(
                                    segment.error_code,
                                    "图片没有发送成功，请检查图片平台后重试。",
                                )
                                self._add_bubble(
                                    "assistant",
                                    "图片发送未完成",
                                    status="failed",
                                    error_text=message,
                                    retry_text="重试图片",
                                    image_retry=(turn.id, segment.event_id),
                                )
                            elif segment.status == "pending":
                                self._add_bubble(
                                    "assistant",
                                    "图片正在生成…",
                                    typing=True,
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
        self.scroll_to_latest()

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
        self._on_new_content()

    def discard_stream(self) -> None:
        """移除等待气泡，但保持输入区锁定直到分段投递结束。"""

        if self._stream_bubble is not None:
            self.messages_layout.removeWidget(self._stream_bubble)
            # 与 _clear_messages 一致：先在 Android 组合器外隐藏再销毁，
            # 避免被移除的气泡在销毁前短暂上浮为原生顶层窗口并干扰几何。
            self._stream_bubble.hide()
            self._stream_bubble.deleteLater()
            self._stream_bubble = None
            self._relayout_messages()

    def show_typing_indicator(self) -> None:
        """在两条分段消息之间显示轻量输入状态。"""

        self.discard_stream()
        self._stream_bubble = self._add_bubble(
            "assistant",
            "对方正在输入…",
            typing=True,
        )
        self._on_new_content()

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
        self._on_new_content()

    def add_image_error(self, error_code: str) -> None:
        message = _ERROR_MESSAGES.get(
            error_code,
            "图片没有发送成功，请检查当前图片平台配置后重试。",
        )
        self.add_assistant_segment(
            f"（图片发送失败：{message}）",
            narration=True,
        )

    def add_image_event(self, *, image_path: str = "", pending: bool = False) -> None:
        if image_path:
            self.add_assistant_segment("", image_path=image_path)
        elif pending:
            self.add_assistant_segment("图片正在生成…")

    def add_image_analysis_error(self, error_code: str) -> None:
        message = _ERROR_MESSAGES.get(
            error_code,
            "暂时无法读取这张图片，角色仍会根据随附文字继续回复。",
        )
        self.add_assistant_segment(
            f"（图片理解失败：{message}）",
            narration=True,
        )

    def finish_stream(self) -> None:
        self._stream_bubble = None
        self.composer.set_generating(False)

    def set_generating(self, generating: bool) -> None:
        self._generating = generating
        self.composer.set_generating(generating)
        self._update_model_combo_enabled()
        self.edit_action.setEnabled(not generating and self._available)
        self.delete_action.setEnabled(not generating and self._available)

    def set_available(self, available: bool) -> None:
        self._available = available
        self.composer.set_available(available)
        self._update_model_combo_enabled()
        self.more_button.setEnabled(available)
        self.edit_action.setEnabled(available and not self._generating)
        self.memory_action.setEnabled(available)
        self.delete_action.setEnabled(available and not self._generating)

    def _update_model_combo_enabled(self) -> None:
        self.model_combo.setEnabled(
            self._available
            and not self._generating
            and self._model_options_editable
        )

    def eventFilter(self, watched, event) -> bool:
        if watched is self.scroll.viewport() and event.type() == QEvent.Type.Resize:
            self._relayout_messages()
            self._position_new_message_button()
        return super().eventFilter(watched, event)

    def _add_bubble(self, role: str, text: str, **kwargs) -> MessageBubble:
        image_retry = kwargs.pop("image_retry", None)
        if "sender_name" not in kwargs:
            kwargs["sender_name"] = (
                "我"
                if role == "user"
                else (
                    self._conversation.display_name
                    if self._conversation is not None
                    else "伴界角色"
                )
            )
        if (
            role != "user"
            and "avatar_path" not in kwargs
            and self._conversation is not None
        ):
            kwargs["avatar_path"] = self._conversation.effective_avatar_path
        bubble = MessageBubble(role, text, **kwargs)
        if image_retry:
            turn_id, event_id = image_retry
            bubble.retry_requested.connect(
                lambda _text, turn=turn_id, event=event_id: (
                    self.image_retry_requested.emit(turn, event)
                )
            )
        else:
            bubble.retry_requested.connect(self.retry_requested)
        bubble.speech_requested.connect(self.speech_requested)
        bubble.speech_stop_requested.connect(self.speech_stop_requested)
        message_key = kwargs.get("message_key", "")
        if message_key:
            self._speech_bubbles[message_key] = bubble
        bubble.set_chat_width(self.scroll.viewport().width())
        # 顶部伸展占位固定在 index 0，新气泡追加到列表末尾。
        self.messages_layout.addWidget(bubble)
        return bubble

    def set_speech_state(self, message_key: str, state: str) -> None:
        bubble = self._speech_bubbles.get(message_key)
        if bubble is not None:
            bubble.set_speech_state(state)

    def _relayout_messages(self) -> None:
        """确定性刷新内容 widget 尺寸与气泡宽度。

        - 宽度跟随视口；仅宽度真正变化时重排气泡，避免 Android 软键盘/
          insets 变化触发 resize 风暴导致换行抖动。
        - 高度精确等于真实内容高度（至少视口高度）。内容溢出时不再残留
          "幽灵空白"，滚动条 maximum 即真实内容底部，钉底就能看到最新
          气泡；内容不足一屏时消息贴底排列、最新消息紧贴输入框上方。
        """

        viewport = self.scroll.viewport()
        width = viewport.width()
        height = viewport.height()
        if width != self.messages.width():
            self.messages.setFixedWidth(width)
        if width != getattr(self, "_last_viewport_width", -1):
            self._last_viewport_width = width
            for index in range(self.messages_layout.count()):
                widget = self.messages_layout.itemAt(index).widget()
                if isinstance(widget, MessageBubble):
                    widget.set_chat_width(width)
        target = max(self._content_height(), height)
        if self.messages.height() != target:
            self.messages.setFixedHeight(target)

    def _content_height(self) -> int:
        """真实内容高度：各气泡在自身宽度下自动换行后的高度求和（含边距）。

        QLabel 对自动换行文本的 sizeHint 高度按单行低估（布局未激活前返回
        极小值），须用 heightForWidth 才能得到真实排布高度；否则内容 widget
        高度不足、最新消息被裁剪。
        """

        total = 0
        for index in range(self.messages_layout.count()):
            widget = self.messages_layout.itemAt(index).widget()
            if isinstance(widget, MessageBubble):
                width = widget.chat_bubble_width
                height = (
                    widget.heightForWidth(width) if width > 0 else -1
                )
                total += height if height > 0 else widget.sizeHint().height()
        margins = self.messages_layout.contentsMargins()
        return total + margins.top() + margins.bottom()

    def _content_bottom(self) -> int:
        """真实内容底部对应的滚动位置，不依赖可能滞后的 widget 高度。

        与 bar.maximum() 不同，这里直接按气泡 heightForWidth 计算，布局
        事件尚未应用到滚动条 range 时结果仍然正确。
        """

        return max(0, self._content_height() - self.scroll.viewport().height())

    def _clear_messages(self) -> None:
        self.scroll.verticalScrollBar().setValue(0)
        self._speech_bubbles.clear()
        self._hide_new_message_button()
        # 顶部伸展占位保留在 index 0，仅移除其后追加的气泡。
        while self.messages_layout.count() > 1:
            item = self.messages_layout.takeAt(1)
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
        self._relayout_messages()

    def _model_selected(self, index: int) -> None:
        model = self.model_combo.itemData(index)
        if model and self._conversation is not None:
            self.model_changed.emit(model)

    def scroll_to_latest(self) -> None:
        """延迟到下次事件循环迭代滚动到最新消息（打开/切换会话时调用）。

        页面显示后布局激活是异步的；直接滚动会基于尚未生效的旧最大值而
        停在错误位置（QTBUG-35250）。延迟一拍后布局已生效，能准确钉在
        最新消息处。重复调用只重启定时器，不会重复滚动。
        """

        self._latest_timer.start(0)

    def _scroll_to_bottom(self) -> None:
        bar = self.scroll.verticalScrollBar()
        if self._mobile:
            # 停掉可能仍在进行的 QScroller 惯性动画，避免其覆盖 setValue。
            scroller = QScroller.scroller(self.scroll.viewport())
            if (
                scroller is not None
                and scroller.state() != QScroller.State.Inactive
            ):
                scroller.stop()
        self._relayout_messages()
        # 直接按真实内容底部滚动，不依赖可能滞后的 bar.maximum()
        # （QTBUG-35250：布局激活前 maximum 过时，会把最新气泡顶到
        # 视口之外）；同时记录贴底意图，等 rangeChanged 补滚兜底。
        # 此方法只由明确需要回到底部的路径调用（打开会话、用户发送、
        # 点击“最新消息”或原本已贴底时收到新内容），因此持续保留钉底
        # 意图。头像、姓名和 Markdown 可能让布局分数次更新 range；直到
        # 用户主动上滑前，每次 rangeChanged 都应继续跟到新的 maximum。
        self._pin_to_bottom = True
        bar.setValue(self._content_bottom())

    def _on_scroll_range_changed(self, _minimum: int, maximum: int) -> None:
        """布局激活、滚动条 range 更新后，若仍钉在底部则补滚到真实内容底部。

        钉底标志由 _scroll_to_bottom 在用户贴底时置位；此时旧 maximum
        可能把 setValue 夹取到欠滚位置，这里直接按真实内容底部重滚，
        消除 QTBUG-35250 的欠滚（Android 上被放大）。
        """

        if not self._pin_to_bottom:
            return
        bar = self.scroll.verticalScrollBar()
        bar.setValue(maximum)

    def _message_bubbles(self) -> list[MessageBubble]:
        """当前可见的消息气泡（按时间顺序，不含顶部伸展占位）。"""

        return [
            self.messages_layout.itemAt(index).widget()
            for index in range(self.messages_layout.count())
            if isinstance(
                self.messages_layout.itemAt(index).widget(), MessageBubble
            )
        ]

    def _on_new_content(self) -> None:
        """新内容加入后的滚动决策。

        用户贴底时跟随滚动到底部；用户正在拖动/惯性滚动时不打断手势
        （否则会截断甩动并反复拉回底部）；用户上滑阅读时浮出"最新消息"
        按钮，点击后跳回最新消息处。
        """

        bar = self.scroll.verticalScrollBar()
        if self._scroller_active():
            # 手势或惯性进行中：不强拉视图、不打断手势，只重排内容高度
            # 让新内容真实可滚动到达，并计入未读；等手势结束（Inactive）
            # 后由 stateChanged 决定补滚到底部或浮出未读按钮。
            self._relayout_messages()
            self._new_message_count += 1
            return
        if bar.maximum() - bar.value() <= self._BOTTOM_THRESHOLD:
            self._scroll_to_bottom()
        else:
            # 用户上滑阅读时不强拉视图；但仍需重排内容高度，让新消息
            # 真实可滚动到达，滚动范围随内容增长。同时解除可能残留的钉底
            # 意图，避免随后的 rangeChanged 把视图拉回底部。
            self._pin_to_bottom = False
            self._relayout_messages()
            self._new_message_count += 1
            self._show_new_message_button()

    def _on_scroll_value_changed(self, value: int) -> None:
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() - value <= self._BOTTOM_THRESHOLD:
            self._hide_new_message_button()
        else:
            # 用户上滑离开底部：解除钉底意图，避免随后的无关 rangeChanged
            # 消费到过期 pin 而把视图拉回底部。
            self._pin_to_bottom = False

    def _scroller_active(self) -> bool:
        """QScroller 是否处于拖动/惯性/回弹等非静止状态。"""

        if not self._mobile:
            return False
        scroller = QScroller.scroller(self.scroll.viewport())
        return (
            scroller is not None
            and scroller.state() != QScroller.State.Inactive
        )

    def _on_scroller_state_changed(self, _state: object) -> None:
        """手势/惯性结束（回到 Inactive）后补判。

        手势期间到达的新内容可能使视图不再贴底；这里在动画停下后补滚到
        最新或浮出未读按钮，避免"甩动中被截断拉回底部"的生涩与反复。
        """

        if not self._mobile or self._scroller_active():
            return
        bar = self.scroll.verticalScrollBar()
        if bar.maximum() - bar.value() <= self._BOTTOM_THRESHOLD:
            self._scroll_to_bottom()
        elif self._new_message_count > 0:
            self._show_new_message_button()

    def _jump_to_latest(self) -> None:
        """点击"最新消息"按钮：回到最新消息处并收起按钮。"""

        self._hide_new_message_button()
        self._scroll_to_bottom()

    def _show_new_message_button(self) -> None:
        if self._new_message_count > 1:
            self.new_message_button.setText(
                f"最新消息 · {self._new_message_count}"
            )
        else:
            self.new_message_button.setText("最新消息")
        self._position_new_message_button()
        self.new_message_button.show()
        self.new_message_button.raise_()

    def _hide_new_message_button(self) -> None:
        self._new_message_count = 0
        self.new_message_button.hide()

    def _position_new_message_button(self) -> None:
        viewport = self.scroll.viewport()
        size = self.new_message_button.sizeHint()
        x = max(8, viewport.width() - size.width() - 12)
        y = max(8, viewport.height() - size.height() - 12)
        self.new_message_button.move(x, y)
