"""微信式三栏主窗口及聊天协调逻辑。"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import TYPE_CHECKING

from PySide6.QtCore import QSize, Qt, QThread, QTimer
from PySide6.QtGui import QCloseEvent
from PySide6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QStackedWidget,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from ...anthropic_gateway import DeepSeekHttpGateway
from ...branding import PRODUCT_NAME, PRODUCT_SHORT_NAME
from ...character_prompt import build_character_prompt, roleplay_memory_query
from ...chat_service import ChatStreamService
from ...diagnostics import DiagnosticRecorder
from ...grsai_gateway import (
    DEFAULT_GRSAI_API_BASE_URL,
    DEFAULT_GRSAI_TEXT_MODEL,
    GrsAiGateway,
)
from ...model_catalog import (
    MODEL_CHAT,
    resolve_model,
    text_provider_models,
)
from ...relationship_policy import (
    ProactiveDecision,
    evaluate_proactive_message,
    is_repetitive_proactive_message,
    proactive_context_text,
    relationship_policy_for,
    relationship_policy_prompt,
)
from ...roleplay_director import (
    DirectorRequest,
    assess_director_trigger,
    build_director_request_text,
)
from ...text_models import safe_sampling_options, text_model_capabilities
from ...tts import TtsProfile, read_tts_profile
from ..ai_features import (
    OPENING_SYSTEM_SUFFIX,
    PROACTIVE_SYSTEM_SUFFIX,
    CharacterDiscoveryScheduler,
    ProactiveMessageScheduler,
    assign_image_events,
    classify_role_reply,
    deserialize_reply_segments,
    explicit_image_request_prompt,
    opening_request,
    proactive_request,
    serialize_reply_segments,
)
from ..assets import AvatarError, import_chat_image
from ..background import (
    AutonomousImageRunner,
    CharacterAvatarRunner,
    CharacterDiscoveryRunner,
    SummaryRunner,
)
from ..builtin_characters import BuiltinCharacterManager
from ..data.repositories import (
    CharacterRepository,
    ChatRepository,
    Conversation,
    SettingsRepository,
)
from ..flow import MessageFlowController, ReplyDelivery
from ..image_service import (
    DEFAULT_GRSAI_IMAGE_MODEL,
    DEFAULT_GRSAI_IMAGE_SIZE,
    DEFAULT_GRSAI_VISION_MODEL,
    DEFAULT_SILICONFLOW_IMAGE_MODEL,
    DEFAULT_SILICONFLOW_IMAGE_SIZE,
    DEFAULT_SILICONFLOW_VISION_MODEL,
    GrsAiImageService,
    SiliconFlowImageService,
)
from ..model_discovery import deserialize_models
from ..platform import is_android_platform
from ..security.credentials import CredentialStore
from ..stickers import sticker_by_id
from ..theme import stylesheet
from .conversation_edit_dialog import ConversationEditDialog
from .icons import navigation_icon
from .memory_manager_dialog import MemoryManagerDialog
from .pages.characters_page import CharactersPage
from .pages.chat_page import ChatPage
from .pages.conversations_page import ConversationsPage
from .pages.settings_page import SettingsPage

ROLEPLAY_RECENT_TURNS = 12

if TYPE_CHECKING:
    from ..notification_sound import NotificationSound
    from ..sync_controller import SyncController
    from ..tts import SpeechController


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
        background_jobs_enabled: bool = True,
        sync_controller: SyncController | None = None,
        diagnostics: DiagnosticRecorder | None = None,
    ) -> None:
        super().__init__()
        self._chats = chats
        self._characters = characters
        self._settings = settings
        self._credentials = credentials
        self._gateway_factory = gateway_factory
        self._image_service_factory = image_service_factory
        self._media_root = media_root
        self._background_jobs_enabled = background_jobs_enabled
        self._sync = sync_controller
        self._diagnostics = diagnostics
        self._speech: SpeechController | None = None
        self._notification_sound: NotificationSound | None = None
        self._shutting_down = False
        self._conversation_id: str | None = None
        self._pending_conversation_switch: tuple[str, bool] | None = None
        self._pending_send: tuple[str, str, str] | None = None
        self._pending_proactive: dict[str, ProactiveDecision] = {}
        # AI 主动开场：新建角色会话时触发，成功则清空模板，失败/无 key 回退模板。
        self._pending_opening_conversation_id: str | None = None
        self._opening_fallbacks: dict[str, str] = {}
        self._proactive = ProactiveMessageScheduler(settings, self)
        self._character_discovery = CharacterDiscoveryScheduler(settings, self)
        self._summary_runner: SummaryRunner | None = None
        self._character_discovery_runner: CharacterDiscoveryRunner | None = None
        self._character_avatar_runner: CharacterAvatarRunner | None = None
        self._image_runner: AutonomousImageRunner | None = None
        self._flow: MessageFlowController | None = None
        self._active_delivery: ReplyDelivery | None = None
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
            nav.setFixedHeight(72)
            nav_layout = QHBoxLayout(nav)
            nav_layout.setContentsMargins(10, 5, 10, 7)
            nav_layout.setSpacing(8)
        else:
            nav.setFixedWidth(80)
            nav_layout = QVBoxLayout(nav)
            nav_layout.setContentsMargins(10, 18, 10, 16)
            nav_layout.setSpacing(12)
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

        nav_specs = (
            ("消息", "messages", "打开消息列表"),
            ("角色", "characters", "打开角色列表"),
            ("设置", "settings", "打开设置"),
        )
        nav_buttons: list[QToolButton] = []
        for label, icon_name, accessible_name in nav_specs:
            button = QToolButton()
            button.setText(label)
            button.setIcon(navigation_icon(icon_name))
            button.setIconSize(QSize(23, 23))
            button.setToolButtonStyle(
                Qt.ToolButtonStyle.ToolButtonTextUnderIcon
            )
            button.setAccessibleName(accessible_name)
            button.setObjectName("navButton")
            button.setCheckable(True)
            if self._mobile:
                button.setMinimumHeight(54)
                nav_layout.addWidget(button, 1)
            else:
                button.setMinimumSize(58, 58)
                nav_layout.addWidget(button)
            nav_buttons.append(button)
        self.message_nav, self.character_nav, self.settings_nav = nav_buttons
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
        self.characters_page = CharactersPage(
            characters, builtins=builtins, settings=settings
        )
        self.settings_page = SettingsPage(
            settings, credentials, diagnostics=diagnostics
        )
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
        self.characters_page.policy_changed.connect(self._proactive.reload)
        self.chat_page.send_requested.connect(self._send)
        if self._mobile:
            self.chat_page.back_requested.connect(self._show_messages)
        self.chat_page.sticker_requested.connect(self._send_sticker)
        self.chat_page.stop_requested.connect(self._stop)
        self.chat_page.retry_requested.connect(self._send)
        self.chat_page.image_retry_requested.connect(self._retry_image_event)
        self.chat_page.model_changed.connect(self._change_model)
        self.chat_page.edit_requested.connect(self._edit_current)
        self.chat_page.memory_requested.connect(self._manage_current_memory)
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
        self.settings_page.character_discovery_settings_changed.connect(
            self._character_discovery.reload
        )
        self.settings_page.notification_sound_preview_requested.connect(
            lambda: self._play_notification(force=True)
        )
        if self._sync is not None:
            self.settings_page.sync_settings_changed.connect(self._sync.reload)
            self.settings_page.sync_now_requested.connect(self._sync.sync_now)
            self.settings_page.sync_account_create_requested.connect(
                self._sync.create_account
            )
            self.settings_page.sync_register_requested.connect(
                self._sync.register_account
            )
            self.settings_page.sync_login_requested.connect(
                self._sync.login_account
            )
            self.settings_page.sync_upgrade_requested.connect(
                self._sync.upgrade_account
            )
            self.settings_page.sync_link_reset_requested.connect(
                self._sync.reset_link_state
            )
            self.settings_page.sync_disconnect_requested.connect(
                self._sync.disconnect_account
            )
            self._sync.status_changed.connect(
                self.settings_page.set_sync_status
            )
            self._sync.account_authenticated.connect(
                self.settings_page.set_sync_account
            )
            self._sync.data_changed.connect(self._on_sync_data_changed)
            self._sync.proactive_claimed.connect(
                self._on_proactive_claimed
            )
        self._proactive.due.connect(self._send_proactive_message)
        self._character_discovery.due.connect(self._generate_random_character)
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
        self._summary_runner = SummaryRunner(
            chats=self._chats,
            characters=self._characters,
            settings=self._settings,
            create_text_service=self._create_aux_text_service,
            text_api_key=self._aux_text_api_key,
            text_model=self._aux_text_model,
            text_provider=self._resolved_aux_text_provider,
            sampling_options=self._aux_sampling_options,
            refresh=self._on_summary_refreshed,
            diagnostics=self._diagnostics,
            parent=self,
        )
        self._character_discovery_runner = CharacterDiscoveryRunner(
            create_text_service=self._create_text_service,
            text_api_key=self._text_api_key,
            on_generated=self._on_random_character_generated,
            on_error=self._on_random_character_error,
            sampling_options=self._main_sampling_options,
            parent=self,
        )
        self._character_avatar_runner = CharacterAvatarRunner(
            create_image_service=self._create_image_service,
            image_api_key=self._image_api_key,
            on_generated=self._on_random_character_avatar_generated,
            on_error=self._on_random_character_avatar_error,
            app_data_root=self._media_root,
            parent=self,
        )
        self._image_runner = AutonomousImageRunner(
            chats=self._chats,
            characters=self._characters,
            settings=self._settings,
            create_text_service=self._create_aux_text_service,
            create_image_service=self._create_image_service,
            text_api_key=self._aux_text_api_key,
            text_model=self._aux_text_model,
            text_provider=self._resolved_aux_text_provider,
            image_api_key=self._image_api_key,
            refresh=lambda: self.conversations.refresh(
                select_id=self._conversation_id
            ),
            on_image_saved=self._on_autonomous_image_saved,
            on_image_error=self._on_autonomous_image_error,
            media_root=self._media_root,
            diagnostics=self._diagnostics,
            parent=self,
        )
        if self._background_jobs_enabled:
            self._enqueue_missing_generated_avatars()
        self._flow = MessageFlowController(
            settings=self._settings,
            tts_auto_play_check=lambda: self._settings.get_bool(
                "tts_auto_play", True
            ),
            diagnostics=self._diagnostics,
            parent=self,
        )
        self._flow.image_described.connect(self._on_flow_image_described)
        self._flow.image_analysis_failed.connect(
            self._on_flow_image_analysis_failed
        )
        self._flow.turn_completed.connect(self._on_turn_completed)
        self._flow.turn_aborted.connect(self._on_turn_aborted)
        self._flow.stream_cleaned_up.connect(self._on_stream_cleaned_up)
        self._flow.delivery_started.connect(self._on_delivery_started)
        self._flow.delivery_typing.connect(self._on_delivery_typing)
        self._flow.delivery_segment.connect(self._on_delivery_segment)
        self._flow.delivery_speech.connect(self._on_delivery_speech)
        self._flow.delivery_notification.connect(self._play_notification)
        self._flow.delivery_finished.connect(self._on_delivery_finished)
        if self._background_jobs_enabled:
            self._proactive.start()
            self._character_discovery.start()
            self._enqueue_pending_summaries()

    @property
    def _summary_thread(self) -> QThread | None:
        """后台摘要线程（测试等待其空闲时读取）。"""

        return self._summary_runner.thread if self._summary_runner else None

    @property
    def _image_thread(self) -> QThread | None:
        """后台发图线程（测试等待其空闲时读取）。"""

        return self._image_runner.thread if self._image_runner else None

    @property
    def _character_discovery_thread(self) -> QThread | None:
        """随机新角色后台线程（测试等待其空闲时读取）。"""

        runner = self._character_discovery_runner
        return runner.thread if runner else None

    @property
    def _character_avatar_thread(self) -> QThread | None:
        """随机角色头像后台线程（测试等待其空闲时读取）。"""

        runner = self._character_avatar_runner
        return runner.thread if runner else None

    @property
    def _thread(self) -> QThread | None:
        """主管线线程（测试等待其空闲时读取）。"""

        return self._flow.thread if self._flow else None

    @property
    def _delivery(self) -> ReplyDelivery | None:
        """主管线投递状态（测试等待其空闲时读取）。"""

        return self._flow.delivery if self._flow else None

    @property
    def _answer(self) -> str:
        """流式累积正文；仅供测试观测。"""

        return self._flow.answer if self._flow else ""

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
        elif not self._flow or not self._flow.busy:
            # 桌面端回到消息页时，把当前会话滚到最新消息处。
            self.chat_page.scroll_to_latest()

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
        self._schedule_sync()
        self.conversations.refresh(select_id=conversation.id)
        self._show_messages()
        # refresh 为避免重复加载会屏蔽列表选择信号；必须显式打开新会话，
        # 否则列表虽然选中，聊天页仍可能停留在旧会话。
        self._open_conversation(conversation.id)

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
        self._schedule_sync()
        # 首条开场改由 AI 主动生成；模板（first_mes）仅作为兜底。load 时先
        # 不显示模板，AI 开场成功则清空模板，失败/无 key 则回退显示模板。
        self._pending_opening_conversation_id = conversation.id
        self._opening_fallbacks[conversation.id] = opening
        self.conversations.refresh(select_id=conversation.id)
        self._show_messages()
        self._open_conversation(conversation.id)
        self._send_ai_opening(conversation, opening)

    def _characters_changed(self) -> None:
        self._schedule_sync()
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
        if self._flow is not None and self._flow.thread is not None:
            self._pending_conversation_switch = (
                conversation_id,
                force_reload,
            )
            self._pending_send = None
            self._flow.stop()
            return
        if self._flow is not None and self._flow.busy:
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
                conversation,
                self._chats.list_turns(conversation_id),
                defer_opening=(
                    conversation_id == self._pending_opening_conversation_id
                ),
            )
        if self._mobile_body is not None:
            self.message_nav.setChecked(True)
            self.content.setCurrentWidget(self.chat_page)
            self._mobile_body.setCurrentWidget(self.content)
        # 打开（含已加载会话重新打开）时都滚到最新消息处；load 内部已触发，
        # 这里覆盖 already_loaded 不重新 load 的路径。
        self.chat_page.scroll_to_latest()

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
        if self._flow is not None and self._flow.thread is not None:
            self._pending_send = (text, image_source, sticker_id)
            self._pending_conversation_switch = None
            self._flow.stop()
            return
        if self._flow is not None and self._flow.busy:
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
        context_started = monotonic()
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
        role_memory_enabled = self._role_memory_enabled(
            character.id if character is not None else ""
        )
        role_state = self._role_state(conversation) if character else {}
        history = self._chats.completed_history(
            conversation.id,
            max_turns=ROLEPLAY_RECENT_TURNS if character is not None else None,
        )
        recalled_memories = (
            self._chats.recalled_memories(
                conversation.id,
                roleplay_memory_query(text, role_state),
                exclude_recent_turns=ROLEPLAY_RECENT_TURNS,
            )
            if character and role_memory_enabled
            else ()
        )
        character_prompt = (
            build_character_prompt(
                character.card,
                history,
                text,
                user_name=self._settings.get("user_name", "用户"),
                user_persona=self._settings.get("user_persona", ""),
                role_state=role_state,
                recalled_memories=recalled_memories,
            )
            if character
            else None
        )
        director_request = (
            self._roleplay_director_request(
                conversation,
                character.card,
                history,
                text,
                role_state,
            )
            if character is not None
            else None
        )
        turn = self._chats.create_turn(
            conversation.id,
            text,
            conversation.model,
            user_image_path=image_path,
            user_sticker=sticker.id if sticker is not None else "",
        )
        self._chats.mark_streaming(turn.id)
        self._schedule_sync()
        self.chat_page.add_user_message(
            text,
            image_path,
            sticker.id if sticker is not None else "",
        )
        self.chat_page.set_generating(True)
        self._proactive.schedule_next()

        service = self._create_text_service(api_key)
        image_service = (
            self._create_image_service()
            if image_path and self._image_api_key()
            else None
        )
        self._flow.begin_stream(
            service=service,
            model=conversation.model,
            history=history,
            request_text=text,
            system_prompt=(
                "\n\n".join(
                    (
                        character_prompt.system,
                        relationship_policy_prompt(
                            relationship_policy_for(
                                self._settings, character.id
                            )
                        ),
                    )
                )
                if character_prompt and character is not None
                else ""
            ),
            example_messages=(
                character_prompt.examples if character_prompt else ()
            ),
            post_history_prompt=(
                character_prompt.post_history if character_prompt else ""
            ),
            image_service=image_service,
            image_path=image_path,
            provider=self._text_provider(),
            image_provider=self._image_provider(),
            context_duration_ms=(monotonic() - context_started) * 1000,
            turn_id=turn.id,
            conversation_id=conversation.id,
            request_kind="user",
            director_request=director_request,
            **(
                self._roleplay_sampling_options(conversation.model)
                if character is not None
                else {}
            ),
        )

    def _text_provider(self) -> str:
        provider = self._settings.get("text_provider", "deepseek").lower()
        return provider if provider in {"deepseek", "grsai"} else "deepseek"

    def _text_provider_label(self) -> str:
        return "GRS AI" if self._text_provider() == "grsai" else "DeepSeek"

    def _text_model_supports_reasoning(self, conversation_model: str) -> bool:
        """按实际供应商模型能力判断是否应关闭采样参数。"""

        return self._text_capabilities(
            self._text_provider(), conversation_model
        ).reasoning

    def _text_capabilities(
        self,
        provider: str,
        model: str,
        *,
        grsai_model: str = "",
    ):
        configured = grsai_model or self._settings.get(
            "grsai_text_model", DEFAULT_GRSAI_TEXT_MODEL
        )
        catalog = deserialize_models(
            self._settings.get("model_catalog_grsai", "")
        )
        return text_model_capabilities(
            provider,
            model,
            configured_model=configured,
            catalog=catalog,
        )

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
        return self._text_api_key_for(self._text_provider())

    def _text_api_key_for(self, provider: str) -> str:
        if provider == "grsai":
            return self._credential_value("get_grsai_text_api_key")
        return self._credential_value("get_api_key")

    def _create_text_gateway_for(
        self,
        provider: str,
        api_key: str,
        *,
        grsai_model: str = "",
        timeout_seconds: int | None = None,
    ):
        if self._gateway_factory is not None:
            return self._gateway_factory(api_key)
        if provider == "grsai":
            options = {
                "base_url": self._settings.get(
                    "grsai_text_base_url", DEFAULT_GRSAI_API_BASE_URL
                ),
                "model": (
                    grsai_model
                    or self._settings.get(
                        "grsai_text_model", DEFAULT_GRSAI_TEXT_MODEL
                    )
                ),
            }
            if timeout_seconds is not None:
                options["timeout"] = timeout_seconds
            return GrsAiGateway(api_key, **options)
        if timeout_seconds is not None:
            return DeepSeekHttpGateway(api_key, timeout=timeout_seconds)
        return DeepSeekHttpGateway(api_key)

    def _create_text_gateway(self, api_key: str):
        return self._create_text_gateway_for(self._text_provider(), api_key)

    def _create_text_service(self, api_key: str) -> ChatStreamService:
        """Capture settings and construct the gateway on the UI thread.

        SettingsRepository owns the SQLite connection created by the UI
        thread.  A deferred factory must not call back into it from a QThread.
        The resulting HTTP gateway itself is safe to consume in the worker.
        """

        gateway = self._create_text_gateway(api_key)
        return ChatStreamService(lambda: gateway)

    def _requested_aux_text_provider(self) -> str:
        provider = self._settings.get("aux_text_provider", "inherit").lower()
        return provider if provider in {"inherit", "deepseek", "grsai"} else "inherit"

    def _resolved_aux_text_provider(self) -> str:
        requested = self._requested_aux_text_provider()
        main_provider = self._text_provider()
        if requested == "inherit":
            return main_provider
        if self._text_api_key_for(requested):
            return requested
        return main_provider

    def _aux_text_api_key(self) -> str:
        return self._text_api_key_for(self._resolved_aux_text_provider())

    def _aux_grsai_model(self) -> str:
        requested = self._requested_aux_text_provider()
        resolved = self._resolved_aux_text_provider()
        if requested == "grsai" and resolved == "grsai":
            auxiliary = self._settings.get(
                "aux_grsai_text_model", ""
            ).strip()
            if auxiliary:
                return auxiliary
        return (
            self._settings.get(
                "grsai_text_model", DEFAULT_GRSAI_TEXT_MODEL
            ).strip()
            or DEFAULT_GRSAI_TEXT_MODEL
        )

    def _aux_text_model(self, conversation_model: str) -> str:
        requested = self._requested_aux_text_provider()
        resolved = self._resolved_aux_text_provider()
        if requested != "inherit" and requested == resolved:
            if resolved == "deepseek":
                return (
                    resolve_model(
                        self._settings.get("aux_deepseek_model", MODEL_CHAT)
                    )
                    or MODEL_CHAT
                )
            return MODEL_CHAT
        return conversation_model or MODEL_CHAT

    def _create_aux_text_service(
        self,
        api_key: str,
        *,
        timeout_seconds: int | None = None,
    ) -> ChatStreamService:
        provider = self._resolved_aux_text_provider()
        gateway = self._create_text_gateway_for(
            provider,
            api_key,
            grsai_model=(self._aux_grsai_model() if provider == "grsai" else ""),
            timeout_seconds=timeout_seconds,
        )
        return ChatStreamService(lambda: gateway)

    def _aux_sampling_options(
        self, model: str, temperature: float
    ) -> dict[str, float]:
        provider = self._resolved_aux_text_provider()
        capabilities = self._text_capabilities(
            provider,
            model,
            grsai_model=(self._aux_grsai_model() if provider == "grsai" else ""),
        )
        return safe_sampling_options(capabilities, temperature=temperature)

    def _main_sampling_options(
        self, model: str, temperature: float
    ) -> dict[str, float]:
        capabilities = self._text_capabilities(self._text_provider(), model)
        return safe_sampling_options(capabilities, temperature=temperature)

    def _image_provider(self) -> str:
        provider = self._settings.get("image_provider", "siliconflow").lower()
        return provider if provider in {"siliconflow", "grsai"} else "siliconflow"

    def _image_api_key(self) -> str:
        if self._image_provider() == "grsai":
            return self._credential_value("get_grsai_image_api_key")
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
        role_state: dict | None = None,
        trigger: str = "semantic",
    ) -> bool:
        if self._image_runner is not None:
            return self._image_runner.enqueue(
                conversation_id,
                turn_id,
                character_name,
                character_card,
                answer,
                fallback_prompt=fallback_prompt,
                segment_index=segment_index,
                role_state=role_state,
                trigger=trigger,
            )
        return False

    def _on_autonomous_image_saved(self, conversation_id: str) -> None:
        """发图成功后的主窗口侧 UI 动作。"""

        self._schedule_sync()
        self.conversations.refresh(select_id=self._conversation_id)
        if (
            conversation_id == self._conversation_id
            and not (self._flow is not None and self._flow.busy)
        ):
            self._open_conversation(conversation_id, force_reload=True)
        self._play_notification()
        if not self.isActiveWindow():
            QApplication.alert(self, 5_000)

    def _on_autonomous_image_error(
        self, conversation_id: str, error_code: str
    ) -> None:
        if (
            conversation_id == self._conversation_id
            and not (self._flow is not None and self._flow.busy)
        ):
            self._open_conversation(conversation_id, force_reload=True)

    def _retry_image_event(self, turn_id: str, event_id: str) -> None:
        """按持久化图片事件重试，不重新调用角色正文模型。"""

        conversation_id = self._conversation_id or ""
        conversation = self._chats.get_conversation(conversation_id)
        turn = self._chats.get_turn(conversation_id, turn_id)
        character = (
            self._characters.get(conversation.character_id)
            if conversation is not None and conversation.character_id
            else None
        )
        if turn is None or character is None:
            return
        segments = deserialize_reply_segments(turn.assistant_segments_json)
        selected = next(
            (
                (index, segment)
                for index, segment in enumerate(segments)
                if segment.kind == "image"
                and (not event_id or segment.event_id == event_id)
            ),
            None,
        )
        if selected is None:
            return
        index, segment = selected
        try:
            self._chats.set_assistant_image_status(
                turn_id, "pending", segment_index=index
            )
        except (KeyError, ValueError):
            return
        queued = self._enqueue_autonomous_image(
            conversation_id,
            turn_id,
            character.name,
            character.card,
            turn.assistant_content,
            fallback_prompt=segment.prompt,
            segment_index=index,
            role_state=self._role_state(conversation),
            trigger="retry",
        )
        if not queued:
            current_turn = self._chats.get_turn(conversation_id, turn_id)
            current_segments = (
                deserialize_reply_segments(current_turn.assistant_segments_json)
                if current_turn is not None
                else ()
            )
            current_image = (
                current_segments[index] if index < len(current_segments) else None
            )
            if current_image is not None and current_image.status == "pending":
                with suppress(KeyError, ValueError):
                    self._chats.set_assistant_image_status(
                        turn_id,
                        "failed",
                        (
                            "image_authentication"
                            if not self._image_api_key()
                            else "image_service_error"
                        ),
                        segment_index=index,
                    )
        self._open_conversation(conversation_id, force_reload=True)

    def _on_flow_image_described(self, turn_id: str, description: str) -> None:
        """图片理解结果落库；空描述（服务异常时）容错忽略。"""

        if not turn_id:
            return
        started = monotonic()
        try:
            self._chats.set_user_image_description(turn_id, description)
        except ValueError:
            self._record_chat_diagnostic(
                "image_description_persisted",
                outcome="error",
                error_code="turn_not_found",
                duration_ms=(monotonic() - started) * 1000,
            )
            return
        self._record_chat_diagnostic(
            "image_description_persisted",
            duration_ms=(monotonic() - started) * 1000,
            details={"output_characters": len(description)},
        )
        self._schedule_sync()

    def _on_flow_image_analysis_failed(self, error_code: str) -> None:
        self.chat_page.add_image_analysis_error(error_code)

    def _on_turn_completed(
        self,
        conversation_id: str,
        turn_id: str,
        answer: str,
        reasoning: str,
        request_kind: str,
    ) -> None:
        if request_kind == "proactive":
            conversation = self._chats.get_conversation(conversation_id)
            character_id = (
                conversation.character_id if conversation is not None else ""
            )
            recent_proactive = [
                turn.assistant_content
                for turn in self._chats.list_turns(conversation_id)
                if turn.id != turn_id
                and turn.origin == "proactive"
                and turn.status == "completed"
                and turn.assistant_content
            ][-5:]
            if is_repetitive_proactive_message(answer, recent_proactive):
                self._chats.delete_turn(turn_id)
                if character_id:
                    self._settings.set(
                        f"proactive_last_status_{character_id}",
                        "本次生成内容与近期主动消息过于相似，已在发送前拦截。",
                    )
                self._record_chat_diagnostic(
                    "proactive_repeat_suppressed",
                    outcome="cancelled",
                    request_kind=request_kind,
                )
                self._schedule_sync()
                return
        classification_started = monotonic()
        plan = assign_image_events(classify_role_reply(answer), turn_id)
        visible_answer = plan.visible_text or answer.strip()
        self._record_chat_diagnostic(
            "reply_classified",
            duration_ms=(monotonic() - classification_started) * 1000,
            request_kind=request_kind,
            details={"segment_count": len(plan.segments)},
        )
        persistence_started = monotonic()
        try:
            self._chats.complete_turn(
                turn_id,
                visible_answer,
                reasoning,
                assistant_segments_json=serialize_reply_segments(
                    plan.segments
                ),
            )
        except KeyError:
            # 会话在流式生成期间被删除（外键级联删除了轮次）：
            # 放弃本轮投递，避免在排队槽中抛出未捕获异常。
            self._record_chat_diagnostic(
                "turn_persisted",
                outcome="error",
                error_code="turn_not_found",
                duration_ms=(monotonic() - persistence_started) * 1000,
                request_kind=request_kind,
            )
            return
        self._record_chat_diagnostic(
            "turn_persisted",
            duration_ms=(monotonic() - persistence_started) * 1000,
            request_kind=request_kind,
            details={"output_characters": len(visible_answer)},
        )
        if request_kind == "opening":
            # AI 开场成功：清空模板开场白，避免重开会话/后续历史与 AI
            # 生成的首条消息重复。
            self._pending_opening_conversation_id = None
            self._opening_fallbacks.pop(conversation_id, "")
            self._chats.set_opening_message(conversation_id, "")
        self._schedule_sync()
        self._enqueue_summary(conversation_id, visible_answer, turn_id=turn_id)
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
                    fallback_prompt = segment.prompt
                    image_segment_index = index
                    break
        turn = self._chats.get_turn(conversation_id, turn_id)
        explicit_prompt = explicit_image_request_prompt(
            turn.user_content if turn is not None else ""
        )
        if character is not None and explicit_prompt and not fallback_prompt:
            fallback_prompt = explicit_prompt
        if character is not None:
            trigger = (
                "explicit"
                if explicit_prompt
                else "role_action"
                if image_segment_index is not None
                else "semantic"
            )
            queued = self._enqueue_autonomous_image(
                conversation_id,
                turn_id,
                character.name,
                character.card,
                visible_answer,
                fallback_prompt=fallback_prompt,
                segment_index=image_segment_index,
                role_state=self._role_state(conversation),
                trigger=trigger,
            )
            if image_segment_index is not None and not queued:
                current_turn = self._chats.get_turn(conversation_id, turn_id)
                current_segments = (
                    deserialize_reply_segments(
                        current_turn.assistant_segments_json
                    )
                    if current_turn is not None
                    else ()
                )
                current_image = (
                    current_segments[image_segment_index]
                    if image_segment_index < len(current_segments)
                    else None
                )
                if current_image is not None and current_image.status == "pending":
                    with suppress(KeyError, ValueError):
                        self._chats.set_assistant_image_status(
                            turn_id,
                            "failed",
                            (
                                "image_authentication"
                                if not self._image_api_key()
                                else "image_service_error"
                            ),
                            segment_index=image_segment_index,
                        )
        profile = read_tts_profile(character.card if character else None)
        self._flow.prepare_delivery(
            ReplyDelivery(
                conversation_id,
                turn_id,
                plan,
                profile,
                reasoning,
                request_kind,
            )
        )

    def _on_turn_aborted(
        self, turn_id: str, request_kind: str, error_code: str
    ) -> None:
        if not turn_id:
            return
        started = monotonic()
        if request_kind in {"proactive", "opening"}:
            # 主动/开场消息失败不留失败气泡；开场失败由 _on_stream_cleaned_up
            # 回退到角色模板。
            self._chats.delete_turn(turn_id)
        elif error_code:
            self._chats.fail_turn(turn_id, "failed", error_code)
        else:
            self._chats.fail_turn(turn_id, "cancelled")
        self._record_chat_diagnostic(
            "turn_persisted",
            outcome="error" if error_code else "cancelled",
            error_code=error_code,
            duration_ms=(monotonic() - started) * 1000,
            request_kind=request_kind,
        )
        self._schedule_sync()

    def _on_stream_cleaned_up(
        self,
        request_kind: str,
        request_conversation_id: str,
        pending_delivery: ReplyDelivery | None,
    ) -> None:
        self.conversations.refresh(select_id=self._conversation_id)
        pending_switch = self._pending_conversation_switch
        pending_send = self._pending_send
        self._pending_conversation_switch = None
        self._pending_send = None
        if pending_switch is not None or pending_send is not None:
            self.chat_page.discard_stream()
            self.chat_page.finish_stream()
            if request_kind == "opening" and pending_delivery is None:
                self._pending_opening_conversation_id = None
                fallback = self._opening_fallbacks.pop(
                    request_conversation_id or "", ""
                )
                self._ensure_opening_fallback(
                    request_conversation_id, fallback
                )
            if pending_switch is not None:
                conversation_id, force_reload = pending_switch
                self._open_conversation(
                    conversation_id, force_reload=force_reload
                )
            elif self._conversation_id:
                self._open_conversation(
                    self._conversation_id, force_reload=True
                )
            if pending_send is not None:
                QTimer.singleShot(
                    0,
                    lambda values=pending_send: self._send(*values),
                )
            return
        if pending_delivery is not None:
            self.chat_page.discard_stream()
            if pending_delivery.conversation_id == self._conversation_id:
                self._flow.begin_delivery(pending_delivery)
            else:
                self.chat_page.finish_stream()
                self._flow.play_pending_notification()
                if (
                    pending_delivery.request_kind == "proactive"
                    and not self.isActiveWindow()
                ):
                    QApplication.alert(self, 5_000)
            return
        # 失败/取消没有投递计划时也必须先把等待气泡从布局中移除；仅调用
        # finish_stream 会丢失引用但留下空白气泡，开场模板因此不能立即显示。
        self.chat_page.discard_stream()
        self.chat_page.finish_stream()
        if request_kind == "opening":
            # 开场请求失败：回退到角色模板开场白，保证新会话有内容可看。
            self._pending_opening_conversation_id = None
            fallback = self._opening_fallbacks.pop(
                request_conversation_id or "", ""
            )
            self._ensure_opening_fallback(request_conversation_id, fallback)
        if (
            request_conversation_id
            and request_conversation_id == self._conversation_id
        ):
            self._open_conversation(
                request_conversation_id, force_reload=True
            )
        if request_kind == "proactive" and not self.isActiveWindow():
            QApplication.alert(self, 5_000)

    def _ensure_opening_fallback(
        self, conversation_id: str, fallback: str
    ) -> None:
        """确保会话有开场白兜底文本（模板），供失败后重开显示。"""

        if not conversation_id or not fallback:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is not None and not conversation.opening_message:
            self._chats.set_opening_message(conversation_id, fallback)

    def _on_delivery_started(self, delivery: ReplyDelivery) -> None:
        self._active_delivery = delivery
        self.chat_page.set_generating(True)
        self.chat_page.discard_stream()

    def _on_delivery_typing(self, show: bool) -> None:
        if show:
            self.chat_page.show_typing_indicator()

    def _on_delivery_segment(
        self, index: int, segment, reasoning: str
    ) -> None:
        delivery = self._active_delivery
        self.chat_page.discard_stream()
        if segment.kind == "image":
            current_segment = segment
            if delivery is not None:
                turn = self._chats.get_turn(
                    delivery.conversation_id, delivery.turn_id
                )
                segments = (
                    deserialize_reply_segments(turn.assistant_segments_json)
                    if turn is not None
                    else ()
                )
                if index < len(segments):
                    current_segment = segments[index]
            if current_segment.image_path:
                self.chat_page.add_image_event(
                    image_path=current_segment.image_path
                )
            elif current_segment.status in {"failed", "cancelled"}:
                self.chat_page.add_image_error(current_segment.error_code)
            else:
                self.chat_page.add_image_event(pending=True)
            return
        self.chat_page.add_assistant_segment(
            segment.text,
            message_key=(
                f"turn:{delivery.turn_id}:segment:{index}"
                if delivery is not None
                else ""
            ),
            speech_enabled=segment.kind == "dialogue",
            narration=segment.kind == "narration",
            reasoning=reasoning,
        )

    def _on_delivery_speech(
        self, message_key: str, text: str, profile: TtsProfile
    ) -> None:
        if self._speech is not None:
            enqueue = getattr(self._speech, "enqueue", None)
            if callable(enqueue):
                enqueue(message_key, text, profile)
            else:
                self._speech.speak(message_key, text, profile)

    def _on_delivery_finished(self, delivery: ReplyDelivery) -> None:
        self._active_delivery = None
        self.chat_page.discard_stream()
        self.chat_page.finish_stream()
        self.conversations.refresh(select_id=self._conversation_id)
        if delivery.conversation_id == self._conversation_id:
            turn = self._chats.get_turn(
                delivery.conversation_id, delivery.turn_id
            )
            if turn is not None and (
                turn.assistant_image_path or delivery.plan.has_image_action
            ):
                self._open_conversation(
                    delivery.conversation_id, force_reload=True
                )
        if delivery.request_kind == "proactive" and not self.isActiveWindow():
            QApplication.alert(self, 5_000)

    def _generate_random_character(self) -> None:
        """随机时刻请求一位新角色；成功前不修改联系人或消耗每日名额。"""

        runner = self._character_discovery_runner
        if (
            self._shutting_down
            or runner is None
            or runner.busy
            or not self._character_discovery.enabled
            or not self._character_discovery.quota_available()
        ):
            return
        profiles = tuple(
            (
                character.name,
                str(character.card.get("data", {}).get("personality", "")),
            )
            for character in self._characters.list()
        )
        runner.generate(
            profiles,
            user_name=self._settings.get("user_name", "用户"),
            user_persona=self._settings.get("user_persona", ""),
            desired_gender=self._character_discovery.choose_gender(),
        )

    def _on_random_character_generated(self, card: dict) -> None:
        """保存新角色，并以其首条消息建立一个不抢占当前界面的联系人会话。"""

        if self._shutting_down:
            return
        name = str(card.get("data", {}).get("name", "")).strip()
        name_key = "".join(name.split()).casefold()
        if not name_key or any(
            "".join(item.name.split()).casefold() == name_key
            for item in self._characters.list()
        ):
            self._on_random_character_error("duplicate_character")
            return
        character = self._characters.create(card, source_type="ai_generated")
        opening = str(character.card["data"].get("first_mes", "")).strip()
        model = (
            self._settings.get("default_model")
            or self.settings_page.default_model.currentData()
        )
        self._chats.create_conversation(
            model,
            title=character.name,
            character_id=character.id,
            opening_message=opening,
        )
        self._schedule_sync()
        self._character_discovery.record_generated()
        self._settings.set("character_discovery_last_error", "")
        self._settings.set("character_discovery_last_name", character.name)
        if (
            self._character_avatar_runner is None
            or not self._character_avatar_runner.enqueue(
                character.id, character.card
            )
        ):
            self._settings.set(
                "character_discovery_avatar_last_error",
                "image_api_key_missing",
            )
        self.characters_page.refresh()
        self.conversations.refresh(select_id=self._conversation_id)
        self._play_notification()
        if not self.isActiveWindow():
            QApplication.alert(self, 5_000)

    def _on_random_character_error(self, error_code: str) -> None:
        """静默记录后台失败；下一随机周期可重试且不占每日名额。"""

        if not self._shutting_down:
            self._settings.set(
                "character_discovery_last_error",
                str(error_code or "character_generation_failed")[:120],
            )

    @staticmethod
    def _is_discovered_character(card: dict) -> bool:
        data = card.get("data", {}) if isinstance(card, dict) else {}
        extensions = (
            data.get("extensions", {}) if isinstance(data, dict) else {}
        )
        app = (
            extensions.get("deepseek_chat", {})
            if isinstance(extensions, dict)
            else {}
        )
        return bool(
            isinstance(app, dict)
            and app.get("generated") is True
            and app.get("source") == "character_discovery"
        )

    def _enqueue_missing_generated_avatars(self) -> None:
        """为当前及旧版本创建的无头像随机角色补齐头像。"""

        runner = self._character_avatar_runner
        if runner is None or not self._image_api_key():
            return
        for character in self._characters.list():
            if (
                not character.avatar_path
                and self._is_discovered_character(character.card)
            ):
                runner.enqueue(character.id, character.card)

    def _on_random_character_avatar_generated(
        self, character_id: str, avatar_path: str
    ) -> None:
        """仅为空头像落库，避免覆盖用户生成期间手动选择的新头像。"""

        character = self._characters.get(character_id)
        if self._shutting_down or character is None or character.avatar_path:
            with suppress(OSError):
                Path(avatar_path).unlink(missing_ok=True)
            return
        self._characters.update(character.id, character.card, avatar_path)
        self._schedule_sync()
        self._settings.set("character_discovery_avatar_last_error", "")
        self._settings.set(
            "character_discovery_avatar_last_name", character.name
        )
        self.characters_page.refresh()
        self.conversations.refresh(select_id=self._conversation_id)
        if self._conversation_id:
            self._open_conversation(self._conversation_id, force_reload=True)

    def _on_random_character_avatar_error(
        self, character_id: str, error_code: str
    ) -> None:
        """记录头像失败但保留角色；下次启动或更新凭据时可再次补齐。"""

        if not self._shutting_down:
            self._settings.set(
                "character_discovery_avatar_last_error",
                str(error_code or "character_avatar_generation_failed")[:120],
            )

    def _send_proactive_message(self) -> None:
        """让当前角色会话在随机计时到期后主动开启话题。"""

        if (self._flow is not None and self._flow.busy) or (
            self._conversation_id is None
        ):
            return
        decision = self._proactive_decision(self._conversation_id)
        if decision is None or not decision.allowed:
            return
        if self._sync is not None and self._sync.enabled:
            self._pending_proactive[self._conversation_id] = decision
            self._sync.claim_proactive(
                self._conversation_id,
                event_id=decision.event_id,
                ttl_seconds=decision.lease_ttl_seconds,
            )
            return
        self._begin_proactive_message(self._conversation_id, decision)

    def _on_proactive_claimed(
        self, conversation_id: str, acquired: bool
    ) -> None:
        if acquired:
            decision = self._pending_proactive.pop(conversation_id, None)
            self._begin_proactive_message(conversation_id, decision)
        else:
            self._pending_proactive.pop(conversation_id, None)

    def _proactive_decision(
        self,
        conversation_id: str,
        *,
        current_time: datetime | None = None,
    ) -> ProactiveDecision | None:
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None or not conversation.character_id:
            return None
        character = self._characters.get(conversation.character_id)
        if character is None:
            return None
        decision = evaluate_proactive_message(
            relationship_policy_for(self._settings, character.id),
            self._chats.list_turns(conversation_id),
            self._role_state(conversation),
            globally_enabled=self._settings.get_bool(
                "proactive_enabled", False
            ),
            conversation_id=conversation_id,
            current_time=current_time,
        )
        self._settings.set(
            f"proactive_last_status_{character.id}",
            decision.explanation,
        )
        return decision

    def _begin_proactive_message(
        self,
        conversation_id: str,
        decision: ProactiveDecision | None = None,
    ) -> None:
        """租约确认后生成主动消息；切换会话或管线占线时放弃。"""

        if (
            not conversation_id
            or conversation_id != self._conversation_id
            or (self._flow is not None and self._flow.busy)
        ):
            return
        api_key = self._text_api_key()
        if not api_key:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None or not conversation.character_id:
            return
        character = self._characters.get(conversation.character_id)
        if character is None:
            return
        current_time = datetime.now().astimezone()
        current_decision = self._proactive_decision(
            conversation_id, current_time=current_time
        )
        if current_decision is None or not current_decision.allowed:
            return
        if decision is not None and decision.event_id != current_decision.event_id:
            return
        decision = current_decision
        context_started = monotonic()

        role_memory_enabled = self._role_memory_enabled(character.id)
        role_state = self._role_state(conversation)
        history = self._chats.completed_history(
            conversation.id, max_turns=ROLEPLAY_RECENT_TURNS
        )
        recalled_memories = (
            self._chats.recalled_memories(
                conversation.id,
                roleplay_memory_query("", role_state),
                exclude_recent_turns=ROLEPLAY_RECENT_TURNS,
            )
            if role_memory_enabled
            else ()
        )
        policy = relationship_policy_for(self._settings, character.id)
        character_prompt = build_character_prompt(
            character.card,
            history,
            "",
            user_name=self._settings.get("user_name", "用户"),
            user_persona=self._settings.get("user_persona", ""),
            role_state=role_state,
            recalled_memories=recalled_memories,
            current_time=current_time,
        )
        turn = self._chats.create_proactive_turn(
            conversation.id, conversation.model
        )
        self._chats.mark_streaming(turn.id)
        self._schedule_sync()
        self.chat_page.add_assistant_stream()
        self.chat_page.set_generating(True)
        system_prompt = "\n\n".join(
            part
            for part in (
                character_prompt.system,
                relationship_policy_prompt(policy),
                PROACTIVE_SYSTEM_SUFFIX,
            )
            if part.strip()
        )
        recent_proactive = [
            turn.assistant_content
            for turn in self._chats.list_turns(conversation.id)
            if turn.origin == "proactive"
            and turn.status == "completed"
            and turn.assistant_content
        ][-3:]
        self._flow.begin_stream(
            service=self._create_text_service(api_key),
            model=conversation.model,
            history=history,
            request_text=proactive_request(
                character.name,
                current_time=current_time,
            )
            + "\n"
            + proactive_context_text(
                policy,
                role_state,
                recent_proactive,
                decision.explanation,
            ),
            system_prompt=system_prompt,
            example_messages=character_prompt.examples,
            post_history_prompt=character_prompt.post_history,
            provider=self._text_provider(),
            image_provider=self._image_provider(),
            context_duration_ms=(monotonic() - context_started) * 1000,
            turn_id=turn.id,
            conversation_id=conversation.id,
            request_kind="proactive",
            **self._roleplay_sampling_options(conversation.model),
        )

    def _on_sync_data_changed(self) -> None:
        """远端增量落库后刷新列表；生成期间不重载当前气泡。"""

        if self._shutting_down:
            return
        self.characters_page.refresh()
        self.conversations.refresh(select_id=self._conversation_id)
        if self._busy_generating():
            return
        if self._conversation_id and self._chats.get_conversation(
            self._conversation_id
        ):
            self._open_conversation(self._conversation_id, force_reload=True)
            return
        conversations = self._chats.list_conversations()
        if conversations:
            self.conversations.select(conversations[0].id)
            self._open_conversation(conversations[0].id, force_reload=True)

    def _send_ai_opening(
        self,
        conversation: Conversation,
        fallback_opening: str,
    ) -> None:
        """新建角色会话后由 AI 主动生成首条开场白。

        无 API Key、角色缺失或管线占线时直接回退到角色模板（fallback_opening）；
        请求失败（流式错误）由 _on_stream_cleaned_up 回退；成功由
        _on_turn_completed 清空模板，避免与 AI 生成的首条重复。
        """

        if self._flow is None or self._flow.busy:
            self._recover_opening(conversation.id, fallback_opening)
            return
        api_key = self._text_api_key()
        if not api_key:
            self._recover_opening(conversation.id, fallback_opening)
            return
        character = self._characters.get(conversation.character_id)
        if character is None:
            self._recover_opening(conversation.id, fallback_opening)
            return
        context_started = monotonic()

        # 模板开场白只用于失败兜底，绝不能作为已经发生的 assistant 历史
        # 传给模型；否则模型会误以为自己已经开过场并引用不存在的上下文。
        history = ()
        current_time = datetime.now().astimezone()
        role_memory_enabled = self._role_memory_enabled(character.id)
        role_state = self._role_state(conversation)
        recalled_memories = (
            self._chats.recalled_memories(
                conversation.id,
                roleplay_memory_query("", role_state),
                exclude_recent_turns=ROLEPLAY_RECENT_TURNS,
            )
            if role_memory_enabled
            else ()
        )
        character_prompt = build_character_prompt(
            character.card,
            history,
            "",
            user_name=self._settings.get("user_name", "用户"),
            user_persona=self._settings.get("user_persona", ""),
            role_state=role_state,
            recalled_memories=recalled_memories,
            current_time=current_time,
        )
        turn = self._chats.create_proactive_turn(
            conversation.id, conversation.model, origin="opening"
        )
        self._chats.mark_streaming(turn.id)
        self._schedule_sync()
        self.chat_page.add_assistant_stream()
        self.chat_page.set_generating(True)
        system_prompt = "\n\n".join(
            part
            for part in (
                character_prompt.system,
                relationship_policy_prompt(
                    relationship_policy_for(
                        self._settings, character.id
                    )
                ),
                OPENING_SYSTEM_SUFFIX,
            )
            if part.strip()
        )
        self._flow.begin_stream(
            service=self._create_text_service(api_key),
            model=conversation.model,
            history=history,
            request_text=opening_request(
                character.name, current_time=current_time
            ),
            system_prompt=system_prompt,
            example_messages=character_prompt.examples,
            post_history_prompt=character_prompt.post_history,
            provider=self._text_provider(),
            image_provider=self._image_provider(),
            context_duration_ms=(monotonic() - context_started) * 1000,
            turn_id=turn.id,
            conversation_id=conversation.id,
            request_kind="opening",
            **self._roleplay_sampling_options(conversation.model),
        )

    def _recover_opening(self, conversation_id: str, fallback: str) -> None:
        """AI 开场不可用（无 key/角色缺失）时回退到角色模板开场白。"""

        self._pending_opening_conversation_id = None
        self._opening_fallbacks.pop(conversation_id, "")
        self._ensure_opening_fallback(conversation_id, fallback)
        self._schedule_sync()
        if conversation_id == self._conversation_id:
            self._open_conversation(conversation_id, force_reload=True)

    def _enqueue_summary(
        self, conversation_id: str, answer: str, *, turn_id: str = ""
    ) -> None:
        if self._summary_runner is not None:
            self._summary_runner.enqueue(
                conversation_id, answer, turn_id=turn_id
            )

    def _on_summary_refreshed(self) -> None:
        self.conversations.refresh(select_id=self._conversation_id)
        self._schedule_sync()

    def _schedule_sync(self) -> None:
        if self._sync is not None:
            self._sync.schedule_sync()

    def _record_chat_diagnostic(
        self,
        stage: str,
        *,
        outcome: str = "ok",
        error_code: str = "",
        duration_ms: float | None = None,
        request_kind: str = "user",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        conversation = (
            self._chats.get_conversation(self._conversation_id)
            if self._conversation_id
            else None
        )
        self._diagnostics.record(
            "text_chat",
            stage,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            provider=self._text_provider(),
            model=conversation.model if conversation is not None else "",
            request_kind=request_kind,
            task_id=(
                self._flow.diagnostic_task_id if self._flow is not None else ""
            ),
            details=details,
        )

    def _enqueue_pending_summaries(self) -> None:
        if self._summary_runner is not None:
            self._summary_runner.enqueue_pending()

    def _roleplay_temperature(self) -> float:
        try:
            value = float(
                self._settings.get("roleplay_temperature", "1.3")
            )
        except ValueError:
            return 1.3
        return max(0.0, min(value, 2.0))

    def _roleplay_sampling_options(self, model: str) -> dict[str, float]:
        capabilities = self._text_capabilities(self._text_provider(), model)
        mode = self._settings.get(
            "roleplay_sampling_mode", "temperature"
        ).lower()
        if mode == "provider_default":
            return {}
        if mode == "top_p":
            try:
                top_p = float(self._settings.get("roleplay_top_p", "0.9"))
            except ValueError:
                top_p = 0.9
            return safe_sampling_options(capabilities, top_p=top_p)
        return safe_sampling_options(
            capabilities, temperature=self._roleplay_temperature()
        )

    def _roleplay_director_request(
        self,
        conversation: Conversation,
        card: dict,
        history,
        user_text: str,
        role_state: dict,
    ) -> DirectorRequest | None:
        """只为高价值用户轮次构造一次隐藏辅助规划请求。"""

        if not self._settings.get_bool("roleplay_director_enabled", False):
            return None
        if not self._settings.get_bool(
            f"roleplay_director_conversation_{conversation.id}", True
        ):
            return None
        try:
            max_extra_calls = int(
                self._settings.get("roleplay_director_max_extra_calls", "1")
            )
            threshold = int(
                self._settings.get("roleplay_director_threshold", "6")
            )
            timeout_seconds = int(
                self._settings.get("roleplay_director_timeout_seconds", "8")
            )
        except ValueError:
            return None
        if max_extra_calls < 1:
            return None
        decision = assess_director_trigger(user_text, role_state)
        if not decision.should_trigger(threshold):
            return None
        api_key = self._aux_text_api_key()
        if not api_key:
            return None
        timeout_seconds = max(3, min(timeout_seconds, 20))
        model = self._aux_text_model(conversation.model)
        try:
            service = self._create_aux_text_service(
                api_key,
                timeout_seconds=timeout_seconds,
            )
        except Exception:
            return None
        sampling = self._aux_sampling_options(model, 0.2)
        return DirectorRequest(
            service=service,
            model=model,
            request_text=build_director_request_text(
                card,
                history,
                user_text,
                role_state=role_state,
            ),
            timeout_seconds=timeout_seconds,
            temperature=sampling.get("temperature"),
            top_p=sampling.get("top_p"),
            trigger_reasons=decision.reasons,
        )

    def _role_state(self, conversation) -> dict:
        if not self._role_memory_enabled(conversation.character_id or ""):
            return {}
        try:
            state = json.loads(conversation.role_state_json or "{}")
        except (TypeError, ValueError):
            return {}
        return state if isinstance(state, dict) else {}

    def _role_memory_enabled(self, character_id: str = "") -> bool:
        if not self._settings.get_bool("role_memory_enabled", True):
            return False
        if character_id:
            return self._settings.get_bool(
                f"role_memory_character_{character_id}", True
            )
        return True

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
        enabled = self._settings.get_bool(
            "notification_sound_enabled", True
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
        if self._flow is not None:
            self._flow.stop()

    def _change_model(self, model: str) -> None:
        if self._conversation_id:
            self._chats.set_model(self._conversation_id, model)
            self._schedule_sync()
            self.conversations.refresh(select_id=self._conversation_id)

    def _edit_current(self) -> None:
        if self._conversation_id:
            self._edit_conversation(self._conversation_id)

    def _manage_current_memory(self) -> None:
        if not self._conversation_id or self._busy_generating():
            return
        dialog = MemoryManagerDialog(
            self._chats,
            self._settings,
            self._conversation_id,
            self,
        )
        dialog.exec()
        self._schedule_sync()

    def _edit_conversation(self, conversation_id: str) -> None:
        if self._flow is not None and self._flow.busy:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None:
            return
        dialog = ConversationEditDialog(
            conversation,
            self._characters.list(),
            self,
            director_enabled=self._settings.get_bool(
                f"roleplay_director_conversation_{conversation.id}", True
            ),
            director_available=self._settings.get_bool(
                "roleplay_director_enabled", False
            ),
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
        self._settings.set(
            f"roleplay_director_conversation_{conversation.id}",
            "true" if dialog.director.isChecked() else "false",
        )
        self._schedule_sync()
        self.conversations.refresh(select_id=conversation.id)
        self._open_conversation(conversation.id, force_reload=True)

    def _delete_current(self) -> None:
        if not self._conversation_id:
            return
        if self._busy_generating():
            QMessageBox.information(
                self, "正在生成", "请等待当前回复完成后删除会话。"
            )
            return
        answer = QMessageBox.question(
            self,
            "删除会话",
            "确定删除当前会话及全部消息吗？此操作无法撤销。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._chats.delete_conversation(self._conversation_id)
        self._schedule_sync()
        self._conversation_id = None
        self.conversations.refresh()
        remaining = self._chats.list_conversations()
        if remaining:
            self.conversations.select(remaining[0].id)
        else:
            self._new_conversation()

    def _clear_all(self) -> None:
        if self._busy_generating():
            QMessageBox.information(
                self, "正在生成", "请等待当前回复完成后清空会话。"
            )
            return
        answer = QMessageBox.question(
            self,
            "清空全部会话",
            "确定清空所有本地会话吗？此操作无法撤销。",
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._chats.clear_all()
        self._schedule_sync()
        self._conversation_id = None
        self.conversations.refresh()
        self._new_conversation()

    def _busy_generating(self) -> bool:
        """主消息管线或分段投递进行中；这些状态下删除会话会破坏落库。"""

        return self._flow is not None and self._flow.busy

    def _credentials_updated(self) -> None:
        if self._text_api_key() and not self._chats.list_conversations():
            self._new_conversation()
        self._enqueue_pending_summaries()
        self._enqueue_missing_generated_avatars()

    def _apply_theme(self, value: str) -> None:
        if value == "system":
            scheme = QApplication.styleHints().colorScheme()
            dark = scheme == Qt.ColorScheme.Dark
        else:
            dark = value == "dark"
        QApplication.instance().setStyleSheet(
            stylesheet(dark, mobile=self._mobile)
        )

    def shutdown(self) -> None:
        """幂等停止所有计时器和后台线程，供窗口关闭与应用退出共用。"""

        if self._shutting_down:
            return
        self._shutting_down = True
        self._pending_conversation_switch = None
        self._pending_send = None
        self._pending_proactive.clear()
        self._proactive.stop()
        self._character_discovery.stop()
        self.settings_page.shutdown_model_refresh()
        if self._notification_sound is not None:
            self._notification_sound.shutdown()
        if self._speech is not None:
            self._speech.shutdown()
        if self._flow is not None:
            self._flow.shutdown()
        if self._summary_runner is not None:
            self._summary_runner.shutdown()
        if self._character_discovery_runner is not None:
            self._character_discovery_runner.shutdown()
        if self._character_avatar_runner is not None:
            self._character_avatar_runner.shutdown()
        if self._image_runner is not None:
            self._image_runner.shutdown()
        if self._sync is not None:
            self._sync.shutdown()

    def closeEvent(self, event: QCloseEvent) -> None:
        self.shutdown()
        event.accept()
