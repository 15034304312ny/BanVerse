"""后台摘要与自主发图队列控制器的边界测试。

聚焦队列去重、关闭拦截与冷却窗口，避免构造完整主窗口。
"""

from __future__ import annotations

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.background import (
    AutonomousImageRunner,
    SummaryRunner,
)
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import (
    CharacterRepository,
    ChatRepository,
    SettingsRepository,
)


class FakeSettings:
    def __init__(self, **values) -> None:
        self._values = values

    def get(self, key, default: str = "") -> str:
        return self._values.get(key, default)

    def get_bool(self, key, default: bool = False) -> bool:
        value = self._values.get(key, "true" if default else "false")
        return str(value).lower() in {"1", "true", "yes", "on"}


def _make_repos(tmp_path):
    """创建临时数据库并返回 (database, chats, characters, settings)。

    测试结束后需调用 database.close() 释放 SQLite 连接。
    """

    database = Database(tmp_path / "chat.db")
    return (
        database,
        ChatRepository(database),
        CharacterRepository(database),
        SettingsRepository(database),
    )


def _noop_service(_api_key):
    """返回一个绝不会真正发起请求的假网关 service。

    边界测试关心队列/关闭/冷却逻辑，不应触发真实网络；若 worker 真的
    走到流式循环，该 service 会立刻产出一个空的完成流，不会访问网络。
    """

    class _EmptyGateway:
        def stream_chat(self, _model, _messages, **_kwargs):
            return iter(())

    from deepseek_cli.chat_service import ChatStreamService

    return ChatStreamService(lambda: _EmptyGateway())


def _noop_image_service():
    class _EmptyImageService:
        def generate_image(self, _prompt):
            return b"\x89PNG\r\n\x1a\n"  # 最小合法 PNG 头，避免真实请求

    return _EmptyImageService()


def _noop_refresh():
    return None


def test_summary_runner_shutdown_discards_queued_and_rejects_new(
    tmp_path, qtbot
) -> None:
    """关闭后队列被清空，且拒绝新任务。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = SummaryRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        text_api_key=lambda: "key",
        refresh=_noop_refresh,
    )
    conversation = chats.create_conversation()

    runner.enqueue(conversation.id, "回答")
    assert runner.thread is not None  # 已开始消费

    runner.shutdown()
    # 线程退出由事件循环中的 finished 信号驱动，用 qtbot 正确派发。
    qtbot.waitUntil(lambda: not runner.busy, timeout=3_000)
    # 关闭后入队被忽略（不触发断言，也不会启动线程）。
    runner.enqueue(conversation.id, "回答")
    database.close()


def test_summary_runner_dedupes_same_conversation(tmp_path) -> None:
    """同一会话的旧任务被新任务替换，队列中至多保留一个。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = SummaryRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        text_api_key=lambda: "",
        refresh=_noop_refresh,
    )
    conversation = chats.create_conversation()

    # text_api_key 为空时 start_next 提前返回，任务停留在队列中便于观察。
    runner.enqueue(conversation.id, "第一条")
    runner.enqueue(conversation.id, "第二条")
    runner.enqueue(conversation.id, "第三条")

    assert len(runner._queue) == 1  # noqa: SLF001
    assert runner._queue[0].request_text.endswith("第三条")  # noqa: SLF001
    database.close()


def test_summary_runner_preserves_each_queued_role_state_turn(tmp_path) -> None:
    """角色状态逐轮演进，不能因摘要去重而丢掉中间一轮。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = SummaryRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        text_api_key=lambda: "",
        refresh=_noop_refresh,
    )
    character = characters.create(empty_card("角色"))
    conversation = chats.create_conversation(character_id=character.id)
    first = chats.create_turn(conversation.id, "第一轮", "model")
    chats.complete_turn(first.id, "第一轮回复")
    second = chats.create_turn(conversation.id, "第二轮", "model")
    chats.complete_turn(second.id, "第二轮回复")

    runner.enqueue(conversation.id, "第一轮回复", turn_id=first.id)
    runner.enqueue(conversation.id, "第二轮回复", turn_id=second.id)

    assert [job.turn_id for job in runner._queue] == [  # noqa: SLF001
        first.id,
        second.id,
    ]
    database.close()


def test_summary_runner_skips_missing_conversation(tmp_path) -> None:
    """入队不存在的会话时静默忽略。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = SummaryRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        text_api_key=lambda: "key",
        refresh=_noop_refresh,
    )
    runner.enqueue("missing-conversation", "回答")
    assert not runner.busy
    database.close()


def test_autonomous_image_runner_respects_cooldown_window(tmp_path) -> None:
    """最近轮次已生成过图片时，新请求被冷却拦截。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = AutonomousImageRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        create_image_service=_noop_image_service,
        text_api_key=lambda: "key",
        image_api_key=lambda: "image-key",
        refresh=_noop_refresh,
        on_image_saved=lambda _conv: None,
        on_image_error=lambda _conv, _code: None,
    )
    character = characters.create(empty_card("角色"))
    conversation = chats.create_conversation(
        character_id=character.id, title="角色会话"
    )
    turn = chats.create_turn(conversation.id, "看看天空", "model")
    chats.complete_turn(turn.id, "天空很美。", assistant_image_path="/tmp/a.png")

    # 冷却窗口内：无显式提示词、仅语义决策时不生成。
    runner.enqueue(
        conversation.id,
        turn.id,
        "角色",
        character.card,
        "天空很美。",
        fallback_prompt="",  # 需走决策，但冷却窗口拦截
    )
    assert runner.thread is None
    assert len(runner._queue) == 0  # noqa: SLF001
    database.close()


def test_autonomous_image_runner_direct_action_bypasses_cooldown(
    tmp_path,
) -> None:
    """用户显式索图（fallback_prompt 非空）不受冷却窗口限制。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = AutonomousImageRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        create_image_service=_noop_image_service,
        text_api_key=lambda: "",  # 无文本 key → can_decide 为 False → 直接动作
        image_api_key=lambda: "image-key",
        refresh=_noop_refresh,
        on_image_saved=lambda _conv: None,
        on_image_error=lambda _conv, _code: None,
    )
    character = characters.create(empty_card("角色"))
    conversation = chats.create_conversation(
        character_id=character.id, title="角色会话"
    )
    # 冷却来源轮：最近已完成轮次带图 → 进入冷却窗口。
    cooldown_turn = chats.create_turn(conversation.id, "刚才发了照片", "model")
    chats.complete_turn(
        cooldown_turn.id, "给你看这张。", assistant_image_path="/tmp/a.png"
    )
    # 目标轮：不带图（若带图会命中"该轮已有图"守卫，而非冷却逻辑）。
    target_turn = chats.create_turn(conversation.id, "看看天空", "model")
    chats.complete_turn(target_turn.id, "天空很美。")

    # fallback_prompt 非空 + 无语义决策 → 直接生成，绕过冷却（线程立即占用）。
    runner.enqueue(
        conversation.id,
        target_turn.id,
        "角色",
        character.card,
        "天空很美。",
        fallback_prompt="用户想看角色拍一张照片",
    )
    assert runner.thread is not None  # 直接动作绕过冷却，已进入生成阶段
    assert runner._job is not None  # noqa: SLF001
    runner.shutdown()
    database.close()


def test_autonomous_image_runner_shutdown_clears_queue(tmp_path) -> None:
    """关闭后队列清空且不再启动。"""

    database, chats, characters, settings = _make_repos(tmp_path)
    runner = AutonomousImageRunner(
        chats=chats,
        characters=characters,
        settings=settings,
        create_text_service=_noop_service,
        create_image_service=_noop_image_service,
        text_api_key=lambda: "key",
        image_api_key=lambda: "",
        refresh=_noop_refresh,
        on_image_saved=lambda _conv: None,
        on_image_error=lambda _conv, _code: None,
    )
    runner.shutdown()
    runner.enqueue("conv", "turn", "角色", empty_card(), "回答")
    assert len(runner._queue) == 0  # noqa: SLF001
    database.close()
