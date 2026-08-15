from pathlib import Path
from threading import Event

from PySide6.QtCore import QObject, Qt, QThread, Signal
from PySide6.QtGui import QImage

import deepseek_cli.desktop.ui.main_window as main_window_module
from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.ai_features import (
    ReplySegment,
    deserialize_reply_segments,
)
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import (
    CharacterRepository,
    ChatRepository,
    SettingsRepository,
)
from deepseek_cli.desktop.ui.main_window import MainWindow
from deepseek_cli.gateway import Message, StreamDelta


class FakeCredentials:
    def get_api_key(self):
        return "test-key"

    def save_api_key(self, _value):
        return None

    def clear_api_key(self):
        return None

    def get_image_api_key(self):
        return "image-test-key"

    def save_image_api_key(self, _value):
        return None

    def clear_image_api_key(self):
        return None

    def get_google_image_api_key(self):
        return "google-image-test-key"

    def save_google_image_api_key(self, _value):
        return None

    def clear_google_image_api_key(self):
        return None

    def get_siliconflow_api_key(self):
        return "siliconflow-test-key"

    def get_grsai_text_api_key(self):
        return "grsai-text-test-key"

    def get_siliconflow_image_api_key(self):
        return "siliconflow-image-test-key"

    def get_grsai_image_api_key(self):
        return "grsai-image-test-key"

    def get_siliconflow_tts_api_key(self):
        return "siliconflow-tts-test-key"

    def save_siliconflow_api_key(self, _value):
        return None

    def clear_siliconflow_api_key(self):
        return None


class FakeNotificationSound:
    def __init__(self):
        self.play_count = 0
        self.shutdown_called = False

    def play(self):
        self.play_count += 1
        return True

    def shutdown(self):
        self.shutdown_called = True


class FakeSpeech(QObject):
    state_changed = Signal(str, str)

    def __init__(self):
        super().__init__()
        self.calls = []

    def speak(self, message_key, text, profile):
        self.calls.append((message_key, text, profile))

    def stop(self):
        return None

    def reload_provider(self):
        return None

    def shutdown(self):
        return None


class FeatureGateway:
    def stream_chat(
        self, _model, _messages, *, system_prompt="", temperature=None
    ):
        if "Character Card V2 编剧" in system_prompt:
            yield StreamDelta(
                content=(
                    '{"name":"顾遥","description":"二十八岁的城市声音采集师，'
                    '短发，常背着旧录音机在街巷工作。","personality":"观察敏锐但'
                    '不擅长直接安慰人，说话简短，会用收集到的声音分享心情并尊重边界。",'
                    '"scenario":"雨夜里她从城市兴趣群添加了用户，正在屋檐下整理录音。",'
                    '"first_mes":"嗨，刚加上你。今晚的雨声很好听，你会给它取什么名字？",'
                    '"alternate_greetings":["用一段电车声和你打个招呼。",'
                    '"你喜欢城市里的哪一种声音？"],"mes_example":"<START>\\n'
                    '{{user}}: 今天录到了什么？\\n{{char}}: 屋檐雨，还有末班车关门前的提示音。",'
                    '"creator_notes":"克制、敏锐的都市声音采集师。",'
                    '"tags":["现代都市","声音采集","慢热"]}'
                )
            )
        elif "列表摘要与连续性记录器" in system_prompt:
            yield StreamDelta(
                content=(
                    '{"summary":"双方继续讨论未完成的调查",'
                    '"role_state":{"scene":{"location":"旧宅",'
                    '"time":"夜晚","ongoing_action":"调查线索"},'
                    '"character_state":{"mood":"专注",'
                    '"current_desire":"查明真相"},'
                    '"relationship":{"stage":"合作",'
                    '"preferred_address":"","boundaries":[]},'
                    '"user_facts":[],"shared_memories":[],'
                    '"open_threads":["继续调查"],'
                    '"recent_patterns":["以问题收尾"]}}'
                )
            )
        elif "聊天列表摘要器" in system_prompt:
            yield StreamDelta(content="AI 摘要：双方继续讨论未完成的调查")
        elif "自主分享图片" in system_prompt:
            yield StreamDelta(content='{"send_image":false,"prompt":""}')
        elif "## 首次开场" in system_prompt:
            yield StreamDelta(content="晚上好。刚路过一家亮着暖灯的小店，你今天过得怎么样？")
        elif "## 主动消息" in system_prompt:
            yield StreamDelta(content="夜色不错。上次的线索，你还想继续查吗？")
        else:
            yield StreamDelta(content="这是需要完整保留在聊天详情中的角色回复。")


def test_reply_delay_grows_with_next_segment_length():
    short = ReplySegment("dialogue", text="好呀。")
    long = ReplySegment(
        "dialogue",
        text="我刚走到楼下，外面正好下起小雨，等我把伞撑开再慢慢跟你说。",
    )

    first_delay = main_window_module.MessageFlowController._reply_delay_ms(
        short, first=True
    )
    short_delay = main_window_module.MessageFlowController._reply_delay_ms(
        short
    )
    long_delay = main_window_module.MessageFlowController._reply_delay_ms(
        long
    )

    assert 900 <= first_delay <= 3_200
    assert 650 <= short_delay <= 2_800
    assert long_delay > short_delay


def test_main_window_uses_single_column_android_navigation(
    tmp_path, qtbot, monkeypatch
):
    monkeypatch.setenv("DEEPSEEK_CHAT_PLATFORM", "android")
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    chats.create_conversation(
        title="移动端会话", opening_message="移动端历史消息"
    )

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
    )
    qtbot.addWidget(window)

    assert window._mobile is True
    assert window.minimumWidth() == 320
    assert window._mobile_body is not None
    original_bubble = window.chat_page._message_bubbles()[0]
    window._show_messages()
    assert window._mobile_body.currentWidget() is window.conversations

    window._open_current_mobile_conversation(
        window.conversations.list.currentItem()
    )
    assert window._mobile_body.currentWidget() is window.content
    assert window.content.currentWidget() is window.chat_page
    assert window.chat_page._message_bubbles()[0] is original_bubble

    window._show_characters()
    assert window._mobile_body.currentWidget() is window.content
    assert window.content.currentWidget() is window.characters_page

    window.close()
    database.close()


def test_desktop_reopening_conversation_scrolls_to_latest(tmp_path, qtbot):
    """桌面端打开/重开/切回会话时自动滚动到最新消息。"""

    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    conversation = chats.create_conversation(title="长对话")
    for index in range(30):
        turn = chats.create_turn(
            conversation.id,
            f"用户问题 {index}：这是一段用于换行测试的较长内容。",
            "deepseek-v4-flash",
        )
        chats.complete_turn(
            turn.id,
            f"回答 {index}：这是一段比较长的助手回复，用于验证打开会话时"
            "自动滚动到最新消息。" + "更多内容。" * 20,
        )

    window = MainWindow(chats, characters, settings, FakeCredentials())
    qtbot.addWidget(window)
    window.show()
    bar = window.chat_page.scroll.verticalScrollBar()
    qtbot.waitUntil(lambda: bar.maximum() > 0, timeout=2000)
    qtbot.waitUntil(
        lambda: bar.maximum() - bar.value() <= 40, timeout=2000
    )

    # 用户上滑到顶后重新打开同一会话（already_loaded，不重新 load）应滚回最新
    bar.setValue(0)
    window._open_conversation(conversation.id)
    qtbot.waitUntil(
        lambda: bar.maximum() - bar.value() <= 40, timeout=2000
    )

    # 切到角色页再切回消息页，也应滚回最新消息
    bar.setValue(0)
    window._show_characters()
    window._show_messages()
    qtbot.waitUntil(
        lambda: bar.maximum() - bar.value() <= 40, timeout=2000
    )

    window.close()
    database.close()


def test_main_window_routes_images_to_siliconflow(tmp_path, qtbot):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    settings.set("siliconflow_image_model", "sf-image-test")
    settings.set("siliconflow_image_size", "1280x720")
    settings.set("siliconflow_vision_model", "sf-vision-test")
    chats.create_conversation()
    captured = {}
    expected_service = object()

    def image_factory(api_key, **kwargs):
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return expected_service

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        image_service_factory=image_factory,
    )
    qtbot.addWidget(window)

    assert window._create_image_service() is expected_service
    assert captured == {
        "api_key": "siliconflow-image-test-key",
        "kwargs": {
            "image_model": "sf-image-test",
            "image_size": "1280x720",
            "vision_model": "sf-vision-test",
        },
    }

    window.close()
    database.close()


def test_main_window_routes_images_to_grsai(tmp_path, qtbot):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    settings.set("image_provider", "grsai")
    settings.set("grsai_image_base_url", "https://grsaiapi.com/v1")
    settings.set("grsai_image_model", "grs-image-test")
    settings.set("grsai_image_size", "1536x1024")
    settings.set("grsai_vision_model", "grs-vision-test")
    chats.create_conversation()
    captured = {}
    expected_service = object()

    def image_factory(api_key, **kwargs):
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return expected_service

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        image_service_factory=image_factory,
    )
    qtbot.addWidget(window)

    assert window._create_image_service() is expected_service
    assert captured == {
        "api_key": "grsai-image-test-key",
        "kwargs": {
            "base_url": "https://grsaiapi.com/v1",
            "image_model": "grs-image-test",
            "image_size": "1536x1024",
            "vision_model": "grs-vision-test",
        },
    }

    window.close()
    database.close()


def test_main_window_routes_all_text_workflows_to_selected_grsai(
    tmp_path, qtbot, monkeypatch
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    settings.set("text_provider", "grsai")
    settings.set("grsai_text_base_url", "https://grsaiapi.com/v1")
    settings.set("grsai_text_model", "gemini-text-test")
    chats.create_conversation()
    captured = {}
    expected_gateway = object()

    def gateway_factory(api_key, **kwargs):
        captured["api_key"] = api_key
        captured["kwargs"] = kwargs
        return expected_gateway

    monkeypatch.setattr(main_window_module, "GrsAiGateway", gateway_factory)
    window = MainWindow(chats, characters, settings, FakeCredentials())
    qtbot.addWidget(window)

    api_key = window._text_api_key()
    assert api_key == "grsai-text-test-key"
    assert window._create_text_gateway(api_key) is expected_gateway
    assert captured == {
        "api_key": "grsai-text-test-key",
        "kwargs": {
            "base_url": "https://grsaiapi.com/v1",
            "model": "gemini-text-test",
        },
    }
    assert window.chat_page.model_combo.currentText() == (
        "GRS AI · gemini-text-test"
    )
    assert not window.chat_page.model_combo.isEnabled()
    assert window.settings_page.default_model.currentText() == (
        "GRS AI · gemini-text-test"
    )
    assert window.windowTitle() == "伴界 BanVerse"

    window.close()
    database.close()


def test_grsai_gateway_is_constructed_before_worker_leaves_ui_thread(
    tmp_path, qtbot, monkeypatch
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    settings.set("text_provider", "grsai")
    settings.set("grsai_text_base_url", "https://grsaiapi.com/v1")
    conversation = chats.create_conversation()
    constructor_threads = []

    class Gateway:
        def stream_chat(
            self, _model, _messages, *, system_prompt="", temperature=None
        ):
            yield StreamDelta(content="线程边界正常")

    def gateway_factory(_api_key, **_kwargs):
        constructor_threads.append(QThread.currentThread())
        return Gateway()

    monkeypatch.setattr(main_window_module, "GrsAiGateway", gateway_factory)
    window = MainWindow(chats, characters, settings, FakeCredentials())
    qtbot.addWidget(window)

    window._send("测试 GRS AI 对话")
    qtbot.waitUntil(
        lambda: bool(chats.list_turns(conversation.id))
        and chats.list_turns(conversation.id)[0].status == "completed"
        and window._thread is None,
        timeout=5_000,
    )

    assert constructor_threads
    assert all(thread is window.thread() for thread in constructor_threads)
    assert chats.list_turns(conversation.id)[0].assistant_content == (
        "线程边界正常"
    )
    window.close()
    database.close()


def test_character_conversations_rotate_alternate_greetings(
    tmp_path, qtbot, monkeypatch
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("会轮换开场的角色")
    card["data"]["first_mes"] = "第一句"
    card["data"]["alternate_greetings"] = ["第二句", "第三句"]
    character = characters.create(card)
    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
    )
    qtbot.addWidget(window)
    # 本测试只验证开场白模板轮换；关闭 AI 主动开场避免真实请求与模板被清空。
    monkeypatch.setattr(window, "_send_ai_opening", lambda *a, **k: None)

    for _ in range(4):
        window._new_character_conversation(character.id)

    openings = [
        row["opening_message"]
        for row in database.connection.execute(
            """SELECT opening_message FROM conversations
               WHERE character_id = ? ORDER BY rowid""",
            (character.id,),
        )
    ]
    assert openings == ["第一句", "第二句", "第三句", "第一句"]
    window.close()
    database.close()


def test_new_character_conversation_ai_opening_clears_template(
    tmp_path, qtbot
):
    """新建角色会话由 AI 主动生成开场白；成功后清空模板防重复。"""

    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("AI 开场角色")
    card["data"]["first_mes"] = "模板开场白"
    card["data"]["description"] = "会主动开场测试角色。"
    character = characters.create(card)
    calls = []

    class RecordingGateway(FeatureGateway):
        def stream_chat(
            self, model, messages, *, system_prompt="", temperature=None
        ):
            calls.append((list(messages), system_prompt))
            yield from super().stream_chat(
                model,
                messages,
                system_prompt=system_prompt,
                temperature=temperature,
            )

    gateway = RecordingGateway()
    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: gateway,
    )
    qtbot.addWidget(window)

    window._new_character_conversation(character.id)
    conversation_id = database.connection.execute(
        "SELECT id FROM conversations ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    assert window._conversation_id == conversation_id
    assert window.chat_page.conversation_id == conversation_id

    # 等待 AI 开场请求完成（proactive turn 落库）
    qtbot.waitUntil(
        lambda: len(chats.list_turns(conversation_id)) == 1
        and chats.list_turns(conversation_id)[-1].status == "completed",
        timeout=5_000,
    )
    turn = chats.list_turns(conversation_id)[-1]
    assert turn.origin == "proactive"
    assert turn.assistant_content.startswith("晚上好")
    opening_calls = [
        messages for messages, prompt in calls if "## 首次开场" in prompt
    ]
    assert len(opening_calls) == 1
    assert [message.role for message in opening_calls[0]] == ["user"]
    assert all(
        "模板开场白" not in message.content for message in opening_calls[0]
    )
    # 模板被清空，后续历史不再重复注入模板
    assert chats.get_conversation(conversation_id).opening_message == ""
    assert [m.role for m in chats.completed_history(conversation_id)] == [
        "assistant"
    ]
    # 等投递与后台线程全部空闲后再关闭，避免迟到的流回调访问已关闭数据库
    qtbot.waitUntil(
        lambda: window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=5_000,
    )
    window.close()
    database.close()


def test_ai_opening_without_api_key_falls_back_to_template(
    tmp_path, qtbot, monkeypatch
):
    """无 API Key 时不触发 AI 开场，保留角色模板开场白。"""

    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("无 key 开场角色")
    card["data"]["first_mes"] = "模板开场白"
    character = characters.create(card)
    window = MainWindow(chats, characters, settings, FakeCredentials())
    qtbot.addWidget(window)
    monkeypatch.setattr(window, "_text_api_key", lambda: "")

    window._new_character_conversation(character.id)
    conversation_id = database.connection.execute(
        "SELECT id FROM conversations ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]

    assert window._thread is None
    assert chats.get_conversation(conversation_id).opening_message == (
        "模板开场白"
    )
    assert len(chats.list_turns(conversation_id)) == 0
    assert window.chat_page._message_bubbles()[0].text_label.text() == (
        "模板开场白"
    )
    window.close()
    database.close()


def test_ai_opening_failure_falls_back_without_failed_bubble(
    tmp_path, qtbot
):
    """首次开场请求失败时删除临时轮次，并显示角色模板兜底。"""

    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("开场失败角色")
    card["data"]["first_mes"] = "失败后的模板开场白"
    character = characters.create(card)

    class FailingOpeningGateway(FeatureGateway):
        def stream_chat(
            self, model, messages, *, system_prompt="", temperature=None
        ):
            if "## 首次开场" in system_prompt:
                raise RuntimeError("opening unavailable")
            yield from super().stream_chat(
                model,
                messages,
                system_prompt=system_prompt,
                temperature=temperature,
            )

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: FailingOpeningGateway(),
    )
    qtbot.addWidget(window)

    window._new_character_conversation(character.id)
    conversation_id = database.connection.execute(
        "SELECT id FROM conversations ORDER BY rowid DESC LIMIT 1"
    ).fetchone()[0]
    qtbot.waitUntil(lambda: window._thread is None, timeout=5_000)

    conversation = chats.get_conversation(conversation_id)
    assert conversation is not None
    assert conversation.opening_message == "失败后的模板开场白"
    assert chats.list_turns(conversation_id) == []
    qtbot.waitUntil(
        lambda: bool(window.chat_page._message_bubbles())
        and window.chat_page._message_bubbles()[0].text_label.text()
        == "失败后的模板开场白",
        timeout=2_000,
    )
    bubbles = window.chat_page._message_bubbles()
    assert len(bubbles) == 1
    assert bubbles[0].text_label.text() == "失败后的模板开场白"
    window.close()
    database.close()


def test_main_window_sends_sticker_as_semantic_user_message(tmp_path, qtbot):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    conversation = chats.create_conversation()
    calls = []

    class Gateway:
        def stream_chat(
            self, _model, messages, *, system_prompt="", temperature=None
        ):
            calls.append((list(messages), system_prompt))
            if "聊天列表摘要器" in system_prompt:
                yield StreamDelta(content="用户发来抱抱表情，角色温暖回应")
            else:
                yield StreamDelta(content="收到你的抱抱啦，也抱抱你。")

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: Gateway(),
    )
    qtbot.addWidget(window)

    window._send_sticker("hug")
    qtbot.waitUntil(
        lambda: bool(chats.list_turns(conversation.id))
        and chats.list_turns(conversation.id)[0].status == "completed"
        and window._thread is None
        and window._summary_thread is None,
        timeout=5_000,
    )

    turn = chats.list_turns(conversation.id)[0]
    assert turn.user_sticker == "hug"
    assert turn.user_content == "我发了一个“抱抱”的表情。"
    assert turn.assistant_content == "收到你的抱抱啦，也抱抱你。"
    chat_calls = [
        messages for messages, system in calls if "聊天列表摘要器" not in system
    ]
    assert chat_calls[0][-1] == Message(
        "user", "我发了一个“抱抱”的表情。"
    )
    window._send_sticker("unknown-sticker")
    assert len(chats.list_turns(conversation.id)) == 1

    window.close()
    database.close()


def test_streamed_answer_stays_hidden_until_local_classification(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    conversation = chats.create_conversation()
    chunk_processed = Event()
    release = Event()

    class Gateway:
        def stream_chat(
            self, _model, _messages, *, system_prompt="", temperature=None
        ):
            if "聊天列表摘要器" in system_prompt:
                yield StreamDelta(content="完成分段展示")
                return
            yield StreamDelta(content="这段完整回复绝不能提前显示。")
            chunk_processed.set()
            release.wait(2)

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: Gateway(),
    )
    qtbot.addWidget(window)

    window._send("测试隐藏流")
    qtbot.waitUntil(chunk_processed.is_set, timeout=3_000)
    qtbot.waitUntil(
        lambda: "绝不能提前显示" in window._answer,
        timeout=3_000,
    )

    assert window.chat_page._stream_bubble is not None
    assert "绝不能提前显示" not in (
        window.chat_page._stream_bubble.text_label.text()
    )
    release.set()
    qtbot.waitUntil(
        lambda: chats.list_turns(conversation.id)[0].status == "completed"
        and window._thread is None
        and window._delivery is None
        and window._summary_thread is None,
        timeout=5_000,
    )

    window.close()
    database.close()


def test_main_window_generates_summary_and_proactive_assistant_turn(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("测试角色")
    card["data"]["description"] = "善于调查的测试角色。"
    character = characters.create(card)
    conversation = chats.create_conversation(
        title=character.name,
        character_id=character.id,
    )
    notification = FakeNotificationSound()
    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: FeatureGateway(),
        notification_sound=notification,
    )
    qtbot.addWidget(window)

    window._send("继续调查")
    qtbot.waitUntil(
        lambda: chats.get_conversation(conversation.id).summary_status
        == "ready",
        timeout=5_000,
    )
    qtbot.waitUntil(
        lambda: window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=5_000,
    )

    summarized = chats.get_conversation(conversation.id)
    assert summarized.ai_summary == "双方继续讨论未完成的调查"
    assert "继续调查" in summarized.role_state_json
    assert chats.completed_history(conversation.id) == [
        Message("user", "继续调查"),
        Message("assistant", "这是需要完整保留在聊天详情中的角色回复。"),
    ]

    window._send_proactive_message()
    qtbot.waitUntil(
        lambda: len(chats.list_turns(conversation.id)) == 2
        and chats.list_turns(conversation.id)[-1].status == "completed",
        timeout=5_000,
    )
    turns = chats.list_turns(conversation.id)
    assert turns[-1].origin == "proactive"
    assert turns[-1].user_content == ""
    assert turns[-1].assistant_content.startswith("夜色不错")
    qtbot.waitUntil(
        lambda: window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=5_000,
    )
    assert notification.play_count == 2

    window.close()
    database.close()


def test_random_character_is_generated_as_new_contact_without_switching_chat(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    settings.set("character_discovery_enabled", "true")
    settings.set("character_discovery_daily_limit", "1")
    existing = characters.create(empty_card("已有角色"))
    current = chats.create_conversation(
        title=existing.name, character_id=existing.id
    )
    notification = FakeNotificationSound()
    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: FeatureGateway(),
        notification_sound=notification,
    )
    qtbot.addWidget(window)

    window._generate_random_character()
    qtbot.waitUntil(
        lambda: len(characters.list()) == 2
        and window._character_discovery_thread is None,
        timeout=5_000,
    )

    discovered = next(
        character for character in characters.list() if character.name == "顾遥"
    )
    contact = next(
        conversation
        for conversation in chats.list_conversations()
        if conversation.character_id == discovered.id
    )
    assert contact.opening_message.startswith("嗨，刚加上你")
    assert window._conversation_id == current.id
    assert settings.get("character_discovery_count") == "1"
    assert settings.get("character_discovery_last_name") == "顾遥"
    assert notification.play_count == 1

    # 当日上限生效：再次到期不会调用模型或新增联系人。
    window._generate_random_character()
    assert len(characters.list()) == 2

    window.close()
    database.close()


def test_main_window_understands_user_image_and_character_autonomously_shares_image(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("林小满")
    card["data"]["description"] = "现代都市里的成年设计师，短发。"
    character = characters.create(card)
    conversation = chats.create_conversation(character_id=character.id)

    source = tmp_path / "sent.png"
    generated = tmp_path / "generated-source.png"
    for path, color in (
        (source, Qt.GlobalColor.blue),
        (generated, Qt.GlobalColor.yellow),
    ):
        image = QImage(640, 360, QImage.Format.Format_RGB32)
        image.fill(color)
        assert image.save(str(path), "PNG")

    class ImageService:
        def describe_image(self, path, user_text):
            assert path.endswith((".png", ".jpg"))
            assert user_text == "看看今天的天空"
            return "蓝色天空下有一排城市建筑。"

        def generate_image(self, prompt):
            assert prompt == (
                "现代都市雨后傍晚，短发成年女设计师站在亮灯花店门口，"
                "手里拿着一束花，暖色生活摄影感"
            )
            return generated.read_bytes()

    class Gateway:
        def __init__(self):
            self.calls = []

        def stream_chat(
            self, _model, messages, *, system_prompt="", temperature=None
        ):
            self.calls.append((list(messages), system_prompt))
            if "列表摘要与连续性记录器" in system_prompt:
                yield StreamDelta(
                    content=(
                        '{"summary":"分享了一张天空照片和一幅生成图",'
                        '"role_state":{"scene":{"location":"城市",'
                        '"time":"雨后","ongoing_action":"分享天空"},'
                        '"character_state":{"mood":"轻松",'
                        '"current_desire":"分享见闻"},'
                        '"relationship":{"stage":"熟悉",'
                        '"preferred_address":"","boundaries":[]},'
                        '"user_facts":[],"shared_memories":[],'
                        '"open_threads":["花店"],"recent_patterns":[]}}'
                    )
                )
            elif "聊天列表摘要器" in system_prompt:
                yield StreamDelta(content="分享了一张天空照片和一幅生成图")
            elif "自主分享图片" in system_prompt:
                yield StreamDelta(
                    content=(
                        '{"send_image":true,"prompt":"现代都市雨后傍晚，'
                        '短发成年女设计师站在亮灯花店门口，手里拿着一束花，'
                        '暖色生活摄影感"}'
                    )
                )
            else:
                yield StreamDelta(content="天空很蓝，像是刚下过雨。")

    gateway = Gateway()
    notification = FakeNotificationSound()
    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: gateway,
        image_service_factory=lambda *_args, **_kwargs: ImageService(),
        notification_sound=notification,
        media_root=tmp_path / "appdata",
    )
    qtbot.addWidget(window)

    window._send("看看今天的天空", str(source))
    qtbot.waitUntil(
        lambda: bool(chats.list_turns(conversation.id))
        and bool(chats.list_turns(conversation.id)[0].assistant_image_path)
        and window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=5_000,
    )
    first = chats.list_turns(conversation.id)[0]
    assert first.status == "completed"
    assert first.user_image_path
    assert (tmp_path / "appdata") in Path(first.user_image_path).parents
    assert first.user_image_description == "蓝色天空下有一排城市建筑。"
    chat_requests = [
        messages
        for messages, system in gateway.calls
        if "聊天列表摘要器" not in system
    ]
    assert "图片理解服务" in chat_requests[0][-1].content
    assert "城市建筑" in chat_requests[0][-1].content

    assert len(chats.list_turns(conversation.id)) == 1
    generated_turn = chats.list_turns(conversation.id)[0]
    assert generated_turn.origin == "user"
    assert generated_turn.assistant_content == "天空很蓝，像是刚下过雨。"
    assert generated_turn.assistant_image_path
    assert Path(generated_turn.assistant_image_path).is_file()
    assert "[助手生成了一张图片。]" in chats.completed_history(
        conversation.id
    )[-1].content
    assert notification.play_count == 2

    window._send("花店后来怎么样？")
    qtbot.waitUntil(
        lambda: len(chats.list_turns(conversation.id)) == 2
        and chats.list_turns(conversation.id)[-1].status == "completed"
        and window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=5_000,
    )
    assert not chats.list_turns(conversation.id)[-1].assistant_image_path
    assert sum(
        "自主分享图片" in system for _messages, system in gateway.calls
    ) == 1
    assert notification.play_count == 3

    window.close()
    database.close()


def test_explicit_user_image_request_generates_when_ai_decision_is_false(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("林小满")
    card["data"]["description"] = "现代都市里的成年短发女设计师。"
    character = characters.create(card)
    conversation = chats.create_conversation(character_id=character.id)
    generated = tmp_path / "requested.png"
    image = QImage(320, 320, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.magenta)
    assert image.save(str(generated), "PNG")
    generated_prompts = []
    decision_calls = []

    class ImageService:
        def generate_image(self, prompt):
            generated_prompts.append(prompt)
            return generated.read_bytes()

    class Gateway:
        def stream_chat(
            self, _model, _messages, *, system_prompt="", temperature=None
        ):
            if "列表摘要与连续性记录器" in system_prompt:
                yield StreamDelta(
                    content=(
                        '{"summary":"用户索要下班路上的照片",'
                        '"role_state":{"scene":{},"character_state":{},'
                        '"relationship":{},"user_facts":[],'
                        '"shared_memories":[],"open_threads":[],'
                        '"recent_patterns":[]}}'
                    )
                )
            elif "自主分享图片" in system_prompt:
                decision_calls.append(system_prompt)
                yield StreamDelta(
                    content='{"send_image":false,"prompt":""}'
                )
            else:
                yield StreamDelta(content="好呀，我挑一张刚才路上拍的发给你。")

    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: Gateway(),
        image_service_factory=lambda *_args, **_kwargs: ImageService(),
        media_root=tmp_path / "appdata",
    )
    qtbot.addWidget(window)

    window._send("给我发一张你下班路上的照片吧")
    qtbot.waitUntil(
        lambda: bool(chats.list_turns(conversation.id))
        and bool(chats.list_turns(conversation.id)[0].assistant_image_path)
        and window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=7_000,
    )

    turn = chats.list_turns(conversation.id)[0]
    assert decision_calls
    assert len(generated_prompts) == 1
    assert "用户明确请求" in generated_prompts[0]
    assert "下班路上" in generated_prompts[0]
    assert Path(turn.assistant_image_path).is_file()

    window.close()
    database.close()


def test_role_reply_is_hidden_then_delivered_as_typed_segments_and_image(
    tmp_path, qtbot
):
    database = Database(tmp_path / "chat.db")
    chats = ChatRepository(database)
    characters = CharacterRepository(database)
    settings = SettingsRepository(database)
    card = empty_card("林小满")
    card["data"]["description"] = "现代都市里的成年短发女设计师。"
    character = characters.create(card)
    conversation = chats.create_conversation(character_id=character.id)
    generated = tmp_path / "generated.png"
    image = QImage(320, 320, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.cyan)
    assert image.save(str(generated), "PNG")

    class ImageService:
        def generate_image(self, prompt):
            assert "成年短发女设计师" in prompt
            assert "窗边晚霞" in prompt
            return generated.read_bytes()

    class Gateway:
        prompts = []

        def stream_chat(
            self, _model, _messages, *, system_prompt="", temperature=None
        ):
            self.prompts.append(system_prompt)
            if "列表摘要与连续性记录器" in system_prompt:
                yield StreamDelta(
                    content=(
                        '{"summary":"小满分享窗边晚霞",'
                        '"role_state":{"scene":{},"character_state":{},'
                        '"relationship":{},"user_facts":[],'
                        '"shared_memories":[],"open_threads":[],'
                        '"recent_patterns":[]}}'
                    )
                )
            else:
                yield StreamDelta(
                    content=(
                        "刚下班，我正好赶上今天的晚霞。"
                        "（她走到窗边，轻轻拉开窗帘）"
                        "（发送图片：窗边晚霞映在城市玻璃幕墙上）"
                        "你看，云边像被点亮了一样。"
                    )
                )

    gateway = Gateway()
    speech = FakeSpeech()
    window = MainWindow(
        chats,
        characters,
        settings,
        FakeCredentials(),
        gateway_factory=lambda _key: gateway,
        image_service_factory=lambda *_args, **_kwargs: ImageService(),
        speech=speech,
        media_root=tmp_path / "appdata",
    )
    qtbot.addWidget(window)

    window._send("今天过得怎么样？")
    qtbot.waitUntil(
        lambda: bool(chats.list_turns(conversation.id))
        and bool(chats.list_turns(conversation.id)[0].assistant_image_path)
        and window._thread is None
        and window._summary_thread is None
        and window._image_thread is None
        and window._delivery is None,
        timeout=7_000,
    )

    turn = chats.list_turns(conversation.id)[0]
    segments = deserialize_reply_segments(turn.assistant_segments_json)
    assert [segment.kind for segment in segments] == [
        "dialogue",
        "narration",
        "image",
        "dialogue",
    ]
    assert segments[2].image_path == turn.assistant_image_path
    assert "发送图片" not in turn.assistant_content
    assert any("自主分享图片" in prompt for prompt in gateway.prompts)
    assert len(speech.calls) == 1
    assert speech.calls[0][1] == (
        "刚下班，我正好赶上今天的晚霞。\n"
        "你看，云边像被点亮了一样。"
    )
    assert "走到窗边" not in speech.calls[0][1]
    visible_texts = [
        widget.text_label.text()
        for widget in window.chat_page._message_bubbles()
        if widget.text_label is not None and widget.text_label.text().strip()
    ]
    assert visible_texts.count("刚下班，我正好赶上今天的晚霞。") == 1
    assert visible_texts.count("（她走到窗边，轻轻拉开窗帘）") == 1
    assert visible_texts.count("你看，云边像被点亮了一样。") == 1
    assert not any(
        "刚下班，我正好赶上今天的晚霞。"
        "（她走到窗边，轻轻拉开窗帘）" in text
        for text in visible_texts
    )

    window.close()
    database.close()
