"""聊天消息气泡。"""

from __future__ import annotations

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QFont, QPixmap
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ...platform import is_android_platform
from ...stickers import sticker_by_id


class MessageBubble(QWidget):
    retry_requested = Signal(str)
    speech_requested = Signal(str, str)
    speech_stop_requested = Signal(str)

    def __init__(
        self,
        role: str,
        text: str,
        *,
        reasoning: str = "",
        status: str = "completed",
        error_text: str = "",
        retry_text: str = "",
        message_key: str = "",
        speech_enabled: bool = False,
        image_path: str = "",
        sticker_id: str = "",
        retry_enabled: bool = True,
        narration: bool = False,
        typing: bool = False,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._role = role
        self._text = text
        self._reasoning_text = reasoning
        self._message_key = message_key
        self._image_path = image_path
        self._sticker = sticker_by_id(sticker_id)
        self._image_preview_width = 0
        self._speech_state = "idle"
        self._chat_width = 0
        self._fixed_width = 0
        self._mobile = is_android_platform()
        self._source_pixmap = QPixmap()

        row = QHBoxLayout(self)
        row.setContentsMargins(
            12 if self._mobile else 20,
            6,
            12 if self._mobile else 20,
            6,
        )
        row.setSpacing(10)
        if role == "user":
            row.addStretch(1)

        self.bubble = QFrame()
        self.bubble.setObjectName(
            "errorBubble" if status in {"failed", "cancelled", "interrupted"}
            else ("userBubble" if role == "user" else "assistantBubble")
        )
        self.bubble.setMaximumWidth(680)
        self.bubble.setSizePolicy(
            QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum
        )
        body = QVBoxLayout(self.bubble)
        body.setContentsMargins(12, 10, 12, 10)
        body.setSpacing(6)

        if reasoning:
            self._reasoning_button = QPushButton("查看思考过程")
            self._reasoning_button.setCheckable(True)
            self._reasoning_button.setAccessibleName("展开或收起思考过程")
            self._reasoning_button.toggled.connect(self._toggle_reasoning)
            body.addWidget(self._reasoning_button)
            self._reasoning_label = QLabel(reasoning)
            self._reasoning_label.setWordWrap(True)
            self._reasoning_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
                if self._mobile
                else Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._reasoning_label.setVisible(False)
            self._reasoning_label.setProperty("muted", True)
            body.addWidget(self._reasoning_label)
        else:
            self._reasoning_button = None
            self._reasoning_label = None

        self.sticker_label = None
        if self._sticker is not None:
            self.sticker_label = QLabel(self._sticker.emoji)
            self.sticker_label.setObjectName("messageSticker")
            self.sticker_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            sticker_font = QFont("Segoe UI Emoji")
            sticker_font.setPointSize(38)
            self.sticker_label.setFont(sticker_font)
            self.sticker_label.setAccessibleName(
                f"表情包：{self._sticker.label}"
            )
            self.sticker_label.setToolTip(self._sticker.label)
            self.sticker_label.setMinimumSize(76, 68)
            body.addWidget(self.sticker_label)

        self.image_label = None
        if image_path:
            self.image_label = QLabel()
            self.image_label.setObjectName("messageImage")
            self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            pixmap = QPixmap(image_path)
            if pixmap.isNull():
                self.image_label.setText("图片文件不可用")
                self.image_label.setProperty("muted", True)
            else:
                self._source_pixmap = pixmap
                preview = pixmap.scaled(
                    420,
                    420,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._image_preview_width = preview.width()
                self.image_label.setPixmap(preview)
                self.image_label.setAccessibleName("聊天图片预览")
            body.addWidget(self.image_label)
            open_image = QPushButton("打开原图")
            open_image.setAccessibleName("使用系统应用打开原图")
            open_image.clicked.connect(self._open_image)
            body.addWidget(open_image, alignment=Qt.AlignmentFlag.AlignLeft)

        fallback = "图片" if image_path else (
            "" if self._sticker is not None else "正在生成…"
        )
        self.text_label = QLabel(text or fallback)
        self.text_label.setObjectName("messageText")
        self.text_label.setWordWrap(True)
        self.text_label.setMinimumWidth(0)
        self.text_label.setSizePolicy(
            (
                QSizePolicy.Policy.Ignored
                if self._mobile
                else QSizePolicy.Policy.Preferred
            ),
            QSizePolicy.Policy.Preferred,
        )
        self.text_label.setTextFormat(
            Qt.TextFormat.MarkdownText
            if role == "assistant"
            else Qt.TextFormat.PlainText
        )
        self.text_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.NoTextInteraction
            if self._mobile
            else Qt.TextInteractionFlag.TextSelectableByMouse
        )
        if narration or typing:
            self.text_label.setProperty("muted", True)
            self.text_label.setAccessibleName(
                "对方正在输入" if typing else "旁白"
            )
        body.addWidget(self.text_label)
        if self._sticker is not None and not text.strip():
            self.text_label.hide()

        if error_text:
            error = QLabel(error_text)
            error.setProperty("muted", True)
            error.setWordWrap(True)
            body.addWidget(error)
        if (
            status in {"failed", "cancelled", "interrupted"}
            and role == "assistant"
            and retry_enabled
        ):
            retry = QPushButton("重试")
            retry.setAccessibleName("重新发送这条消息")
            retry.clicked.connect(
                lambda: self.retry_requested.emit(retry_text or self._text)
            )
            body.addWidget(retry, alignment=Qt.AlignmentFlag.AlignLeft)

        self.speech_button = None
        if (
            role == "assistant"
            and status == "completed"
            and bool(text.strip())
            and speech_enabled
            and message_key
        ):
            self.speech_button = QPushButton("播放")
            self.speech_button.setMinimumHeight(44)
            self.speech_button.setAccessibleName("播放这条 AI 回复")
            self.speech_button.clicked.connect(self._speech_action)
            body.addWidget(
                self.speech_button, alignment=Qt.AlignmentFlag.AlignLeft
            )

        row.addWidget(self.bubble)
        if role != "user":
            row.addStretch(1)

    @property
    def chat_bubble_width(self) -> int:
        """气泡实际宽度；用于按宽度计算自动换行后的真实高度。"""

        return self._fixed_width or self.bubble.width() or 0

    def set_chat_width(self, viewport_width: int) -> None:
        """根据聊天视口分配稳定且可读的气泡宽度。"""

        self._chat_width = max(0, viewport_width)
        if self._mobile:
            available = max(120, viewport_width - 24)
            if self._role == "user":
                maximum = max(120, int(available * 0.82))
                lines = (
                    self._text
                    or (
                        "表情"
                        if self._sticker is not None
                        else "正在生成…"
                    )
                ).splitlines() or [""]
                natural = max(
                    self.text_label.fontMetrics().horizontalAdvance(line)
                    for line in lines
                ) + 32
                if self._sticker is not None:
                    natural = max(natural, 112)
                width = min(maximum, max(120, natural))
            else:
                width = max(160, int(available * 0.90))
                width = min(available, width)
            self.bubble.setFixedWidth(width)
            self._fixed_width = width
            text_width = max(0, width - 24)
            self.text_label.setMinimumWidth(0)
            self.text_label.setMaximumWidth(text_width)
            if self.image_label is not None and not self._source_pixmap.isNull():
                preview = self._source_pixmap.scaled(
                    min(420, text_width),
                    min(420, text_width),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                self._image_preview_width = preview.width()
                self.image_label.setPixmap(preview)
            self.text_label.updateGeometry()
            self.bubble.updateGeometry()
            return

        available = max(320, viewport_width - 40)
        if self._role == "user":
            maximum = max(220, available // 2)
            lines = (
                self._text
                or ("表情" if self._sticker is not None else "正在生成…")
            ).splitlines() or [""]
            natural = max(
                self.text_label.fontMetrics().horizontalAdvance(line)
                for line in lines
            ) + 32
            if self._sticker is not None:
                natural = max(natural, 112)
            if self._image_preview_width:
                natural = max(natural, self._image_preview_width + 24)
            width = min(maximum, max(160, natural))
            self.bubble.setFixedWidth(width)
            self._fixed_width = width
            self.text_label.setMinimumWidth(max(136, width - 24))
            self.text_label.setMaximumWidth(max(136, width - 24))
        else:
            width = min(900, max(420, int(available * 0.76)))
            self.bubble.setFixedWidth(width)
            self._fixed_width = width
            self.text_label.setMinimumWidth(max(0, width - 24))
            self.text_label.setMaximumWidth(max(0, width - 24))
        self.text_label.updateGeometry()
        self.bubble.updateGeometry()

    def append_content(self, text: str) -> None:
        if self.text_label.text() in {
            "正在生成…",
            "正在理解图片…",
            "正在生成图片…",
        }:
            self.text_label.clear()
        self._text += text
        self.text_label.setText(self._text)
        if self._chat_width:
            self.set_chat_width(self._chat_width)

    def append_reasoning(self, text: str) -> None:
        self._reasoning_text += text
        if self._reasoning_label is None:
            layout = self.findChild(QFrame).layout()
            self._reasoning_button = QPushButton("查看思考过程")
            self._reasoning_button.setCheckable(True)
            self._reasoning_button.setAccessibleName("展开或收起思考过程")
            self._reasoning_button.toggled.connect(self._toggle_reasoning)
            layout.insertWidget(0, self._reasoning_button)
            self._reasoning_label = QLabel()
            self._reasoning_label.setWordWrap(True)
            self._reasoning_label.setTextInteractionFlags(
                Qt.TextInteractionFlag.NoTextInteraction
                if self._mobile
                else Qt.TextInteractionFlag.TextSelectableByMouse
            )
            self._reasoning_label.setVisible(False)
            self._reasoning_label.setProperty("muted", True)
            layout.insertWidget(1, self._reasoning_label)
        self._reasoning_label.setText(self._reasoning_text)

    def set_speech_state(self, state: str) -> None:
        self._speech_state = state
        if self.speech_button is None:
            return
        labels = {
            "idle": ("播放", "播放这条 AI 回复"),
            "synthesizing": ("停止", "停止生成语音"),
            "playing": ("停止", "停止朗读"),
            "finished": ("重播", "重新朗读这条 AI 回复"),
            "error": ("重试播放", "重试生成并播放语音"),
        }
        text, accessible = labels.get(state, labels["idle"])
        self.speech_button.setText(text)
        self.speech_button.setAccessibleName(accessible)
        self.speech_button.setToolTip(accessible)

    def _speech_action(self) -> None:
        if self._speech_state in {"synthesizing", "playing"}:
            self.speech_stop_requested.emit(self._message_key)
        else:
            self.speech_requested.emit(self._message_key, self._text)

    def _toggle_reasoning(self, expanded: bool) -> None:
        if self._reasoning_label is None or self._reasoning_button is None:
            return
        self._reasoning_label.setVisible(expanded)
        self._reasoning_button.setText(
            "收起思考过程" if expanded else "查看思考过程"
        )

    def _open_image(self) -> None:
        if self._image_path:
            QDesktopServices.openUrl(QUrl.fromLocalFile(self._image_path))
