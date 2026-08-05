"""主消息流与真人式分段投递的状态机控制器。

从主窗口抽取的主管线：负责后台流式 worker 的生命周期、流式事件累积，
以及“完整回复 → 按真人节奏逐段投递”的状态机。主窗口不再直接持有线程、
投递与请求上下文，只通过 Qt 信号接收事件并执行 UI 动作与落库。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..tts import TtsProfile
from .ai_features import ReplyPlan, ReplySegment
from .background import launch_worker
from .workers import ChatWorker


@dataclass(frozen=True, slots=True)
class ReplyDelivery:
    """一条已完成回复及其投递所需信息。"""

    conversation_id: str
    turn_id: str
    plan: ReplyPlan
    profile: TtsProfile
    reasoning: str
    request_kind: str


class MessageFlowController(QObject):
    """主管线状态机。

    ``begin_stream`` 启动一个后台流式请求并累积增量；``prepare_delivery``
    登记完成事件产生的投递计划，由主窗口在 ``stream_cleaned_up`` 后决定
    是否 ``begin_delivery``。全部 UI 副作用通过信号交给主窗口。
    """

    reasoning_accumulated = Signal(str)
    content_accumulated = Signal(str)
    image_described = Signal(str, str)  # turn_id, description
    # conversation_id, turn_id, answer, reasoning, request_kind
    turn_completed = Signal(str, str, str, str, str)
    # turn_id, request_kind, error_code（空串表示取消）
    turn_aborted = Signal(str, str, str)
    # request_kind, request_conversation_id, pending_delivery（或 None）
    stream_cleaned_up = Signal(str, str, object)
    delivery_started = Signal(object)  # ReplyDelivery
    delivery_typing = Signal(bool)
    delivery_segment = Signal(int, object, str)  # index, ReplySegment, reasoning
    delivery_speech = Signal(str, str, object)  # message_key, text, profile
    delivery_notification = Signal()
    delivery_finished = Signal(object)  # ReplyDelivery

    def __init__(
        self,
        *,
        settings,
        tts_auto_play_check: Callable[[], bool] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._tts_auto_play_check = tts_auto_play_check
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._turn_id: str | None = None
        self._request_conversation_id: str | None = None
        self._request_kind = "user"
        self._answer = ""
        self._reasoning = ""
        self._notification_pending = False
        self._pending_delivery: ReplyDelivery | None = None
        self._delivery: ReplyDelivery | None = None
        self._delivery_segments: deque[tuple[int, ReplySegment]] = deque()
        self._delivery_reasoning = ""
        self._delivery_speech_started = False
        self._delivery_timer = QTimer(self)
        self._delivery_timer.setSingleShot(True)
        self._delivery_timer.timeout.connect(self._deliver_next_segment)

    # ---- 只读观测（主窗口与测试用） ----

    @property
    def busy(self) -> bool:
        """主管线是否占线（互锁闸门）。"""

        return (
            self._thread is not None
            or self._delivery is not None
            or self._pending_delivery is not None
        )

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def delivery(self) -> ReplyDelivery | None:
        return self._delivery

    @property
    def answer(self) -> str:
        return self._answer

    # ---- 发起请求 ----

    def begin_stream(
        self,
        *,
        service,
        model: str,
        history: Sequence,
        request_text: str,
        system_prompt: str = "",
        example_messages: Sequence = (),
        temperature: float | None = None,
        image_service=None,
        image_path: str = "",
        turn_id: str,
        conversation_id: str,
        request_kind: str = "user",
    ) -> None:
        """启动一个后台流式请求，并登记请求上下文。

        依赖的 HTTP 网关与图片服务必须由调用方在 UI 线程构造好再传入；
        worker 只在后台线程消费它们。
        """

        self._turn_id = turn_id
        self._request_conversation_id = conversation_id
        self._request_kind = request_kind
        self._notification_pending = False
        self._answer = ""
        self._reasoning = ""
        worker = ChatWorker(
            service,
            model,
            history,
            request_text,
            system_prompt=system_prompt,
            example_messages=example_messages,
            temperature=temperature,
            image_service=image_service,
            image_path=image_path,
        )
        worker.reasoning.connect(self._on_reasoning)
        worker.content.connect(self._on_content)
        worker.completed.connect(self._on_completed)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        worker.image_described.connect(self._on_image_described)
        self._worker = worker
        self._thread = launch_worker(
            self, worker, thread_finished=self._stream_finished
        )

    def stop(self) -> None:
        """取消当前流式请求（若在进行中）。"""

        if self._worker is not None:
            self._worker.cancel()

    # ---- 完成事件 ----

    def prepare_delivery(self, delivery: ReplyDelivery) -> None:
        """登记完成事件的投递计划，等待 stream_cleaned_up 后由主窗口启动。"""

        self._pending_delivery = delivery
        self._notification_pending = True

    def play_pending_notification(self) -> None:
        """播放待播通知（用于投递不属于当前可见会话的情形）。"""

        if self._notification_pending:
            self._notification_pending = False
            self.delivery_notification.emit()

    def begin_delivery(self, delivery: ReplyDelivery) -> None:
        """按真人节奏开始逐段投递一条已完成的回复。"""

        self._delivery = delivery
        self._delivery_segments = deque(
            (index, segment)
            for index, segment in enumerate(delivery.plan.segments)
            if segment.kind in {"dialogue", "narration"} and segment.text
        )
        self._delivery_reasoning = delivery.reasoning
        self._delivery_speech_started = False
        self.delivery_started.emit(delivery)
        if self._delivery_segments:
            self.delivery_typing.emit(True)
            self._delivery_timer.start(
                self._reply_delay_ms(
                    self._delivery_segments[0][1], first=True
                )
            )
        else:
            self._finish_reply_delivery()

    # ---- worker 事件 ----

    def _on_reasoning(self, text: str) -> None:
        self._reasoning += text
        self.reasoning_accumulated.emit(text)

    def _on_content(self, text: str) -> None:
        self._answer += text
        self.content_accumulated.emit(text)

    def _on_image_described(self, description: str) -> None:
        if self._turn_id:
            self.image_described.emit(self._turn_id, description)

    def _on_completed(self, answer: str) -> None:
        if self._turn_id and self._request_conversation_id:
            self.turn_completed.emit(
                self._request_conversation_id,
                self._turn_id,
                answer,
                self._reasoning,
                self._request_kind,
            )

    def _on_cancelled(self) -> None:
        self._notification_pending = False
        self.turn_aborted.emit(self._turn_id or "", self._request_kind, "")

    def _on_failed(self, error_code: str) -> None:
        self._notification_pending = False
        self.turn_aborted.emit(
            self._turn_id or "", self._request_kind, error_code
        )

    def _stream_finished(self) -> None:
        request_conversation_id = self._request_conversation_id or ""
        request_kind = self._request_kind
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
        self.stream_cleaned_up.emit(
            request_kind, request_conversation_id, pending_delivery
        )

    # ---- 分段投递 ----

    def _deliver_next_segment(self) -> None:
        delivery = self._delivery
        if delivery is None:
            return
        if not self._delivery_segments:
            self._finish_reply_delivery()
            return
        index, segment = self._delivery_segments.popleft()
        self.delivery_segment.emit(index, segment, self._delivery_reasoning)
        self._delivery_reasoning = ""
        if self._notification_pending:
            self._notification_pending = False
            self.delivery_notification.emit()
        if (
            not self._delivery_speech_started
            and segment.kind == "dialogue"
            and delivery.plan.dialogue_text
            and self._tts_auto_play_check is not None
            and self._tts_auto_play_check()
        ):
            self._delivery_speech_started = True
            self.delivery_speech.emit(
                f"turn:{delivery.turn_id}:segment:{index}",
                delivery.plan.dialogue_text,
                delivery.profile,
            )
        if self._delivery_segments:
            self.delivery_typing.emit(True)
            self._delivery_timer.start(
                self._reply_delay_ms(self._delivery_segments[0][1])
            )
        else:
            self._finish_reply_delivery()

    def _finish_reply_delivery(self) -> None:
        delivery = self._delivery
        if delivery is None:
            return
        self._delivery_timer.stop()
        self._delivery = None
        self._delivery_segments.clear()
        self._delivery_reasoning = ""
        if self._notification_pending:
            self._notification_pending = False
            self.delivery_notification.emit()
        self.delivery_finished.emit(delivery)

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

    def shutdown(self) -> None:
        """关闭：取消进行中的请求，等待线程退出。"""

        self._delivery_timer.stop()
        self._delivery = None
        self._pending_delivery = None
        self._delivery_segments.clear()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
