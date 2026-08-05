from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QImage
from PySide6.QtWidgets import (
    QDialog,
    QFormLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QScroller,
)

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import (
    Character,
    Conversation,
    SettingsRepository,
)
from deepseek_cli.desktop.model_discovery import ProviderModel, serialize_models
from deepseek_cli.desktop.stickers import STICKERS
from deepseek_cli.desktop.ui.character_editor_dialog import (
    CharacterEditorDialog,
)
from deepseek_cli.desktop.ui.pages.characters_page import CharacterRow
from deepseek_cli.desktop.ui.pages.chat_page import ChatPage
from deepseek_cli.desktop.ui.pages.conversations_page import ConversationRow
from deepseek_cli.desktop.ui.pages.settings_page import SettingsPage
from deepseek_cli.desktop.ui.widgets.chat_composer import ChatComposer
from deepseek_cli.desktop.ui.widgets.message_bubble import MessageBubble
from deepseek_cli.desktop.ui.widgets.sticker_picker import StickerPickerDialog


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


def test_android_chat_reload_hides_old_bubbles(monkeypatch, qtbot):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    page = ChatPage()
    qtbot.addWidget(page)
    current = conversation(opening_message="第一次加载")

    page.load(current, [])
    old_bubble = page.messages_layout.itemAt(0).widget()
    page.load(current, [])

    assert old_bubble.isHidden()
    assert old_bubble.parent() is page.messages
    bubbles = [
        page.messages_layout.itemAt(index).widget()
        for index in range(page.messages_layout.count() - 1)
    ]
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
        "random-id", "导入角色", "", card, "now", "now"
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
    assert ordinary.badge is None
    assert "内置角色" in row.accessibleName()


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
    assert page.role_memory_enabled.isChecked()
    assert "伴界 BanVerse" in labels
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
    composer = ChatComposer()
    for widget in (assistant, user, composer):
        qtbot.addWidget(widget)

    assistant.set_chat_width(360)
    user.set_chat_width(360)
    composer.resize(360, 130)
    composer.show()
    qtbot.wait(10)

    assert assistant.bubble.width() <= 336
    assert user.bubble.width() <= 336
    assert assistant.text_label.maximumWidth() <= 312
    assert user.text_label.maximumWidth() <= 312
    assert composer.editor.width() >= 320
    assert composer.editor.height() == 52
    assert composer.attach_button.height() >= 44
    assert composer.sticker_button.height() >= 44
    assert composer.action.height() >= 44


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


def test_android_settings_and_character_rows_are_touch_ready(
    monkeypatch, tmp_path, qtbot
):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")

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
