"""MessageFlowController 的单元测试。

直接验证从主窗口抽取出来的主管线状态机：投递节奏、通知消费、忙态互锁、
停止/关闭。不依赖完整主窗口，只构造 controller 本身。
"""

from __future__ import annotations

from deepseek_cli.desktop.ai_features import ReplyPlan, ReplySegment
from deepseek_cli.desktop.flow import MessageFlowController, ReplyDelivery
from deepseek_cli.tts import TtsProfile


class FakeSettings:
    """仅提供 get_bool 的伪设置（flow 只依赖它）。"""

    def get_bool(self, _key: str, default: bool = False) -> bool:
        return default


def _delivery(*, segments, request_kind: str = "user", reasoning: str = ""):
    return ReplyDelivery(
        conversation_id="conv-1",
        turn_id="turn-1",
        plan=ReplyPlan(tuple(segments)),
        profile=TtsProfile(),
        reasoning=reasoning,
        request_kind=request_kind,
    )


def _controller(*, tts_auto_play: bool = True) -> MessageFlowController:
    return MessageFlowController(
        settings=FakeSettings(),
        tts_auto_play_check=lambda: tts_auto_play,
    )


def test_reply_delay_respects_first_segment_and_length() -> None:
    short = ReplySegment("dialogue", text="好呀。")
    long_segment = ReplySegment(
        "dialogue",
        text="我刚走到楼下，外面正好下起小雨，等我把伞撑开再慢慢跟你说。",
    )

    first = MessageFlowController._reply_delay_ms(short, first=True)
    normal = MessageFlowController._reply_delay_ms(short)
    longer = MessageFlowController._reply_delay_ms(long_segment)

    assert 900 <= first <= 3_200
    assert 650 <= normal <= 2_800
    assert longer > normal


def _run_delivery_to_completion(qtbot, controller, timeout: int = 5_000) -> None:
    """驱动 controller 的分段投递事件循环直至完成。"""

    qtbot.waitUntil(lambda: controller.delivery is None, timeout=timeout)


def test_begin_delivery_emits_segments_in_order_with_speech(qtbot) -> None:
    """对白段逐条投递，且只在第一个对白段触发一次 TTS。"""

    controller = _controller(tts_auto_play=True)
    segments = (
        ReplySegment("dialogue", text="第一句。"),
        ReplySegment("narration", text="她抬头看了看。"),
        ReplySegment("dialogue", text="第二句。"),
    )
    delivery = _delivery(segments=segments)
    emitted: list[str] = []
    speech_calls: list[tuple[str, str]] = []
    started: list[ReplyDelivery] = []

    controller.delivery_started.connect(lambda d: started.append(d))
    controller.delivery_segment.connect(
        lambda _i, seg, _r: emitted.append(seg.text)
    )
    controller.delivery_speech.connect(
        lambda key, text, _p: speech_calls.append((key, text))
    )
    controller.delivery_typing.connect(lambda _show: None)
    controller.delivery_finished.connect(lambda _d: None)

    controller.begin_delivery(delivery)
    _run_delivery_to_completion(qtbot, controller)

    assert started == [delivery]
    assert emitted == ["第一句。", "她抬头看了看。", "第二句。"]
    assert len(speech_calls) == 1
    assert speech_calls[0][0] == "turn:turn-1:segment:0"
    assert speech_calls[0][1] == "第一句。\n第二句。"


def test_delivery_reasoning_attached_only_to_first_segment(qtbot) -> None:
    """思考过程只在首段携带，之后清空。"""

    controller = _controller()
    segments = (
        ReplySegment("dialogue", text="第一句。"),
        ReplySegment("dialogue", text="第二句。"),
    )
    delivery = _delivery(segments=segments, reasoning="我先想了想。")
    reasoning_seen: list[str] = []

    controller.delivery_segment.connect(
        lambda _i, _seg, r: reasoning_seen.append(r)
    )
    controller.begin_delivery(delivery)
    _run_delivery_to_completion(qtbot, controller)

    assert reasoning_seen == ["我先想了想。", ""]


def test_notification_consumed_once_per_delivery(qtbot) -> None:
    """一条投递至多播放一次通知；首段后消费。"""

    controller = _controller()
    segments = (ReplySegment("dialogue", text="只有一句。"),)
    delivery = _delivery(segments=segments)
    notifications: list[bool] = []

    controller.delivery_notification.connect(lambda: notifications.append(True))
    controller.prepare_delivery(delivery)
    assert controller.delivery is None  # 待投递，尚未开始

    controller.begin_delivery(delivery)
    _run_delivery_to_completion(qtbot, controller)

    assert len(notifications) == 1


def test_busy_reflects_stream_pending_and_delivery(qtbot) -> None:
    """忙态覆盖进行中的投递；prepare_delivery 后立即占线。"""

    controller = _controller()
    assert not controller.busy
    delivery = _delivery(segments=(ReplySegment("dialogue", text="你好。"),))
    controller.prepare_delivery(delivery)
    assert controller.busy  # 有待投递计划

    # 清空待投递（模拟 stream_cleaned_up 分支）后不再忙。
    controller._pending_delivery = None  # noqa: SLF001 - 测试直接观察内部状态
    assert not controller.busy


def test_shutdown_clears_pending_and_stops_timer(qtbot) -> None:
    controller = _controller()
    delivery = _delivery(segments=(ReplySegment("dialogue", text="你好。"),))
    controller.prepare_delivery(delivery)
    controller.begin_delivery(delivery)

    controller.shutdown()

    assert controller.delivery is None
    assert not controller.busy
    assert not controller._delivery_timer.isActive()  # noqa: SLF001
