"""微信式三栏主窗口及聊天协调逻辑。"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QThread, QTimer, Qt
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ...anthropic_gateway import DeepSeekHttpGateway
from ...branding import PRODUCT_NAME, PRODUCT_SHORT_NAME
from ...chat_service import ChatStreamService
from ...character_prompt import build_character_prompt
from ...model_catalog import MODEL_CHAT, text_provider_models
from ...grsai_gateway import (
    DEFAULT_GRSAI_API_BASE_URL,
    DEFAULT_GRSAI_TEXT_MODEL,
    GrsAiGateway,
)
from ...tts import TtsProfile, read_tts_profile
from ..ai_features import (
    AUTONOMOUS_IMAGE_SYSTEM_PROMPT,
    PROACTIVE_SYSTEM_SUFFIX,
    ROLE_MEMORY_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    AutonomousImageDecision,
    ProactiveMessageScheduler,
    ReplyPlan,
    ReplySegment,
    autonomous_image_request,
    classify_role_reply,
    clean_ai_summary,
    enrich_role_image_prompt,
    explicit_image_request_prompt,
    parse_autonomous_image_decision,
    parse_role_postprocess,
    proactive_request,
    role_memory_request,
    serialize_reply_segments,
    summary_request,
)
from ..assets import AvatarError, import_chat_image
from ..builtin_characters import BuiltinCharacterManager
from ..data.repositories import (
    CharacterRepository,
    ChatRepository,
    SettingsRepository,
)
from ..security.credentials import CredentialStore
from ..image_service import (
    DEFAULT_SILICONFLOW_IMAGE_MODEL,
    DEFAULT_SILICONFLOW_IMAGE_SIZE,
    DEFAULT_SILICONFLOW_VISION_MODEL,
    DEFAULT_GRSAI_IMAGE_MODEL,
    DEFAULT_GRSAI_IMAGE_SIZE,
    DEFAULT_GRSAI_VISION_MODEL,
    GrsAiImageService,
    SiliconFlowImageService,
)
from ..platform import is_android_platform
from ..stickers import sticker_by_id
from ..theme import stylesheet
from ..workers import ChatWorker, ImageGenerationWorker
from .conversation_edit_dialog import ConversationEditDialog
from .pages.characters_page import CharactersPage
from .pages.chat_page import ChatPage
from .pages.conversations_page import ConversationsPage
from .pages.settings_page import SettingsPage

if TYPE_CHECKING:
    from ..notification_sound import NotificationSound
    from ..tts import SpeechController


@dataclass(frozen=True, slots=True)
class _AutonomousImageJob:
    conversation_id: str
    turn_id: str
    decision_request: str = ""
    prompt: str = ""
    segment_index: int | None = None


@dataclass(frozen=True, slots=True)
class _SummaryJob:
    conversation_id: str
    request_text: str
    system_prompt: str
    updates_role_state: bool = False


@dataclass(frozen=True, slots=True)
class _ReplyDelivery:
    conversation_id: str
    turn_id: str
    plan: ReplyPlan
    profile: TtsProfile
    reasoning: str
    request_kind: str


class MainWindow(QMainWindow):
    def __init__(
        self,
        chats: ChatRepository,
        characters: CharacterRepository,
        settings: SettingsRepository,
        credentials: CredentialStore,
        gateway_factory: Callable[[str], object] | None = None,
        builtins: BuiltinCharacterManager | None = None,
        speech: SpeechController | None = None,
        image_service_factory: Callable[..., object] | None = None,
        notification_sound: NotificationSound | None = None,
        media_root: str | Path | None = None,
    ) -> None:
        super().__init__()
        self._chats = chats
        self._characters = characters
        self._settings = settings
        self._credentials = credentials
        self._gateway_factory = gateway_factory
        self._image_service_factory = image_service_factory
        self._media_root = media_root
        self._speech: SpeechController | None = None
        self._notification_sound: NotificationSound | None = None
        self._shutting_down = False
        self._notification_pending = False
        self._pending_delivery: _ReplyDelivery | None = None
        self._delivery: _ReplyDelivery | None = None
        self._delivery_segments: deque[tuple[int, ReplySegment]] = deque()
        self._delivery_reasoning = ""
        self._delivery_speech_started = False
        self._delivery_timer = QTimer(self)
        self._delivery_timer.setSingleShot(True)
        self._delivery_timer.timeout.connect(self._deliver_next_segment)
        self._conversation_id: str | None = None
        self._turn_id: str | None = None
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._request_conversation_id: str | None = None
        self._request_kind = "user"
        self._summary_queue: deque[_SummaryJob] = deque()
        self._summary_thread: QThread | None = None
        self._summary_worker: ChatWorker | None = None
        self._summary_job: _SummaryJob | None = None
        self._image_queue: deque[_AutonomousImageJob] = deque()
        self._image_thread: QThread | None = None
        self._image_worker: ChatWorker | ImageGenerationWorker | None = None
        self._image_job: _AutonomousImageJob | None = None
        self._image_decision = AutonomousImageDecision()
        self._answer = ""
        self._reasoning = ""
        self._proactive = ProactiveMessageScheduler(settings, self)
        self._mobile = is_android_platform()
        self._mobile_body: QStackedWidget | None = None

        self.setWindowTitle(PRODUCT_NAME)
        if self._mobile:
            self.resize(420, 800)
            self.setMinimumSize(320, 480)
        else:
            self.resize(1160, 760)
            self.setMinimumSize(900, 620)

        root = QWidget()
        root.setObjectName("root")
        root_layout = (
            QVBoxLayout(root) if self._mobile else QHBoxLayout(root)
        )
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        nav = QWidget()
        nav.setObjectName("navBar")
        if self._mobile:
            nav.setFixedHeight(68)
            nav_layout = QHBoxLayout(nav)
            nav_layout.setContentsMargins(8, 6, 8, 8)
            nav_layout.setSpacing(6)
        else:
            nav.setFixedWidth(72)
            nav_layout = QVBoxLayout(nav)
            nav_layout.setContentsMargins(8, 16, 8, 16)
            nav_layout.setSpacing(10)
        brand = QLabel()
        brand.setObjectName("brand")
        brand.setAlignment(Qt.AlignmentFlag.AlignCenter)
        brand.setAccessibleName(PRODUCT_NAME)
        brand.setFixedHeight(48)
        app_icon = QApplication.windowIcon()
        if not app_icon.isNull():
            brand.setPixmap(app_icon.pixmap(44, 44))
        else:
            brand.setText(PRODUCT_SHORT_NAME)
        if not self._mobile:
            nav_layout.addWidget(brand)

        self.message_nav = QPushButton("消息")
        self.character_nav = QPushButton("角色")
        self.settings_nav = QPushButton("设置")
        for button in (
            self.message_nav,
            self.character_nav,
            self.settings_nav,
        ):
            button.setObjectName("navButton")
            button.setCheckable(True)
            if self._mobile:
                button.setMinimumHeight(48)
                nav_layout.addWidget(button, 1)
            else:
                button.setMinimumSize(52, 52)
                nav_layout.addWidget(button)
        if not self._mobile:
            nav_layout.addStretch(1)
        group = QButtonGroup(self)
        group.setExclusive(True)
        group.addButton(self.message_nav)
        group.addButton(self.character_nav)
        group.addButton(self.settings_nav)
        self.message_nav.setChecked(True)

        self.conversations = ConversationsPage(chats)
        self.content = QStackedWidget()
        self.chat_page = ChatPage()
        self.characters_page = CharactersPage(characters, builtins=builtins)
        self.settings_page = SettingsPage(settings, credentials)
        self._refresh_text_model_controls()
        self.content.addWidget(self.chat_page)
        self.content.addWidget(self.characters_page)
        self.content.addWidget(self.settings_page)
        if self._mobile:
            self.conversations.setMinimumWidth(0)
            self.conversations.setMaximumWidth(16_777_215)
            self._mobile_body = QStackedWidget()
            self._mobile_body.addWidget(self.conversations)
            self._mobile_body.addWidget(self.content)
            root_layout.addWidget(self._mobile_body, 1)
            root_layout.addWidget(nav)
        else:
            root_layout.addWidget(nav)
            root_layout.addWidget(self.conversations)
            root_layout.addWidget(self.content, 1)
        self.setCentralWidget(root)

        self.message_nav.clicked.connect(self._show_messages)
        self.character_nav.clicked.connect(self._show_characters)
        self.settings_nav.clicked.connect(self._show_settings)
        self.conversations.new_requested.connect(self._new_conversation)
        self.conversations.conversation_selected.connect(self._open_conversation)
        if self._mobile:
            self.conversations.list.itemClicked.connect(
                self._open_current_mobile_conversation
            )
        self.conversations.edit_requested.connect(self._edit_conversation)
        self.characters_page.start_chat_requested.connect(
            self._new_character_conversation
        )
        self.characters_page.changed.connect(self._characters_changed)
        self.chat_page.send_requested.connect(self._send)
        self.chat_page.sticker_requested.connect(self._send_sticker)
        self.chat_page.stop_requested.connect(self._stop)
        self.chat_page.retry_requested.connect(self._send)
        self.chat_page.model_changed.connect(self._change_model)
        self.chat_page.edit_requested.connect(self._edit_current)
        self.chat_page.delete_requested.connect(self._delete_current)
        self.chat_page.speech_requested.connect(self._play_message)
        self.chat_page.speech_stop_requested.connect(self._stop_speech)
        self.settings_page.theme_changed.connect(self._apply_theme)
        self.settings_page.data_clear_requested.connect(self._clear_all)
        self.settings_page.credentials_changed.connect(self._credentials_updated)
        self.settings_page.text_settings_changed.connect(
            self._refresh_text_model_controls
        )
        self.settings_page.proactive_settings_changed.connect(
            self._proactive.reload
        )
        self.settings_page.notification_sound_preview_requested.connect(
            lambda: self._play_notification(force=True)
        )
        self._proactive.due.connect(self._send_proactive_message)
        self.set_audio_services(
            speech=speech,
            notification_sound=notification_sound,
        )

        self._apply_theme(self._settings.get("theme", "system"))
        self._chats.recover_interrupted()
        self.conversations.refresh()
        conversations = self._chats.list_conversations()
        if conversations:
            self.conversations.select(conversations[0].id)
        elif self._text_api_key():
            self._new_conversation()
        else:
            self._show_settings()
        self._proactive.start()
        self._enqueue_pending_summaries()

    def set_audio_services(
        self,
        *,
        speech: SpeechController | None = None,
        notification_sound: NotificationSound | None = None,
    ) -> None:
        """Attach optional multimedia services after the first window is visible.

        Android multimedia backends can fail independently of the chat UI.  Keeping
        these services optional lets the application remain usable when an OEM TTS
        engine or media plugin cannot be initialized.
        """

        if speech is not None and speech is not self._speech:
            if self._speech is not None:
                try:
                    self._speech.state_changed.disconnect(
                        self.chat_page.set_speech_state
                    )
                    self.settings_page.tts_settings_changed.disconnect(
                        self._speech.reload_provider
                    )
                except (RuntimeError, TypeError):
                    pass
            self._speech = speech
            self._speech.state_changed.connect(
                self.chat_page.set_speech_state
            )
            self.settings_page.tts_settings_changed.connect(
                self._speech.reload_provider
            )
        if notification_sound is not None:
            self._notification_sound = notification_sound

    def _show_messages(self) -> None:
        self.message_nav.setChecked(True)
        self.conversations.show()
        self.content.setCurrentWidget(self.chat_page)
        if self._mobile_body is not None:
            self._mobile_body.setCurrentWidget(self.conversations)

    def _show_characters(self) -> None:
        self.character_nav.setChecked(True)
        self.conversations.hide()
        self.characters_page.refresh()
        self.content.setCurrentWidget(self.characters_page)
        if self._mobile_body is not None:
            self._mobile_body.setCurrentWidget(self.content)

    def _show_settings(self) -> None:
        self.settings_nav.setChecked(True)
        self.conversations.hide()
        self.content.setCurrentWidget(self.settings_page)
        if self._mobile_body is not None:
            self._mobile_body.setCurrentWidget(self.content)

    def _new_conversation(self) -> None:
        model = (
            self._settings.get("default_model")
            or self.settings_page.default_model.currentData()
        )
        conversation = self._chats.create_conversation(model)
        self.conversations.refresh(select_id=conversation.id)
        self._show_messages()
        self.conversations.select(conversation.id)

    def _new_character_conversation(self, character_id: str) -> None:
        character = self._characters.get(character_id)
        if character is None:
            return
        model = (
            self._settings.get("default_model")
            or self.settings_page.default_model.currentData()
        )
        data = character.card["data"]
        greetings = [str(data.get("first_mes", "")).strip()]
        alternates = data.get("alternate_greetings", [])
        if isinstance(alternates, list):
            greetings.extend(
                str(item).strip() for item in alternates if str(item).strip()
            )
        greetings = [item for item in greetings if item]
        used = sum(
            item.character_id == character.id
            for item in self._chats.list_conversations()
        )
        opening = greetings[used % len(greetings)] if greetings else ""
        conversation = self._chats.create_conversation(
            model,
            title=character.name,
            character_id=character.id,
            opening_message=opening,
        )
        self.conversations.refresh(select_id=conversation.id)
        self._show_messages()
        self.conversations.select(conversation.id)

    def _characters_changed(self) -> None:
        current = self._conversation_id
        self.conversations.refresh(select_id=current)
        if current:
            self._open_conversation(current, force_reload=True)

    def _open_conversation(
        self,
        conversation_id: str,
        *,
        force_reload: bool = False,
    ) -> None:
        if self._thread is not None or self._delivery is not None:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None:
            return
        already_loaded = (
            self._conversation_id == conversation_id
            and self.chat_page.conversation_id == conversation_id
        )
        self._conversation_id = conversation_id
        self._refresh_text_model_controls(conversation.model)
        if force_reload or not already_loaded:
            self.chat_page.load(
                conversation, self._chats.list_turns(conversation_id)
            )
        if self._mobile_body is not None:
            self.message_nav.setChecked(True)
            self.content.setCurrentWidget(self.chat_page)
            self._mobile_body.setCurrentWidget(self.content)

    def _open_current_mobile_conversation(self, item) -> None:
        """再次点击已选中的会话时，也能从移动端列表进入聊天页。"""

        if (
            self._mobile_body is not None
            and self._mobile_body.currentWidget() is self.conversations
        ):
            self._open_conversation(str(item.data(256)))

    def _send_sticker(self, sticker_id: str) -> None:
        self._send("", "", sticker_id)

    def _send(
        self,
        text: str,
        image_source: str = "",
        sticker_id: str = "",
    ) -> None:
        if (
            self._thread is not None
            or self._pending_delivery is not None
            or self._delivery is not None
        ):
            return
        api_key = self._text_api_key()
        if not api_key:
            QMessageBox.information(
                self,
                "需要 API Key",
                f"请先在设置中保存{self._text_provider_label()} API Key。",
            )
            self._show_settings()
            return
        if self._conversation_id is None:
            self._new_conversation()
        conversation = self._chats.get_conversation(self._conversation_id)
        if conversation is None:
            return
        sticker = sticker_by_id(sticker_id) if sticker_id else None
        if sticker_id and sticker is None:
            return
        image_path = ""
        if image_source:
            try:
                image_path = import_chat_image(
                    image_source, app_data_root=self._media_root
                )
            except AvatarError as exc:
                QMessageBox.warning(self, "无法发送图片", str(exc))
                return
        text = (
            sticker.model_text
            if sticker is not None
            else text.strip() or ("看看这张图片。" if image_path else "")
        )
        if not text:
            return

        character = (
            self._characters.get(conversation.character_id)
            if conversation.character_id
            else None
        )
        history = self._chats.completed_history(
            conversation.id,
            max_turns=16 if character is not None else None,
        )
        character_prompt = (
            build_character_prompt(
                character.card,
                history,
                text,
                user_name=self._settings.get("user_name", "用户"),
                user_persona=self._settings.get("user_persona", ""),
                role_state=self._role_state(conversation),
            )
            if character
            else None
        )
        turn = self._chats.create_turn(
            conversation.id,
            text,
            conversation.model,
            user_image_path=image_path,
            user_sticker=sticker.id if sticker is not None else "",
        )
        self._turn_id = turn.id
        self._request_conversation_id = conversation.id
        self._request_kind = "user"
        self._notification_pending = False
        self._chats.mark_streaming(turn.id)
        self._answer = ""
        self._reasoning = ""
        self.chat_page.add_user_message(
            text,
            image_path,
            sticker.id if sticker is not None else "",
        )
        self.chat_page.set_generating(True)
        self._proactive.schedule_next()

        self._start_chat_worker(
            api_key,
            conversation.model,
            history,
            text,
            system_prompt=character_prompt.system if character_prompt else "",
            example_messages=(
                character_prompt.examples if character_prompt else ()
            ),
            image_path=image_path,
            temperature=(
                self._roleplay_temperature()
                if character is not None and conversation.model == MODEL_CHAT
                else None
            ),
        )

    def _start_chat_worker(
        self,
        api_key: str,
        model: str,
        history,
        request_text: str,
        *,
        system_prompt: str = "",
        example_messages=(),
        image_path: str = "",
        temperature: float | None = None,
    ) -> None:
        service = self._create_text_service(api_key)
        self._thread = QThread(self)
        self._worker = ChatWorker(
            service,
            model,
            history,
            request_text,
            system_prompt=system_prompt,
            example_messages=example_messages,
            temperature=temperature,
            image_service=(
                self._create_image_service()
                if image_path and self._image_api_key()
                else None
            ),
            image_path=image_path,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.reasoning.connect(self._on_reasoning)
        self._worker.content.connect(self._on_content)
        self._worker.completed.connect(self._on_completed)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.failed.connect(self._on_failed)
        self._worker.image_described.connect(self._on_image_described)
        self._worker.image_analysis_failed.connect(
            self._on_image_analysis_failed
        )
        self._worker.finished.connect(self._thread.quit)
        self._thread.finished.connect(self._stream_finished)
        self._thread.start()

    def _on_image_described(self, description: str) -> None:
        if self._turn_id:
            self._chats.set_user_image_description(
                self._turn_id, description
            )

    def _on_image_analysis_failed(self, _error_code: str) -> None:
        """看图失败时继续文本回复；气泡会由实际回复替换等待提示。"""

    def _text_provider(self) -> str:
        provider = self._settings.get("text_provider", "deepseek").lower()
        return provider if provider in {"deepseek", "grsai"} else "deepseek"

    def _text_provider_label(self) -> str:
        return "GRS AI" if self._text_provider() == "grsai" else "DeepSeek"

    def _refresh_text_model_controls(
        self, selected_model: str = ""
    ) -> None:
        if not selected_model and self._conversation_id:
            conversation = self._chats.get_conversation(
                self._conversation_id
            )
            selected_model = conversation.model if conversation else ""
        models = text_provider_models(
            self._text_provider(),
            self._settings.get(
                "grsai_text_model", DEFAULT_GRSAI_TEXT_MODEL
            ),
        )
        self.chat_page.set_model_options(models, selected_model)

    def _credential_value(self, name: str, fallback: str = "") -> str:
        getter = getattr(self._credentials, name, None)
        if callable(getter):
            value = str(getter() or "").strip()
            if value:
                return value
        if fallback:
            legacy = getattr(self._credentials, fallback, None)
            if callable(legacy):
                return str(legacy() or "").strip()
        return ""

    def _text_api_key(self) -> str:
        if self._text_provider() == "grsai":
            return self._credential_value("get_grsai_text_api_key")
        return self._credential_value("get_api_key")

    def _create_text_gateway(self, api_key: str):
        if self._gateway_factory is not None:
            return self._gateway_factory(api_key)
        if self._text_provider() == "grsai":
            return GrsAiGateway(
                api_key,
                base_url=self._settings.get(
                    "grsai_text_base_url", DEFAULT_GRSAI_API_BASE_URL
                ),
                model=self._settings.get(
                    "grsai_text_model", DEFAULT_GRSAI_TEXT_MODEL
                ),
            )
        return DeepSeekHttpGateway(api_key)

    def _create_text_service(self, api_key: str) -> ChatStreamService:
        """Capture settings and construct the gateway on the UI thread.

        SettingsRepository owns the SQLite connection created by the UI
        thread.  A deferred factory must not call back into it from a QThread.
        The resulting HTTP gateway itself is safe to consume in the worker.
        """

        gateway = self._create_text_gateway(api_key)
        return ChatStreamService(lambda: gateway)

    def _image_provider(self) -> str:
        provider = self._settings.get("image_provider", "siliconflow").lower()
        return provider if provider in {"siliconflow", "grsai"} else "siliconflow"

    def _image_api_key(self) -> str:
        if self._image_provider() == "grsai":
            return self._credential_value("get_grsai_image_api_key")
        return self._credential_value(
            "get_siliconflow_image_api_key", "get_siliconflow_api_key"
        )

    def _siliconflow_api_key(self) -> str:
        """Legacy test/plugin compatibility; runtime uses capability keys."""

        return self._credential_value(
            "get_siliconflow_image_api_key", "get_siliconflow_api_key"
        )

    def _create_image_service(self):
        provider = self._image_provider()
        api_key = self._image_api_key()
        if provider == "grsai":
            kwargs = {
                "base_url": self._settings.get(
                    "grsai_image_base_url", DEFAULT_GRSAI_API_BASE_URL
                ),
                "vision_model": self._settings.get(
                    "grsai_vision_model", DEFAULT_GRSAI_VISION_MODEL
                ),
                "image_model": self._settings.get(
                    "grsai_image_model", DEFAULT_GRSAI_IMAGE_MODEL
                ),
                "image_size": self._settings.get(
                    "grsai_image_size", DEFAULT_GRSAI_IMAGE_SIZE
                ),
            }
            factory = self._image_service_factory or GrsAiImageService
            return factory(api_key, **kwargs)
        kwargs = {
            "vision_model": self._settings.get(
                "siliconflow_vision_model",
                DEFAULT_SILICONFLOW_VISION_MODEL,
            ),
            "image_model": self._settings.get(
                "siliconflow_image_model",
                DEFAULT_SILICONFLOW_IMAGE_MODEL,
            ),
            "image_size": self._settings.get(
                "siliconflow_image_size",
                DEFAULT_SILICONFLOW_IMAGE_SIZE,
            ),
        }
        factory = self._image_service_factory or SiliconFlowImageService
        return factory(api_key, **kwargs)

    def _autonomous_images_enabled(self) -> bool:
        return self._settings.get(
            "autonomous_images_enabled", "true"
        ).lower() in {"1", "true", "yes", "on"}

    def _recently_shared_image(self, conversation_id: str) -> bool:
        completed = [
            turn
            for turn in self._chats.list_turns(conversation_id)
            if turn.status == "completed"
        ]
        # 当前轮次加前三个已完成轮次构成冷却窗口，避免角色连续刷图。
        return any(turn.assistant_image_path for turn in completed[-4:])

    def _enqueue_autonomous_image(
        self,
        conversation_id: str,
        turn_id: str,
        character_name: str,
        character_card: dict,
        answer: str,
        *,
        fallback_prompt: str = "",
        segment_index: int | None = None,
    ) -> None:
        fallback = fallback_prompt.strip()[:1_500]
        if (
            self._shutting_down
            or not self._autonomous_images_enabled()
            or not self._image_api_key()
            or (
                not fallback
                and (
                    not self._text_api_key()
                    or self._recently_shared_image(conversation_id)
                )
            )
        ):
            return
        api_key = self._text_api_key()
        request = ""
        if api_key:
            history = self._chats.completed_history(
                conversation_id, max_turns=8
            )
            request = autonomous_image_request(
                character_name,
                character_card,
                history,
                answer,
            )
        self._image_queue = deque(
            job
            for job in self._image_queue
            if job.turn_id != turn_id
        )
        self._image_queue.append(
            _AutonomousImageJob(
                conversation_id,
                turn_id,
                decision_request=request,
                prompt=fallback,
                segment_index=segment_index,
            )
        )
        self._start_next_autonomous_image()

    def _start_next_autonomous_image(self) -> None:
        if self._shutting_down or self._image_thread is not None:
            return
        while self._image_queue:
            job = self._image_queue.popleft()
            conversation = self._chats.get_conversation(job.conversation_id)
            turn = next(
                (
                    item
                    for item in self._chats.list_turns(job.conversation_id)
                    if item.id == job.turn_id
                ),
                None,
            )
            can_decide = bool(
                job.decision_request and self._text_api_key()
            )
            direct_action = bool(job.prompt) and not can_decide
            if (
                conversation is None
                or turn is None
                or turn.status != "completed"
                or turn.assistant_image_path
                or not self._autonomous_images_enabled()
                or not self._image_api_key()
                or (
                    not job.prompt
                    and (
                        not can_decide
                        or self._recently_shared_image(job.conversation_id)
                    )
                )
            ):
                continue
            self._image_job = job
            if direct_action:
                self._start_autonomous_image_generation(job, job.prompt)
                return
            if not can_decide:
                continue
            self._image_decision = AutonomousImageDecision()
            api_key = self._text_api_key()
            service = self._create_text_service(api_key)
            self._image_thread = QThread(self)
            self._image_worker = ChatWorker(
                service,
                MODEL_CHAT,
                (),
                job.decision_request,
                system_prompt=AUTONOMOUS_IMAGE_SYSTEM_PROMPT,
            )
            self._image_worker.moveToThread(self._image_thread)
            self._image_thread.started.connect(self._image_worker.run)
            self._image_worker.completed.connect(
                self._on_autonomous_image_decision
            )
            self._image_worker.finished.connect(self._image_thread.quit)
            self._image_thread.finished.connect(
                self._autonomous_image_decision_finished
            )
            self._image_thread.start()
            return

    def _on_autonomous_image_decision(self, text: str) -> None:
        self._image_decision = parse_autonomous_image_decision(text)

    def _autonomous_image_decision_finished(self) -> None:
        decision = self._image_decision
        job = self._image_job
        self._dispose_autonomous_image_phase()
        selected_prompt = (
            decision.prompt
            if decision.send_image and decision.prompt
            else (job.prompt if job is not None else "")
        )
        if (
            not self._shutting_down
            and job is not None
            and selected_prompt
            and self._chats.get_conversation(job.conversation_id) is not None
            and self._image_api_key()
        ):
            self._start_autonomous_image_generation(job, selected_prompt)
            return
        self._image_job = None
        self._start_next_autonomous_image()

    def _start_autonomous_image_generation(
        self, job: _AutonomousImageJob, prompt: str
    ) -> None:
        self._image_job = job
        self._image_thread = QThread(self)
        self._image_worker = ImageGenerationWorker(
            self._create_image_service(),
            prompt,
            app_data_root=self._media_root,
        )
        self._image_worker.moveToThread(self._image_thread)
        self._image_thread.started.connect(self._image_worker.run)
        self._image_worker.completed.connect(
            self._on_autonomous_image_generated
        )
        self._image_worker.failed.connect(
            self._on_autonomous_image_failed
        )
        self._image_worker.finished.connect(self._image_thread.quit)
        self._image_thread.finished.connect(
            self._autonomous_image_generation_finished
        )
        self._image_thread.start()

    def _on_autonomous_image_generated(self, image_path: str) -> None:
        job = self._image_job
        if self._shutting_down or job is None:
            return
        try:
            self._chats.set_assistant_image_path(
                job.turn_id,
                image_path,
                segment_index=job.segment_index,
            )
        except (KeyError, ValueError):
            return
        self.conversations.refresh(select_id=self._conversation_id)
        if (
            job.conversation_id == self._conversation_id
            and self._thread is None
            and self._delivery is None
        ):
            self._open_conversation(job.conversation_id, force_reload=True)
        self._play_notification()
        if not self.isActiveWindow():
            QApplication.alert(self, 5_000)

    def _on_autonomous_image_failed(self, error_code: str) -> None:
        job = self._image_job
        if (
            self._shutting_down
            or job is None
            or job.conversation_id != self._conversation_id
        ):
            return
        self.chat_page.add_image_error(error_code)

    def _autonomous_image_generation_finished(self) -> None:
        self._dispose_autonomous_image_phase()
        self._image_job = None
        self._image_decision = AutonomousImageDecision()
        self._start_next_autonomous_image()

    def _dispose_autonomous_image_phase(self) -> None:
        if self._image_worker is not None:
            self._image_worker.deleteLater()
        if self._image_thread is not None:
            self._image_thread.deleteLater()
        self._image_worker = None
        self._image_thread = None

    def _on_reasoning(self, text: str) -> None:
        self._reasoning += text

    def _on_content(self, text: str) -> None:
        self._answer += text

    def _on_completed(self, answer: str) -> None:
        conversation_id = self._request_conversation_id
        if self._turn_id and conversation_id:
            plan = classify_role_reply(answer)
            visible_answer = plan.visible_text or answer.strip()
            self._chats.complete_turn(
                self._turn_id,
                visible_answer,
                self._reasoning,
                assistant_segments_json=serialize_reply_segments(
                    plan.segments
                ),
            )
            self._enqueue_summary(conversation_id, visible_answer)
            conversation = self._chats.get_conversation(conversation_id)
            character = (
                self._characters.get(conversation.character_id)
                if conversation and conversation.character_id
                else None
            )
            fallback_prompt = ""
            image_segment_index: int | None = None
            if character is not None and plan.has_image_action:
                for index, segment in enumerate(plan.segments):
                    if segment.kind == "image" and segment.prompt:
                        fallback_prompt = enrich_role_image_prompt(
                            character.name,
                            character.card,
                            segment.prompt,
                        )
                        image_segment_index = index
                        break
            turn = next(
                (
                    item
                    for item in self._chats.list_turns(conversation_id)
                    if item.id == self._turn_id
                ),
                None,
            )
            explicit_prompt = explicit_image_request_prompt(
                turn.user_content if turn is not None else ""
            )
            if character is not None and explicit_prompt and not fallback_prompt:
                fallback_prompt = enrich_role_image_prompt(
                    character.name,
                    character.card,
                    explicit_prompt,
                )
            if character is not None:
                self._enqueue_autonomous_image(
                    conversation_id,
                    self._turn_id,
                    character.name,
                    character.card,
                    visible_answer,
                    fallback_prompt=fallback_prompt,
                    segment_index=image_segment_index,
                )
            profile = read_tts_profile(character.card if character else None)
            self._pending_delivery = _ReplyDelivery(
                conversation_id,
                self._turn_id,
                plan,
                profile,
                self._reasoning,
                self._request_kind,
            )
            self._notification_pending = True

    def _on_cancelled(self) -> None:
        self._pending_delivery = None
        self._notification_pending = False
        if self._turn_id:
            if self._request_kind == "proactive":
                self._chats.delete_turn(self._turn_id)
            else:
                self._chats.fail_turn(self._turn_id, "cancelled")

    def _on_failed(self, error_code: str) -> None:
        self._pending_delivery = None
        self._notification_pending = False
        if self._turn_id:
            if self._request_kind == "proactive":
                self._chats.delete_turn(self._turn_id)
            else:
                self._chats.fail_turn(self._turn_id, "failed", error_code)

    def _stream_finished(self) -> None:
        request_conversation_id = self._request_conversation_id
        request_kind = self._request_kind
        visible_conversation_id = self._conversation_id
        pending_delivery = self._pending_delivery
        self._pending_delivery = None
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None
        self._turn_id = None
        self._request_conversation_id = None
        self._request_kind = "user"
        self.conversations.refresh(select_id=visible_conversation_id)
        if pending_delivery is not None:
            self.chat_page.discard_stream()
            if pending_delivery.conversation_id == visible_conversation_id:
                self._start_reply_delivery(pending_delivery)
            else:
                self.chat_page.finish_stream()
                if self._notification_pending:
                    self._play_notification()
                self._notification_pending = False
                if (
                    pending_delivery.request_kind == "proactive"
                    and not self.isActiveWindow()
                ):
                    QApplication.alert(self, 5_000)
                self._start_next_summary()
            return

        self.chat_page.finish_stream()
        if (
            request_conversation_id
            and request_conversation_id == visible_conversation_id
        ):
            self._open_conversation(
                request_conversation_id, force_reload=True
            )
        self._notification_pending = False
        if request_kind == "proactive" and not self.isActiveWindow():
            QApplication.alert(self, 5_000)
        self._start_next_summary()

    def _start_reply_delivery(self, delivery: _ReplyDelivery) -> None:
        """把完整回复按真人聊天节奏逐条投递到消息列表。"""

        self._delivery = delivery
        self._delivery_segments = deque(
            (index, segment)
            for index, segment in enumerate(delivery.plan.segments)
            if segment.kind in {"dialogue", "narration"} and segment.text
        )
        self._delivery_reasoning = delivery.reasoning
        self._delivery_speech_started = False
        self.chat_page.set_generating(True)
        if self._delivery_segments:
            self.chat_page.show_typing_indicator()
            self._delivery_timer.start(
                self._reply_delay_ms(
                    self._delivery_segments[0][1],
                    first=True,
                )
            )
        else:
            self._finish_reply_delivery()

    def _deliver_next_segment(self) -> None:
        delivery = self._delivery
        if delivery is None:
            return
        if delivery.conversation_id != self._conversation_id:
            self._finish_reply_delivery()
            return
        if not self._delivery_segments:
            self._finish_reply_delivery()
            return
        index, segment = self._delivery_segments.popleft()
        self.chat_page.discard_stream()
        message_key = f"turn:{delivery.turn_id}:segment:{index}"
        self.chat_page.add_assistant_segment(
            segment.text,
            message_key=message_key,
            speech_enabled=segment.kind == "dialogue",
            narration=segment.kind == "narration",
            reasoning=self._delivery_reasoning,
        )
        self._delivery_reasoning = ""
        if self._notification_pending:
            self._play_notification()
            self._notification_pending = False
        if (
            not self._delivery_speech_started
            and segment.kind == "dialogue"
            and delivery.plan.dialogue_text
            and self._speech is not None
            and self._settings.get("tts_auto_play", "true").lower()
            in {"1", "true", "yes", "on"}
        ):
            self._delivery_speech_started = True
            self._speech.speak(
                message_key,
                delivery.plan.dialogue_text,
                delivery.profile,
            )
        if self._delivery_segments:
            next_segment = self._delivery_segments[0][1]
            self.chat_page.show_typing_indicator()
            self._delivery_timer.start(
                self._reply_delay_ms(next_segment)
            )
        else:
            self._finish_reply_delivery()

    @staticmethod
    def _reply_delay_ms(
        segment: ReplySegment,
        *,
        first: bool = False,
    ) -> int:
        """按下一段内容估算真人组织和输入消息所需的等待时间。"""

        text = segment.text.strip()
        characters = min(len(text), 100)
        punctuation = sum(
            text.count(symbol)
            for symbol in "，,。！？!?；;：:…"
        )
        base = 760 if first else 480
        per_character = 24 if segment.kind == "dialogue" else 18
        jitter = sum(ord(character) for character in text[:24]) % 260
        estimated = (
            base
            + characters * per_character
            + min(punctuation, 8) * 85
            + jitter
        )
        minimum = 900 if first else 650
        maximum = 3_200 if first else 2_800
        return max(minimum, min(estimated, maximum))

    def _finish_reply_delivery(self) -> None:
        delivery = self._delivery
        if delivery is None:
            return
        self._delivery_timer.stop()
        self._delivery = None
        self._delivery_segments.clear()
        self._delivery_reasoning = ""
        self.chat_page.discard_stream()
        self.chat_page.finish_stream()
        self.conversations.refresh(select_id=self._conversation_id)
        if self._notification_pending:
            self._play_notification()
        self._notification_pending = False
        if delivery.conversation_id == self._conversation_id:
            turn = next(
                (
                    item
                    for item in self._chats.list_turns(
                        delivery.conversation_id
                    )
                    if item.id == delivery.turn_id
                ),
                None,
            )
            if turn is not None and turn.assistant_image_path:
                self._open_conversation(
                    delivery.conversation_id, force_reload=True
                )
        if delivery.request_kind == "proactive" and not self.isActiveWindow():
            QApplication.alert(self, 5_000)
        self._start_next_summary()

    def _send_proactive_message(self) -> None:
        """让当前角色会话在随机计时到期后主动开启话题。"""

        if (
            self._thread is not None
            or self._delivery is not None
            or self._pending_delivery is not None
            or self._conversation_id is None
        ):
            return
        api_key = self._text_api_key()
        if not api_key:
            return
        conversation = self._chats.get_conversation(self._conversation_id)
        if conversation is None or not conversation.character_id:
            return
        character = self._characters.get(conversation.character_id)
        if character is None:
            return

        history = self._chats.completed_history(
            conversation.id, max_turns=16
        )
        current_time = datetime.now().astimezone()
        character_prompt = build_character_prompt(
            character.card,
            history,
            "",
            user_name=self._settings.get("user_name", "用户"),
            user_persona=self._settings.get("user_persona", ""),
            role_state=self._role_state(conversation),
            current_time=current_time,
        )
        turn = self._chats.create_proactive_turn(
            conversation.id, conversation.model
        )
        self._turn_id = turn.id
        self._request_conversation_id = conversation.id
        self._request_kind = "proactive"
        self._notification_pending = False
        self._chats.mark_streaming(turn.id)
        self._answer = ""
        self._reasoning = ""
        self.chat_page.add_assistant_stream()
        self.chat_page.set_generating(True)
        system_prompt = "\n\n".join(
            part
            for part in (character_prompt.system, PROACTIVE_SYSTEM_SUFFIX)
            if part.strip()
        )
        self._start_chat_worker(
            api_key,
            conversation.model,
            history,
            proactive_request(
                character.name,
                current_time=current_time,
            ),
            system_prompt=system_prompt,
            example_messages=character_prompt.examples,
            temperature=(
                self._roleplay_temperature()
                if conversation.model == MODEL_CHAT
                else None
            ),
        )

    def _enqueue_summary(self, conversation_id: str, answer: str) -> None:
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None:
            return
        character = (
            self._characters.get(conversation.character_id)
            if conversation.character_id
            else None
        )
        role_memory_enabled = self._settings.get(
            "role_memory_enabled", "true"
        ).lower() in {"1", "true", "yes", "on"}
        if character is not None and role_memory_enabled:
            completed = [
                turn
                for turn in self._chats.list_turns(conversation_id)
                if turn.status == "completed"
            ]
            user_text = completed[-1].user_content if completed else ""
            job = _SummaryJob(
                conversation_id,
                role_memory_request(
                    character.name,
                    conversation.role_state_json,
                    user_text,
                    answer,
                ),
                ROLE_MEMORY_SYSTEM_PROMPT,
                True,
            )
        else:
            job = _SummaryJob(
                conversation_id,
                summary_request(answer),
                SUMMARY_SYSTEM_PROMPT,
            )
        self._summary_queue = deque(
            queued
            for queued in self._summary_queue
            if queued.conversation_id != conversation_id
        )
        self._summary_queue.append(job)

    def _enqueue_pending_summaries(self) -> None:
        for conversation_id, answer in self._chats.pending_summary_jobs():
            self._enqueue_summary(conversation_id, answer)
        self._start_next_summary()

    def _start_next_summary(self) -> None:
        if self._summary_thread is not None or not self._summary_queue:
            return
        api_key = self._text_api_key()
        if not api_key:
            return
        job = self._summary_queue.popleft()
        if self._chats.get_conversation(job.conversation_id) is None:
            self._start_next_summary()
            return

        self._summary_job = job
        service = self._create_text_service(api_key)
        self._summary_thread = QThread(self)
        self._summary_worker = ChatWorker(
            service,
            MODEL_CHAT,
            (),
            job.request_text,
            system_prompt=job.system_prompt,
            temperature=0.2,
        )
        self._summary_worker.moveToThread(self._summary_thread)
        self._summary_thread.started.connect(self._summary_worker.run)
        self._summary_worker.completed.connect(self._on_summary_completed)
        self._summary_worker.failed.connect(self._on_summary_failed)
        self._summary_worker.cancelled.connect(self._on_summary_failed)
        self._summary_worker.finished.connect(self._summary_thread.quit)
        self._summary_thread.finished.connect(self._summary_finished)
        self._summary_thread.start()

    def _on_summary_completed(self, text: str) -> None:
        if self._summary_job is None:
            return
        if self._summary_job.updates_role_state:
            result = parse_role_postprocess(text)
            summary = result.summary
            if summary and result.role_state:
                self._chats.set_role_state(
                    self._summary_job.conversation_id,
                    result.role_state,
                )
        else:
            summary = clean_ai_summary(text)
        if summary:
            self._chats.set_ai_summary(
                self._summary_job.conversation_id, summary
            )
        else:
            self._chats.mark_summary_failed(
                self._summary_job.conversation_id
            )
        self.conversations.refresh(select_id=self._conversation_id)

    def _on_summary_failed(self, _error_code: str = "") -> None:
        if self._summary_job is not None:
            self._chats.mark_summary_failed(
                self._summary_job.conversation_id
            )
            self.conversations.refresh(select_id=self._conversation_id)

    def _summary_finished(self) -> None:
        if self._summary_worker is not None:
            self._summary_worker.deleteLater()
        if self._summary_thread is not None:
            self._summary_thread.deleteLater()
        self._summary_worker = None
        self._summary_thread = None
        self._summary_job = None
        self._start_next_summary()

    def _roleplay_temperature(self) -> float:
        try:
            value = float(
                self._settings.get("roleplay_temperature", "1.3")
            )
        except ValueError:
            return 1.3
        return max(0.0, min(value, 2.0))

    def _role_state(self, conversation) -> dict:
        if self._settings.get(
            "role_memory_enabled", "true"
        ).lower() not in {"1", "true", "yes", "on"}:
            return {}
        try:
            state = json.loads(conversation.role_state_json or "{}")
        except (TypeError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _profile_for_current_conversation(self) -> TtsProfile:
        conversation = (
            self._chats.get_conversation(self._conversation_id)
            if self._conversation_id
            else None
        )
        character = (
            self._characters.get(conversation.character_id)
            if conversation and conversation.character_id
            else None
        )
        return read_tts_profile(character.card if character else None)

    def _play_notification(self, *, force: bool = False) -> bool:
        if self._notification_sound is None:
            return False
        enabled = (
            self._settings.get(
                "notification_sound_enabled", "true"
            ).lower()
            in {"1", "true", "yes", "on"}
        )
        return self._notification_sound.play() if force or enabled else False

    def _play_message(self, message_key: str, text: str) -> None:
        if self._speech is not None:
            self._speech.speak(
                message_key, text, self._profile_for_current_conversation()
            )

    def _stop_speech(self, _message_key: str = "") -> None:
        if self._speech is not None:
            self._speech.stop()

    def _stop(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def _change_model(self, model: str) -> None:
        if self._conversation_id:
            self._chats.set_model(self._conversation_id, model)
            self.conversations.refresh(select_id=self._conversation_id)

    def _edit_current(self) -> None:
        if self._conversation_id:
            self._edit_conversation(self._conversation_id)

    def _edit_conversation(self, conversation_id: str) -> None:
        if self._thread is not None or self._delivery is not None:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None:
            return
        dialog = ConversationEditDialog(
            conversation, self._characters.list(), self
        )
        if not dialog.exec():
            return
        self._chats.rename_conversation(
            conversation.id, dialog.name.text()
        )
        self._chats.set_avatar_override(
            conversation.id, dialog.avatar_path
        )
        self._chats.bind_character(
            conversation.id, dialog.character.currentData()
        )
        self.conversations.refresh(select_id=conversation.id)
        self._open_conversation(conversation.id, force_reload=True)

    def _delete_current(self) -> None:
        if not self._conversation_id:
            return
        answer = QMessageBox.question(
            self,
            "删除会话",
            "确定删除当前会话及全部消息吗？此操作无法撤销。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._chats.delete_conversation(self._conversation_id)
        self._conversation_id = None
        self.conversations.refresh()
        remaining = self._chats.list_conversations()
        if remaining:
            self.conversations.select(remaining[0].id)
        else:
            self._new_conversation()

    def _clear_all(self) -> None:
        answer = QMessageBox.question(
            self,
            "清空全部会话",
            "确定清空所有本地会话吗？此操作无法撤销。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._chats.clear_all()
        self._conversation_id = None
        self.conversations.refresh()
        self._new_conversation()

    def _credentials_updated(self) -> None:
        if self._text_api_key() and not self._chats.list_conversations():
            self._new_conversation()
        self._enqueue_pending_summaries()

    def _apply_theme(self, value: str) -> None:
        if value == "system":
            scheme = QApplication.styleHints().colorScheme()
            dark = scheme == Qt.ColorScheme.Dark
        else:
            dark = value == "dark"
        QApplication.instance().setStyleSheet(
            stylesheet(dark, mobile=self._mobile)
        )

    def closeEvent(self, event: QCloseEvent) -> None:
        self._shutting_down = True
        self._proactive.stop()
        self._delivery_timer.stop()
        self._delivery = None
        self._pending_delivery = None
        self._delivery_segments.clear()
        self._image_queue.clear()
        self.settings_page.shutdown_model_refresh()
        if self._notification_sound is not None:
            self._notification_sound.shutdown()
        if self._speech is not None:
            self._speech.shutdown()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
        if self._summary_worker is not None:
            self._summary_worker.cancel()
        if self._summary_thread is not None:
            self._summary_thread.quit()
            self._summary_thread.wait(1500)
        if self._image_worker is not None:
            self._image_worker.cancel()
        if self._image_thread is not None:
            self._image_thread.quit()
            self._image_thread.wait(1500)
        event.accept()
