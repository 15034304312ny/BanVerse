from __future__ import annotations

import json

from PySide6.QtCore import QCoreApplication, QDateTime, QPointF, Qt, QUrl
from PySide6.QtGui import QGuiApplication, QImage, QScrollEvent
from PySide6.QtWidgets import (
    QApplication,
    QDialog,
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QScroller,
    QScrollerProperties,
    QVBoxLayout,
    QWidget,
)

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import (
    Character,
    CharacterRepository,
    ChatRepository,
    Conversation,
    SettingsRepository,
)
from deepseek_cli.desktop.model_discovery import ProviderModel, serialize_models
from deepseek_cli.desktop.stickers import STICKERS
from deepseek_cli.desktop.ui.character_editor_dialog import (
    CharacterEditorDialog,
)
from deepseek_cli.desktop.ui.conversation_edit_dialog import (
    ConversationEditDialog,
)
from deepseek_cli.desktop.ui.file_dialogs import open_mobile_file_dialog
from deepseek_cli.desktop.ui.memory_manager_dialog import MemoryManagerDialog
from deepseek_cli.desktop.ui.mobile import enable_touch_scrolling
from deepseek_cli.desktop.ui.pages.characters_page import CharacterRow
from deepseek_cli.desktop.ui.pages.chat_page import ChatPage
from deepseek_cli.desktop.ui.pages.conversations_page import ConversationRow
from deepseek_cli.desktop.ui.pages.settings_page import SettingsPage
from deepseek_cli.desktop.ui.relationship_policy_dialog import (
    RelationshipPolicyDialog,
)
from deepseek_cli.desktop.ui.widgets.chat_composer import ChatComposer
from deepseek_cli.desktop.ui.widgets.message_bubble import MessageBubble
from deepseek_cli.desktop.ui.widgets.sticker_picker import StickerPickerDialog
from deepseek_cli.diagnostics import DiagnosticRecorder
from deepseek_cli.multimodal import read_visual_identity
from deepseek_cli.relationship_policy import relationship_policy_for


def conversation(**overrides):
    values = {
        "id": "c1",
        "title": "Alice",
        "model": "deepseek-v4-flash",
        "updated_at": "now",
        "last_preview": "",
        "ai_summary": "已经讨论星图异变，并决定今晚调查钦天监。",
        "summary_status": "ready",
        "character_name": "谢昭宁",
    }
    values.update(overrides)
    return Conversation(**values)


def test_conversation_row_shows_character_name_above_ai_summary(qtbot):
    row = ConversationRow(conversation())
    qtbot.addWidget(row)
    row.show()

    assert row.minimumHeight() >= 84
    assert row.avatar.width() == 48
    assert row.name.text() == "谢昭宁"
    assert row.preview.wordWrap()
    assert "调查钦天监" in row.preview.text()
    visible_labels = [
        label.text()
        for label in row.findChildren(QLabel)
        if label.isVisibleTo(row)
    ]
    assert "谢昭宁" in visible_labels
    assert "调查钦天监" in row.preview.toolTip()


def test_conversation_row_reports_summary_generation_state(qtbot):
    row = ConversationRow(
        conversation(ai_summary="", summary_status="pending")
    )
    qtbot.addWidget(row)

    assert row.preview.text() == "AI 正在生成摘要…"


def test_user_bubble_max_width_tracks_half_viewport(qtbot):
    bubble = MessageBubble("user", "短消息")
    qtbot.addWidget(bubble)
    bubble.show()

    bubble.set_chat_width(1000)
    assert 160 <= bubble.bubble.width() <= 480
    assert bubble.bubble.minimumWidth() == bubble.bubble.maximumWidth()
    assert bubble.text_label.minimumWidth() == bubble.bubble.width() - 24
    bubble.set_chat_width(700)
    assert 160 <= bubble.bubble.width() <= 330
    assert bubble.bubble.sizePolicy().horizontalPolicy().name == "Preferred"


def test_assistant_bubble_uses_readable_responsive_width(qtbot):
    bubble = MessageBubble("assistant", "这是一段较长的助手回答。")
    qtbot.addWidget(bubble)
    bubble.show()

    bubble.set_chat_width(1000)
    assert bubble.bubble.width() == 729
    assert bubble.text_label.minimumWidth() == 705
    bubble.set_chat_width(700)
    assert bubble.bubble.width() == 501
    assert bubble.text_label.minimumWidth() == 477


def test_completed_assistant_has_speech_controls(qtbot):
    assistant = MessageBubble(
        "assistant",
        "完整回复",
        message_key="turn:t1",
        speech_enabled=True,
    )
    user = MessageBubble(
        "user", "用户消息", message_key="turn:u1", speech_enabled=True
    )
    streaming = MessageBubble("assistant", "")
    qtbot.addWidget(assistant)
    qtbot.addWidget(user)
    qtbot.addWidget(streaming)

    assert assistant.speech_button is not None
    assert user.speech_button is None
    assert streaming.speech_button is None
    assistant.set_speech_state("playing")
    assert assistant.speech_button.text() == "停止"
    assistant.set_speech_state("finished")
    assert assistant.speech_button.text() == "重播"


def test_chat_page_shows_and_removes_typing_indicator(qtbot):
    page = ChatPage()
    qtbot.addWidget(page)

    page.show_typing_indicator()

    assert page._stream_bubble is not None
    assert page._stream_bubble.text_label.text() == "对方正在输入…"
    assert (
        page._stream_bubble.text_label.accessibleName()
        == "对方正在输入"
    )
    page.discard_stream()
    assert page._stream_bubble is None


def test_chat_page_load_defers_opening(qtbot):
    page = ChatPage()
    qtbot.addWidget(page)
    current = conversation(opening_message="模板开场白")

    page.load(current, [])
    bubbles = page._message_bubbles()
    assert len(bubbles) == 1
    assert bubbles[0].text_label.text() == "模板开场白"

    # defer_opening=True（AI 开场进行中）时不显示模板
    page.load(current, [], defer_opening=True)
    assert page._message_bubbles() == []


def test_chat_page_restores_failed_image_event_with_dedicated_retry(
    tmp_path, qtbot
):
    database = Database(tmp_path / "failed-image-ui.db")
    chats = ChatRepository(database)
    current = chats.create_conversation(title="图片重试")
    turn = chats.create_turn(current.id, "给我看看", "model")
    chats.complete_turn(
        turn.id,
        "我这就发给你。",
        assistant_segments_json=json.dumps(
            [
                {"kind": "dialogue", "text": "我这就发给你。"},
                {
                    "kind": "image",
                    "prompt": "窗边晚霞",
                    "event_id": "image-event-1",
                    "status": "failed",
                    "error_code": "image_timeout",
                },
            ],
            ensure_ascii=False,
        ),
    )
    page = ChatPage()
    qtbot.addWidget(page)
    retries = []
    page.image_retry_requested.connect(
        lambda turn_id, event_id: retries.append((turn_id, event_id))
    )

    page.load(current, chats.list_turns(current.id))

    failed = next(
        bubble
        for bubble in page._message_bubbles()
        if bubble.text_label.text() == "图片发送未完成"
    )
    retry = next(
        button
        for button in failed.findChildren(QPushButton)
        if button.text() == "重试"
    )
    retry.click()
    assert retries == [(turn.id, "image-event-1")]
    database.close()


def test_chat_page_pins_to_bottom_and_hides_new_button(qtbot):
    page = ChatPage()
    qtbot.addWidget(page)
    page.resize(400, 600)
    page.show()
    for _ in range(30):
        page.add_assistant_segment("多行文本" * 20)
    qtbot.waitUntil(
        lambda: page.scroll.verticalScrollBar().maximum() > 0, timeout=2000
    )
    assert not page.new_message_button.isVisible()
    page.add_assistant_segment("贴底新消息")
    assert not page.new_message_button.isVisible()
    qtbot.waitUntil(
        lambda: page.scroll.verticalScrollBar().maximum()
        - page.scroll.verticalScrollBar().value()
        <= 160,
        timeout=2000,
    )


def test_chat_page_new_message_button_jumps_to_latest(qtbot):
    page = ChatPage()
    qtbot.addWidget(page)
    page.resize(400, 600)
    page.show()
    for _ in range(30):
        page.add_assistant_segment("多行文本" * 20)
    qtbot.waitUntil(
        lambda: page.scroll.verticalScrollBar().maximum() > 0, timeout=2000
    )
    bar = page.scroll.verticalScrollBar()
    bar.setValue(0)
    page.add_assistant_segment("上滑阅读时到达的新消息")
    assert page.new_message_button.isVisible()
    assert page.new_message_button.text() == "最新消息"
    page.add_assistant_segment("又一条新消息")
    assert page.new_message_button.text() == "最新消息 · 2"
    page.new_message_button.click()
    assert not page.new_message_button.isVisible()
    qtbot.waitUntil(
        lambda: bar.maximum() - bar.value() <= 160, timeout=2000
    )


def test_android_chat_reload_hides_old_bubbles(monkeypatch, qtbot):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    page = ChatPage()
    qtbot.addWidget(page)
    current = conversation(opening_message="第一次加载")

    page.load(current, [])
    old_bubble = page._message_bubbles()[0]
    page.load(current, [])

    assert old_bubble.isHidden()
    assert old_bubble.parent() is page.messages
    bubbles = page._message_bubbles()
    assert len(bubbles) == 1
    assert isinstance(bubbles[0], MessageBubble)
    assert bubbles[0].text_label.text() == "第一次加载"


def test_chat_page_model_selector_tracks_actual_provider_model(qtbot):
    from deepseek_cli.model_catalog import text_provider_models

    page = ChatPage()
    qtbot.addWidget(page)
    page.set_available(True)

    page.set_model_options(
        text_provider_models("grsai", "gemini-3.1-pro")
    )
    assert page.model_combo.currentText() == "GRS AI · gemini-3.1-pro"
    assert not page.model_combo.isEnabled()

    page.set_model_options(text_provider_models("deepseek"))
    assert page.model_combo.count() == 2
    assert page.model_combo.isEnabled()
    assert "deepseek-v4-flash" in page.model_combo.itemText(0)


def test_character_row_shows_avatar_content_and_trusted_builtin_badge(qtbot):
    card = empty_card("谢昭宁")
    card["data"]["description"] = "冷静克制的钦天监调查者。"
    card["data"]["tags"] = ["女性", "推理", "宫廷", "慢热"]
    character = Character(
        "builtin:xie_zhaoning", "谢昭宁", "", card, "now", "now"
    )
    imported = Character(
        "random-id", "导入角色", "", card, "now", "now", "imported"
    )

    row = CharacterRow(character)
    ordinary = CharacterRow(imported)
    qtbot.addWidget(row)
    qtbot.addWidget(ordinary)
    row.show()
    ordinary.show()

    assert row.minimumHeight() >= 104
    assert row.avatar.width() == 56
    assert row.name.text() == "谢昭宁"
    assert row.description.wordWrap()
    assert "钦天监" in row.description.toolTip()
    assert row.tags.text() == "女性 · 推理 · 宫廷"
    assert row.badge is not None and row.badge.text() == "内置"
    assert ordinary.badge is not None and ordinary.badge.text() == "导入"
    assert "内置角色" in row.accessibleName()
    assert "导入角色" in ordinary.accessibleName()


def test_assistant_markdown_and_user_plain_text(qtbot):
    assistant = MessageBubble(
        "assistant", "# 标题\n\n* 列表\n\n**加粗**"
    )
    user = MessageBubble("user", "# 这是用户原文")
    qtbot.addWidget(assistant)
    qtbot.addWidget(user)

    assert assistant.text_label.textFormat() == Qt.TextFormat.MarkdownText
    assert user.text_label.textFormat() == Qt.TextFormat.PlainText
    assistant.append_content("\n\n## 二级标题")
    assert assistant.text_label.textFormat() == Qt.TextFormat.MarkdownText
    assert "二级标题" in assistant.text_label.text()


def test_message_bubble_renders_local_image_preview(tmp_path, qtbot):
    path = tmp_path / "photo.png"
    image = QImage(320, 180, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.cyan)
    assert image.save(str(path), "PNG")

    bubble = MessageBubble(
        "user",
        "给你看看今天的天空",
        image_path=str(path),
    )
    qtbot.addWidget(bubble)
    bubble.show()
    bubble.set_chat_width(1000)

    assert bubble.image_label is not None
    assert bubble.image_label.pixmap() is not None
    assert not bubble.image_label.pixmap().isNull()
    assert bubble.bubble.width() <= 480


def test_sticker_picker_and_message_bubble_render_builtin_sticker(qtbot):
    picker = StickerPickerDialog()
    bubble = MessageBubble("user", "", sticker_id="hug")
    qtbot.addWidget(picker)
    qtbot.addWidget(bubble)

    buttons = [
        button
        for button in picker.findChildren(QPushButton)
        if button.objectName() == "stickerButton"
    ]
    assert len(buttons) == len(STICKERS)
    assert {button.toolTip() for button in buttons} >= {"开心", "抱抱", "生气"}
    assert bubble.sticker_label is not None
    assert bubble.sticker_label.text() == "🤗"
    assert bubble.sticker_label.accessibleName() == "表情包：抱抱"
    assert bubble.text_label.isHidden()


def test_settings_separates_text_image_and_tts_providers(tmp_path, qtbot):
    class Credentials:
        @staticmethod
        def get_api_key():
            return ""

        @staticmethod
        def get_image_api_key():
            return ""

        @staticmethod
        def get_google_image_api_key():
            return ""

        @staticmethod
        def get_siliconflow_api_key():
            return ""

        @staticmethod
        def get_grsai_text_api_key():
            return ""

        @staticmethod
        def get_siliconflow_image_api_key():
            return ""

        @staticmethod
        def get_grsai_image_api_key():
            return ""

        @staticmethod
        def get_siliconflow_tts_api_key():
            return ""

        @staticmethod
        def get_xfyun_tts_api_password():
            return ""

    database = Database(tmp_path / "chat.db")
    page = SettingsPage(SettingsRepository(database), Credentials())
    qtbot.addWidget(page)

    labels = " ".join(label.text() for label in page.findChildren(QLabel))
    buttons = {
        button.text(): button for button in page.findChildren(QPushButton)
    }

    assert "图片理解与角色自主生图共用当前图片平台" in labels
    assert "文本与语音不会使用这里的密钥" in labels
    assert "DeepSeek 与 GRS AI 文本凭据彼此独立" in labels
    assert "获取 API Key" in buttons
    assert "模型与价格" in buttons
    assert (
        buttons["获取 API Key"].accessibleName()
        == "打开硅基流动 API Key 页面"
    )
    assert (
        page.siliconflow_image_model.currentData()
        == "Tongyi-MAI/Z-Image-Turbo"
    )
    assert page.siliconflow_image_size.currentData() == "1024x1024"
    assert page.siliconflow_vision_model.text() == "Qwen/Qwen3-VL-8B-Instruct"
    assert page.text_provider.findData("deepseek") >= 0
    assert page.text_provider.findData("grsai") >= 0
    assert page.text_provider.currentData() == "deepseek"
    assert page.aux_text_provider.currentData() == "inherit"
    assert page.aux_deepseek_model.currentData() == "deepseek-v4-flash"
    assert "辅助任务跟随主角色模型" in page.aux_text_status.text()
    assert page.image_provider.findData("siliconflow") >= 0
    assert page.image_provider.findData("grsai") >= 0
    assert page.image_provider.currentData() == "siliconflow"
    assert page.grsai_text_model.text() == "gemini-3.1-flash-lite"
    assert page.default_model.count() == 2
    assert "deepseek-v4-flash" in page.default_model.currentText()
    page.default_model.setCurrentIndex(
        page.default_model.findData("deepseek-v4-pro")
    )
    page.text_provider.setCurrentIndex(
        page.text_provider.findData("grsai")
    )
    assert page.default_model.count() == 1
    assert page.default_model.currentText() == (
        "GRS AI · gemini-3.1-flash-lite"
    )
    assert not page.default_model.isEnabled()
    page.grsai_text_model.setText("gemini-3.1-pro")
    page.grsai_text_model.editingFinished.emit()
    assert page.default_model.currentText() == "GRS AI · gemini-3.1-pro"
    page.text_provider.setCurrentIndex(
        page.text_provider.findData("deepseek")
    )
    assert page.default_model.currentData() == "deepseek-v4-pro"
    assert page.grsai_image_model.text() == "gpt-image-2"
    assert page.grsai_vision_model.text() == "gemini-3.1-flash-lite"
    assert (
        page.siliconflow_tts_model.text()
        == "FunAudioLLM/CosyVoice2-0.5B"
    )
    assert page.tts_provider.findData("edge") >= 0
    assert page.tts_provider.findData("xfyun") >= 0
    assert page.tts_provider.findData("siliconflow") >= 0
    assert page.tts_provider.findData("indextts2") >= 0
    assert page.tts_provider.currentData() == "edge"
    assert page.autonomous_images_enabled.isChecked()
    assert page.autonomous_image_daily_limit.value() == 4
    assert page.autonomous_image_cooldown_turns.value() == 4
    page.autonomous_images_enabled.setChecked(False)
    assert not page.autonomous_image_daily_limit.isEnabled()
    assert not page.autonomous_image_cooldown_turns.isEnabled()
    page.autonomous_images_enabled.setChecked(True)
    assert page.notification_sound_enabled.isChecked()
    assert page.xfyun_tts_voice.findData("auto") >= 0
    assert page.xfyun_tts_voice.findData("x5_lingxiaoxuan_flow") >= 0
    assert "检测当前账号可用音色" in buttons
    assert "固定使用发音人的默认语速、语调和音量" in labels
    assert page.xfyun_tts_auth_method.findData("password") >= 0
    assert page.xfyun_tts_auth_method.findData("hmac") >= 0
    assert page.indextts2_base_url.text() == "http://127.0.0.1:7861"
    assert page.indextts2_preset.findData(
        "BanVerse_林小满_讯飞聆小糖"
    ) >= 0
    assert "动作与旁白" in page.tts_auto_play.text()
    assert page.user_name.text() == "用户"
    assert page.roleplay_temperature.value() == 1.3
    assert page.roleplay_sampling_mode.currentData() == "temperature"
    assert page.roleplay_temperature.isEnabled()
    assert not page.roleplay_top_p.isEnabled()
    page.roleplay_sampling_mode.setCurrentIndex(
        page.roleplay_sampling_mode.findData("provider_default")
    )
    assert not page.roleplay_temperature.isEnabled()
    assert page._settings.get("roleplay_sampling_mode") == "provider_default"
    assert page.role_memory_enabled.isChecked()
    assert page.memory_retention_days.value() == 365
    assert page.memory_max_items.value() == 200
    assert not page.roleplay_director_enabled.isChecked()
    assert not page.roleplay_director_threshold.isEnabled()
    assert page.roleplay_director_threshold.value() == 6
    assert page.roleplay_director_max_extra_calls.value() == 1
    assert page.roleplay_director_timeout.value() == 8
    page.roleplay_director_enabled.setChecked(True)
    assert page.roleplay_director_threshold.isEnabled()
    assert page._settings.get("roleplay_director_enabled") == "true"
    assert page.relationship_pace.currentData() == "natural"
    assert page.relationship_preferred_address.text() == ""
    assert page.proactive_frequency.currentData() == "normal"
    assert page.proactive_daily_limit.value() == 2
    assert page.proactive_quiet_start.time().toString("HH:mm") == "22:30"
    assert page.proactive_quiet_end.time().toString("HH:mm") == "08:00"
    assert not page.proactive_frequency.isEnabled()
    page.proactive_enabled.setChecked(True)
    assert page.proactive_frequency.isEnabled()
    assert page.proactive_daily_limit.isEnabled()
    page.relationship_preferred_address.setText("阿澄")
    page.relationship_preferred_address.editingFinished.emit()
    page.relationship_blocked_topics.setText("收入、住址")
    page.relationship_blocked_topics.editingFinished.emit()
    assert page._settings.get("relationship_preferred_address") == "阿澄"
    assert page._settings.get("relationship_blocked_topics") == "收入、住址"
    assert not page.character_discovery_enabled.isChecked()
    assert not page.character_discovery_min_minutes.isEnabled()
    assert page.character_discovery_female_percent.value() == 50
    assert not page.character_discovery_female_percent.isEnabled()
    page.character_discovery_enabled.setChecked(True)
    assert page.character_discovery_min_minutes.isEnabled()
    assert page.character_discovery_max_minutes.isEnabled()
    assert page.character_discovery_daily_limit.isEnabled()
    assert page.character_discovery_female_percent.isEnabled()
    page.character_discovery_female_percent.setValue(65)
    assert page.character_discovery_male_percent.text() == "男性 35 %"
    assert (
        page._settings.get("character_discovery_female_percent") == "65"
    )
    assert page._settings.get("character_discovery_enabled") == "true"
    assert "新联系人会话" in labels
    assert "伴界 BanVerse" in labels
    database.close()


def test_character_relationship_policy_dialog_saves_override_pause_and_reason(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    settings.set("relationship_pace", "slow")
    character = characters.create(empty_card("边界测试角色"))
    settings.set(
        f"proactive_last_status_{character.id}",
        "因为存在未完话题，并且已通过静默和冷却检查。",
    )
    dialog = RelationshipPolicyDialog(character, settings)
    qtbot.addWidget(dialog)

    assert dialog.inherit.isChecked()
    assert not dialog.pace.isEnabled()
    assert dialog.muted.isEnabled()
    assert "未完话题" in dialog.last_reason.text()
    dialog.inherit.setChecked(False)
    dialog.pace.setCurrentIndex(dialog.pace.findData("fast"))
    dialog.preferred_address.setText("老师")
    dialog.blocked_topics.setText("收入、住址")
    dialog.frequency.setCurrentIndex(dialog.frequency.findData("low"))
    dialog.daily_limit.setValue(1)
    dialog.muted.setChecked(True)
    dialog._pause_day()
    assert dialog._paused_until
    dialog.pause_until.setDateTime(QDateTime(2030, 1, 2, 9, 30, 0))
    dialog._apply_pause()
    dialog._save()

    saved = relationship_policy_for(settings, character.id)
    assert saved.inherited is False
    assert saved.pace == "fast"
    assert saved.preferred_address == "老师"
    assert saved.blocked_topics == ("收入", "住址")
    assert saved.proactive_frequency == "low"
    assert saved.daily_limit == 1
    assert saved.muted is True
    assert saved.paused_until.startswith("2030-01-02T09:30")
    database.close()


def test_conversation_editor_exposes_per_conversation_director_switch(qtbot):
    dialog = ConversationEditDialog(
        conversation(),
        [],
        director_enabled=False,
        director_available=True,
    )
    qtbot.addWidget(dialog)

    assert dialog.director.isEnabled()
    assert not dialog.director.isChecked()


def test_memory_manager_confirms_candidate_without_deleting_messages(
    tmp_path, qtbot
):
    database = Database(tmp_path / "memory-manager.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    character = characters.create(empty_card("记忆角色"))
    conversation = chats.create_conversation(character_id=character.id)
    turn = chats.create_turn(conversation.id, "保留消息", "deepseek-v4-flash")
    chats.complete_turn(turn.id, "好的")
    memory = chats.create_memory(
        conversation.id,
        "user_fact",
        "模型推测的候选",
        source_type="assistant_inferred",
        source_turn_id=turn.id,
        status="candidate",
    )
    dialog = MemoryManagerDialog(
        chats, settings, conversation.id
    )
    qtbot.addWidget(dialog)

    assert dialog.list.count() == 1
    assert dialog.confirm_button.isEnabled()
    dialog.confirm_button.click()
    confirmed = chats.get_memory(memory.id)
    assert confirmed.status == "active"
    assert confirmed.confirmed_at
    assert len(chats.list_turns(conversation.id)) == 1
    dialog.character_enabled.setChecked(False)
    assert settings.get_bool(
        f"role_memory_character_{character.id}", True
    ) is False
    database.close()


def test_sync_settings_save_pairing_and_account_creation_signals(tmp_path, qtbot):
    class Credentials:
        token = ""
        get_api_key = staticmethod(lambda: "")

        def get_sync_token(self):
            return self.token

        def save_sync_token(self, value):
            self.token = value

    credentials = Credentials()
    database = Database(tmp_path / "sync-settings.db")
    settings = SettingsRepository(database)
    page = SettingsPage(settings, credentials)
    qtbot.addWidget(page)
    changed = []
    sync_now = []
    account_requests = []
    register_requests = []
    login_requests = []
    upgrade_requests = []
    page.sync_settings_changed.connect(lambda: changed.append(True))
    page.sync_now_requested.connect(lambda: sync_now.append(True))
    page.sync_account_create_requested.connect(
        lambda *args: account_requests.append(args)
    )
    page.sync_register_requested.connect(
        lambda *args: register_requests.append(args)
    )
    page.sync_login_requested.connect(lambda *args: login_requests.append(args))
    page.sync_upgrade_requested.connect(
        lambda *args: upgrade_requests.append(args)
    )

    page.sync_server_url.setText("https://sync.example.test/")
    page.sync_account_id.setText("account-123456")
    page.sync_token.setText("token-12345678")
    page.sync_device_name.setText("  我的 Android 手机  ")
    page.sync_enabled.setChecked(True)

    assert page._save_sync_settings() is True
    assert settings.get("sync_server_url") == "https://sync.example.test"
    assert settings.get("sync_account_id") == "account-123456"
    assert settings.get("sync_device_name") == "我的 Android 手机"
    assert settings.get_bool("sync_enabled") is True
    assert credentials.token == "token-12345678"
    assert changed == [True]
    assert sync_now == [True]

    page._copy_sync_pairing()
    pairing = json.loads(QApplication.clipboard().text())
    assert pairing == {
        "server_url": "https://sync.example.test",
        "account_id": "account-123456",
        "token": "token-12345678",
    }

    QApplication.clipboard().setText("not-json")
    page._import_sync_pairing()
    assert "不是有效的 JSON" in page.sync_status.text()

    imported = {
        "server_url": "https://official-sync.example.test/",
        "account_id": "account-87654321",
        "token": "token-87654321",
    }
    QApplication.clipboard().setText(json.dumps(imported))
    page._import_sync_pairing()
    assert QApplication.clipboard().text() == ""
    assert settings.get("sync_server_url") == "https://official-sync.example.test"
    assert settings.get("sync_account_id") == "account-87654321"
    assert credentials.token == "token-87654321"
    assert changed == [True, True]
    assert sync_now == [True, True]
    assert "剪贴板中的令牌已清除" in page.sync_status.text()

    page.sync_registration_secret.setText("registration-secret")
    page._create_sync_account()
    assert account_requests == [
        ("https://official-sync.example.test", "用户", "registration-secret")
    ]
    assert page.sync_registration_secret.text() == ""

    page.sync_username.setText("BanVerse用户")
    page.sync_password.setText("safe-password-2026")
    page.sync_password_confirm.setText("safe-password-2026")
    page.sync_registration_secret.setText("invite-code")
    page._register_sync_account()
    assert register_requests == [
        (
            "https://official-sync.example.test",
            "BanVerse用户",
            "safe-password-2026",
            "用户",
            "invite-code",
        )
    ]
    assert page.sync_password.text() == ""
    assert page.sync_registration_secret.text() == ""

    page.sync_password.setText("safe-password-2026")
    page._login_sync_account()
    assert login_requests == [
        (
            "https://official-sync.example.test",
            "BanVerse用户",
            "safe-password-2026",
        )
    ]

    page.sync_password.setText("upgraded-password")
    page.sync_password_confirm.setText("upgraded-password")
    page._upgrade_sync_account()
    assert upgrade_requests == [
        ("BanVerse用户", "upgraded-password", "用户")
    ]
    page.set_sync_account(
        {"account_id": "account-87654321", "username": "BanVerse用户"}
    )
    assert page.sync_account_state.text() == "已登录：BanVerse用户"
    assert not page.sync_upgrade.isEnabled()
    database.close()


def test_settings_only_shows_selected_provider_configuration(tmp_path, qtbot):
    class Credentials:
        get_api_key = staticmethod(lambda: "")
        get_grsai_text_api_key = staticmethod(lambda: "")
        get_siliconflow_image_api_key = staticmethod(lambda: "")
        get_grsai_image_api_key = staticmethod(lambda: "")
        get_siliconflow_tts_api_key = staticmethod(lambda: "")
        get_xfyun_tts_api_password = staticmethod(lambda: "")

    database = Database(tmp_path / "provider-sections.db")
    page = SettingsPage(SettingsRepository(database), Credentials())
    qtbot.addWidget(page)

    assert all(
        page._text_form.isRowVisible(row)
        for row in page._text_deepseek_rows
    )
    assert not any(
        page._text_form.isRowVisible(row)
        for row in page._text_grsai_rows
    )
    page.text_provider.setCurrentIndex(page.text_provider.findData("grsai"))
    assert not any(
        page._text_form.isRowVisible(row)
        for row in page._text_deepseek_rows
    )
    assert all(
        page._text_form.isRowVisible(row)
        for row in page._text_grsai_rows
    )

    page.image_provider.setCurrentIndex(page.image_provider.findData("grsai"))
    assert not any(
        page._image_form.isRowVisible(row)
        for row in page._image_siliconflow_rows
    )
    assert all(
        page._image_form.isRowVisible(row)
        for row in page._image_grsai_rows
    )

    page.tts_provider.setCurrentIndex(page.tts_provider.findData("xfyun"))
    assert not any(
        page._tts_form.isRowVisible(row)
        for row in page._tts_siliconflow_rows
    )
    assert all(
        page._tts_form.isRowVisible(row)
        for row in page._xfyun_password_rows
    )
    assert not any(
        page._tts_form.isRowVisible(row)
        for row in page._xfyun_hmac_rows
    )
    page.xfyun_tts_auth_method.setCurrentIndex(
        page.xfyun_tts_auth_method.findData("hmac")
    )
    assert not any(
        page._tts_form.isRowVisible(row)
        for row in page._xfyun_password_rows
    )
    assert all(
        page._tts_form.isRowVisible(row)
        for row in page._xfyun_hmac_rows
    )
    page.tts_provider.setCurrentIndex(
        page.tts_provider.findData("indextts2")
    )
    assert not any(
        page._tts_form.isRowVisible(row)
        for row in page._tts_xfyun_rows
    )
    assert all(
        page._tts_form.isRowVisible(row)
        for row in page._tts_indextts2_rows
    )
    database.close()


def test_settings_model_dropdowns_filter_cached_catalog_by_capability(
    tmp_path, qtbot
):
    class Credentials:
        get_api_key = staticmethod(lambda: "")
        get_grsai_text_api_key = staticmethod(lambda: "")
        get_siliconflow_image_api_key = staticmethod(lambda: "")
        get_grsai_image_api_key = staticmethod(lambda: "")
        get_siliconflow_tts_api_key = staticmethod(lambda: "")
        get_xfyun_tts_api_password = staticmethod(lambda: "")

    database = Database(tmp_path / "model-catalog.db")
    settings = SettingsRepository(database)
    settings.set(
        "model_catalog_grsai",
        serialize_models(
            (
                ProviderModel("grsai", "chat-only", ("chat",)),
                ProviderModel(
                    "grsai", "vision-chat", ("chat", "vision", "reasoning")
                ),
                ProviderModel("grsai", "image-only", ("image_generation",)),
            )
        ),
    )
    settings.set(
        "model_catalog_siliconflow",
        serialize_models(
            (
                ProviderModel(
                    "siliconflow", "vision-model", ("chat", "vision")
                ),
                ProviderModel(
                    "siliconflow", "image-model", ("image_generation",)
                ),
                ProviderModel("siliconflow", "tts-model", ("tts",)),
                ProviderModel(
                    "siliconflow", "embedding-model", ()
                ),
            )
        ),
    )
    page = SettingsPage(settings, Credentials())
    qtbot.addWidget(page)

    def combo_values(combo):
        return {
            combo.itemData(index) for index in range(combo.count())
        }
    assert {"chat-only", "vision-chat"} <= combo_values(page.grsai_text_model)
    assert {"chat-only", "vision-chat"} <= combo_values(
        page.aux_grsai_text_model
    )
    assert "image-only" not in combo_values(page.grsai_text_model)
    assert "vision-chat" in combo_values(page.grsai_vision_model)
    assert "chat-only" not in combo_values(page.grsai_vision_model)
    assert "image-only" in combo_values(page.grsai_image_model)
    assert "vision-model" in combo_values(page.siliconflow_vision_model)
    assert "image-model" in combo_values(page.siliconflow_image_model)
    assert "tts-model" in combo_values(page.siliconflow_tts_model)
    assert "embedding-model" not in combo_values(page.siliconflow_tts_model)
    vision_index = page.grsai_text_model.findData("vision-chat")
    assert "多模态" in page.grsai_text_model.itemText(vision_index)
    database.close()


def test_settings_migrates_legacy_grsai_image_endpoint(tmp_path, qtbot):
    class Credentials:
        get_api_key = staticmethod(lambda: "")
        get_grsai_text_api_key = staticmethod(lambda: "")
        get_siliconflow_image_api_key = staticmethod(lambda: "")
        get_grsai_image_api_key = staticmethod(lambda: "")
        get_siliconflow_tts_api_key = staticmethod(lambda: "")
        get_xfyun_tts_api_password = staticmethod(lambda: "")

    database = Database(tmp_path / "legacy-grsai-endpoint.db")
    settings = SettingsRepository(database)
    settings.set(
        "grsai_image_base_url",
        "https://grsai.dakka.com.cn/v1/api/generate",
    )
    page = SettingsPage(settings, Credentials())
    qtbot.addWidget(page)

    assert page.grsai_image_base_url.text() == (
        "https://grsai.dakka.com.cn/v1"
    )
    assert settings.get("grsai_image_base_url", "") == (
        "https://grsai.dakka.com.cn/v1"
    )
    database.close()


def test_chat_page_shows_autonomous_image_failure_inline(qtbot):
    page = ChatPage()
    qtbot.addWidget(page)

    page.add_image_error("image_authentication")

    labels = [label.text() for label in page.findChildren(QLabel)]
    assert any("图片发送失败" in text for text in labels)
    assert any("API Key 无效" in text for text in labels)


def test_composer_has_no_manual_image_generation_entry(qtbot):
    composer = ChatComposer()
    qtbot.addWidget(composer)

    assert not hasattr(composer, "generate_button")
    assert "ImageGen" not in {
        button.text() for button in composer.findChildren(QPushButton)
    }
    assert composer.attach_button.text() == "图片"
    assert composer.sticker_button.text() == "表情"
    assert composer.sticker_button.accessibleName() == "打开表情包"


def test_android_chat_layout_fits_narrow_viewport(monkeypatch, qtbot):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    assistant = MessageBubble("assistant", "这是一条需要自动换行的移动端回复。")
    user = MessageBubble("user", "一条比较长的用户消息，用来检查右侧边界。")
    host = QWidget()
    layout = QVBoxLayout(host)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(0)
    composer = ChatComposer()
    layout.addWidget(composer)
    for widget in (assistant, user, host):
        qtbot.addWidget(widget)

    assistant.set_chat_width(360)
    user.set_chat_width(360)
    # 直接激活子控件布局，避免 Windows 原生顶层窗口在
    # DPI 缩放下重算客户区；这里只验证 360px Android 布局。
    host.resize(360, 130)
    layout.activate()
    composer.layout().activate()
    QCoreApplication.processEvents()

    assert assistant.bubble.width() <= 336
    assert user.bubble.width() <= 336
    assert assistant.text_label.maximumWidth() <= 312
    assert user.text_label.maximumWidth() <= 312
    assert composer.width() == 360
    assert composer.editor.width() >= 320
    assert composer.editor.height() == 52
    assert composer.attach_button.height() >= 44
    assert composer.sticker_button.height() >= 44
    assert composer.action.height() >= 44
    assert (
        assistant.text_label.textInteractionFlags()
        == Qt.TextInteractionFlag.NoTextInteraction
    )


def test_android_dialogs_and_stickers_use_mobile_layout(monkeypatch, qtbot):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    editor = CharacterEditorDialog()
    picker = StickerPickerDialog()
    qtbot.addWidget(editor)
    qtbot.addWidget(picker)

    assert editor.width() == 356
    assert all(
        isinstance(editor.tabs.widget(index), QScrollArea)
        for index in range(editor.tabs.count())
    )
    basic_scroll = editor.tabs.widget(0)
    assert QScroller.hasScroller(basic_scroll.viewport())
    assert (
        basic_scroll.widget().layout().rowWrapPolicy()
        == QFormLayout.RowWrapPolicy.WrapAllRows
    )
    assert picker._columns == 4
    assert picker.width() == 344


def test_character_editor_saves_stable_visual_identity(qtbot):
    card = empty_card("视觉测试角色")
    character = Character(
        "visual-test", "视觉测试角色", "", card, "now", "now"
    )
    editor = CharacterEditorDialog(character)
    qtbot.addWidget(editor)
    editor.visual_description.setPlainText("成年女性，棕色短发，灰蓝眼睛")
    editor.visual_default_outfit.setPlainText("米白色长外套")
    editor.visual_negative_prompt.setPlainText("不要改变发色和眼睛颜色")
    editor.visual_use_avatar_reference.setChecked(False)

    editor._save()

    identity = read_visual_identity(editor.card)
    assert identity.description == "成年女性，棕色短发，灰蓝眼睛"
    assert identity.default_outfit == "米白色长外套"
    assert identity.negative_prompt == "不要改变发色和眼睛颜色"
    assert not identity.use_avatar_reference


def test_android_settings_and_character_rows_are_touch_ready(
    monkeypatch, tmp_path, qtbot
):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    gestures = []
    original_grab_gesture = QScroller.grabGesture

    def record_gesture(target, gesture):
        gestures.append(gesture)
        return original_grab_gesture(target, gesture)

    monkeypatch.setattr(
        QScroller,
        "grabGesture",
        staticmethod(record_gesture),
    )

    class Credentials:
        get_api_key = staticmethod(lambda: "")
        get_grsai_text_api_key = staticmethod(lambda: "")
        get_siliconflow_image_api_key = staticmethod(lambda: "")
        get_grsai_image_api_key = staticmethod(lambda: "")
        get_siliconflow_tts_api_key = staticmethod(lambda: "")
        get_xfyun_tts_api_password = staticmethod(lambda: "")

    database = Database(tmp_path / "mobile-settings.db")
    page = SettingsPage(SettingsRepository(database), Credentials())
    card = empty_card("移动角色")
    card["data"]["description"] = "很长的角色简介" * 30
    row = CharacterRow(
        Character("builtin:mobile", "移动角色", "", card, "now", "now")
    )
    qtbot.addWidget(page)
    qtbot.addWidget(row)
    page.resize(360, 760)
    page.show()
    qtbot.wait(20)

    assert QScroller.hasScroller(page.scroll.viewport())
    assert QScroller.ScrollerGestureType.LeftMouseButtonGesture in gestures
    properties = QScroller.scroller(
        page.scroll.viewport()
    ).scrollerProperties()
    drag_distance = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.DragStartDistance
    )
    assert abs(float(drag_distance) - 0.0015) < 1e-9
    smoothing = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.DragVelocitySmoothingFactor
    )
    assert abs(float(smoothing) - 0.85) < 1e-9
    deceleration = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.DecelerationFactor
    )
    assert abs(float(deceleration) - 0.2) < 1e-9
    maximum_velocity = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.MaximumVelocity
    )
    assert abs(float(maximum_velocity) - 0.9) < 1e-9
    accelerating_time = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.AcceleratingFlickMaximumTime
    )
    assert float(accelerating_time) == 0.0
    accelerating_factor = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.AcceleratingFlickSpeedupFactor
    )
    assert float(accelerating_factor) == 1.0
    for metric in (
        QScrollerProperties.ScrollMetric.OvershootDragResistanceFactor,
        QScrollerProperties.ScrollMetric.OvershootDragDistanceFactor,
        QScrollerProperties.ScrollMetric.OvershootScrollDistanceFactor,
        QScrollerProperties.ScrollMetric.OvershootScrollTime,
    ):
        assert float(properties.scrollMetric(metric)) == 0.0
    horizontal_overshoot = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.HorizontalOvershootPolicy
    )
    vertical_overshoot = properties.scrollMetric(
        QScrollerProperties.ScrollMetric.VerticalOvershootPolicy
    )
    assert (
        horizontal_overshoot
        == QScrollerProperties.OvershootPolicy.OvershootAlwaysOff
    )
    assert (
        vertical_overshoot
        == QScrollerProperties.OvershootPolicy.OvershootAlwaysOff
    )
    assert (
        page.scroll.horizontalScrollBarPolicy()
        == Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert (
        page._text_form.rowWrapPolicy()
        == QFormLayout.RowWrapPolicy.WrapAllRows
    )
    assert page.scroll.horizontalScrollBar().maximum() == 0
    assert page.scroll.widget().width() <= page.scroll.viewport().width()
    assert row.minimumHeight() == 128
    assert len(row.description.text()) <= 53
    database.close()


def test_android_touch_scroll_hard_clamps_boundary_overshoot(
    monkeypatch, qtbot
):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    area = QScrollArea()
    content = QWidget()
    content.setFixedSize(360, 1600)
    area.setWidget(content)
    enable_touch_scrolling(area)
    qtbot.addWidget(area)
    area.resize(360, 500)
    area.show()
    qtbot.waitUntil(
        lambda: area.verticalScrollBar().maximum() > 0, timeout=1000
    )

    bar = area.verticalScrollBar()
    viewport = area.viewport()
    bar.setValue(bar.minimum())
    top_overshoot = QScrollEvent(
        QPointF(0, bar.minimum()),
        QPointF(0, -24),
        QScrollEvent.ScrollState.ScrollUpdated,
    )
    assert QCoreApplication.sendEvent(viewport, top_overshoot)
    assert bar.value() == bar.minimum()

    bar.setValue(bar.maximum())
    bottom_overshoot = QScrollEvent(
        QPointF(0, bar.maximum()),
        QPointF(0, 24),
        QScrollEvent.ScrollState.ScrollUpdated,
    )
    assert QCoreApplication.sendEvent(viewport, bottom_overshoot)
    assert bar.value() == bar.maximum()


def test_android_sticker_picker_is_non_blocking(monkeypatch, qtbot):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    composer = ChatComposer()
    qtbot.addWidget(composer)
    selected = []
    composer.sticker_requested.connect(selected.append)

    composer._choose_sticker()

    dialog = composer._sticker_dialog
    assert dialog is not None and dialog.isVisible()
    dialog._select("happy")
    qtbot.wait(10)
    assert selected == ["happy"]
    assert composer._sticker_dialog is None


def test_android_attachment_picker_is_non_blocking(
    monkeypatch, qtbot, tmp_path
):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    image_path = tmp_path / "selected.png"
    image = QImage(32, 32, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.green)
    assert image.save(str(image_path), "PNG")
    selected_callback = {}

    def fake_open(parent, _title, _name_filter, on_selected, **_kwargs):
        selected_callback["callback"] = on_selected
        dialog = QDialog(parent)
        dialog.open()
        return dialog

    monkeypatch.setattr(
        "deepseek_cli.desktop.ui.widgets.chat_composer.open_mobile_file_dialog",
        fake_open,
    )
    composer = ChatComposer()
    qtbot.addWidget(composer)

    composer._choose_attachment()

    dialog = composer._attachment_dialog
    assert dialog is not None and dialog.isVisible()
    selected_callback["callback"](str(image_path))
    dialog.reject()
    qtbot.wait(10)
    assert composer._attachment_path == str(image_path)
    assert composer._attachment_dialog is None


def test_mobile_file_dialog_delivers_selection_on_accept(qtbot, tmp_path):
    image_path = tmp_path / "accepted.png"
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.blue)
    assert image.save(str(image_path), "PNG")
    parent = QWidget()
    qtbot.addWidget(parent)
    selected = []

    dialog = open_mobile_file_dialog(
        parent,
        "发送图片",
        "图片 (*.png)",
        selected.append,
        mime_types=("image/png",),
    )
    dialog.selectFile(str(image_path))
    dialog.accept()
    qtbot.wait(10)

    assert len(selected) == 1
    selected_path = QUrl.fromUserInput(selected[0]).toLocalFile()
    assert selected_path.replace("\\", "/") == str(image_path).replace(
        "\\", "/"
    )


def test_mobile_file_dialog_delivers_selection_on_finished_fallback(
    qtbot, tmp_path
):
    image_path = tmp_path / "finished.png"
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.cyan)
    assert image.save(str(image_path), "PNG")
    parent = QWidget()
    qtbot.addWidget(parent)
    selected = []

    dialog = open_mobile_file_dialog(
        parent,
        "发送图片",
        "图片 (*.png)",
        selected.append,
        mime_types=("image/png",),
    )
    dialog.selectFile(str(image_path))
    dialog.finished.emit(QDialog.DialogCode.Accepted.value)
    qtbot.wait(10)

    assert len(selected) == 1
    selected_path = QUrl.fromUserInput(selected[0]).toLocalFile()
    assert selected_path.replace("\\", "/") == str(image_path).replace(
        "\\", "/"
    )


def test_mobile_file_dialog_preserves_android_content_uri(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    selected = []
    content_uri = "content://media/external/images/media/42"
    dialog = open_mobile_file_dialog(
        parent,
        "发送图片",
        "图片 (*.png)",
        selected.append,
    )

    dialog.urlSelected.emit(QUrl(content_uri))
    qtbot.wait(10)

    assert selected == [content_uri]


def test_mobile_file_dialog_prefers_url_over_lossy_path(qtbot):
    parent = QWidget()
    qtbot.addWidget(parent)
    selected = []
    content_uri = "content://media/external/images/media/42"
    dialog = open_mobile_file_dialog(
        parent,
        "发送图片",
        "图片 (*.png)",
        selected.append,
    )

    dialog.fileSelected.emit("content:/media/external/images/media/42")
    dialog.urlSelected.emit(QUrl(content_uri))
    qtbot.wait(10)

    assert selected == [content_uri]


def test_mobile_file_dialog_recovers_selection_when_app_returns(
    qtbot, tmp_path
):
    image_path = tmp_path / "returned.png"
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.magenta)
    assert image.save(str(image_path), "PNG")
    parent = QWidget()
    qtbot.addWidget(parent)
    selected = []

    dialog = open_mobile_file_dialog(
        parent,
        "发送图片",
        "图片 (*.png)",
        selected.append,
        mime_types=("image/png",),
    )
    dialog.selectFile(str(image_path))
    application = QGuiApplication.instance()
    application.applicationStateChanged.emit(
        Qt.ApplicationState.ApplicationInactive
    )
    application.applicationStateChanged.emit(
        Qt.ApplicationState.ApplicationActive
    )
    qtbot.wait(20)

    assert len(selected) == 1
    selected_path = QUrl.fromUserInput(selected[0]).toLocalFile()
    assert selected_path.replace("\\", "/") == str(image_path).replace(
        "\\", "/"
    )


def test_android_document_bridge_delivers_private_copy(
    monkeypatch, qtbot, tmp_path
):
    monkeypatch.setenv("ANDROID_PRIVATE", str(tmp_path))
    opened = []
    monkeypatch.setattr(
        "deepseek_cli.desktop.ui.file_dialogs.QDesktopServices.openUrl",
        lambda url: opened.append(url.toString()) or True,
    )
    parent = QWidget()
    qtbot.addWidget(parent)
    selected = []
    results = []

    picker = open_mobile_file_dialog(
        parent,
        "发送图片",
        "图片 (*.png)",
        selected.append,
        mime_types=("image/png",),
    )
    picker.finished.connect(results.append)
    qtbot.waitUntil(lambda: bool(opened))
    image_path = tmp_path / "banverse-picker" / "imports" / "selected.png"
    image_path.parent.mkdir(parents=True)
    image = QImage(24, 24, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.yellow)
    assert image.save(str(image_path), "PNG")
    picker._result_file.parent.mkdir(parents=True, exist_ok=True)
    picker._result_file.write_text(
        f"ok\n{image_path}\n",
        encoding="utf-8",
    )
    qtbot.waitUntil(lambda: bool(results))

    assert selected == [str(image_path)]
    assert results == [QDialog.DialogCode.Accepted.value]
    assert opened[0].startswith("banverse-picker://open?")


def test_settings_shows_and_exports_privacy_safe_diagnostics(
    tmp_path, qtbot, monkeypatch
):
    class Credentials:
        @staticmethod
        def get_api_key():
            return ""

    database = Database(tmp_path / "diagnostics-settings.db")
    recorder = DiagnosticRecorder(tmp_path / "diagnostics")
    recorder.record("text_chat", "model_completed", duration_ms=25)
    page = SettingsPage(
        SettingsRepository(database), Credentials(), diagnostics=recorder
    )
    qtbot.addWidget(page)
    messages: list[str] = []
    monkeypatch.setattr(
        "deepseek_cli.desktop.ui.pages.settings_page.QMessageBox.information",
        lambda _parent, _title, message: messages.append(message),
    )

    output = tmp_path / "exported-diagnostics.zip"
    page._export_diagnostics_to_path(str(output))

    assert "本机事件：1" in page.diagnostics_status.text()
    assert output.is_file()
    assert messages and "脱敏诊断包已保存" in messages[0]
