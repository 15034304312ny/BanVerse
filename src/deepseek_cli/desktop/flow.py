"""主消息流与真人式分段投递的状态机控制器。

从主窗口抽取的主管线：负责后台流式 worker 的生命周期、流式事件累积，
以及“完整回复 → 按真人节奏逐段投递”的状态机。主窗口不再直接持有线程、
投递与请求上下文，只通过 Qt 信号接收事件并执行 UI 动作与落库。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from time import monotonic

from PySide6.QtCore import QObject, QThread, QTimer, Signal

from ..diagnostics import DiagnosticRecorder
from ..roleplay_director import DirectorRequest
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
    diagnostic_task_id: str = ""
    request_started_at: float = 0.0


class MessageFlowController(QObject):
    """主管线状态机。

    ``begin_stream`` 启动一个后台流式请求并累积增量；``prepare_delivery``
    登记完成事件产生的投递计划，由主窗口在 ``stream_cleaned_up`` 后决定
    是否 ``begin_delivery``。全部 UI 副作用通过信号交给主窗口。
    """

    reasoning_accumulated = Signal(str)
    content_accumulated = Signal(str)
    image_described = Signal(str, str)  # turn_id, description
    image_analysis_failed = Signal(str)
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
        diagnostics: DiagnosticRecorder | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._tts_auto_play_check = tts_auto_play_check
        self._diagnostics = diagnostics
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._turn_id: str | None = None
        self._request_conversation_id: str | None = None
        self._request_kind = "user"
        self._answer = ""
        self._reasoning = ""
        self._notification_pending = False
        self._diagnostic_task_id = ""
        self._diagnostic_provider = ""
        self._diagnostic_image_provider = ""
        self._diagnostic_model = ""
        self._request_started_at = 0.0
        self._first_content_seen = False
        self._pending_delivery: ReplyDelivery | None = None
        self._delivery: ReplyDelivery | None = None
        self._delivery_segments: deque[tuple[int, ReplySegment]] = deque()
        self._delivery_reasoning = ""
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

    @property
    def diagnostic_task_id(self) -> str:
        return self._diagnostic_task_id

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
        post_history_prompt: str = "",
        temperature: float | None = None,
        top_p: float | None = None,
        frequency_penalty: float | None = None,
        presence_penalty: float | None = None,
        repetition_penalty: float | None = None,
        image_service=None,
        image_path: str = "",
        provider: str = "",
        image_provider: str = "",
        context_duration_ms: float | None = None,
        turn_id: str,
        conversation_id: str,
        request_kind: str = "user",
        director_request: DirectorRequest | None = None,
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
        self._diagnostic_task_id = (
            self._diagnostics.new_task_id(f"chat-{request_kind}")
            if self._diagnostics is not None
            else ""
        )
        self._diagnostic_provider = provider
        self._diagnostic_image_provider = image_provider
        self._diagnostic_model = model
        self._request_started_at = monotonic()
        self._first_content_seen = False
        if context_duration_ms is not None:
            self._record(
                "context_prepared",
                duration_ms=context_duration_ms,
                details={
                    "history_turns": len(history),
                    "input_characters": len(request_text),
                    "has_image": bool(image_path),
                },
            )
        self._record(
            "request_started",
            outcome="started",
            details={
                "call_count": 2 if director_request is not None else 1,
                "has_image": bool(image_path),
                "director_trigger_reasons": (
                    list(director_request.trigger_reasons)
                    if director_request is not None
                    else []
                ),
            },
        )
        worker = ChatWorker(
            service,
            model,
            history,
            request_text,
            system_prompt=system_prompt,
            example_messages=example_messages,
            post_history_prompt=post_history_prompt,
            temperature=temperature,
            top_p=top_p,
            frequency_penalty=frequency_penalty,
            presence_penalty=presence_penalty,
            repetition_penalty=repetition_penalty,
            image_service=image_service,
            image_path=image_path,
            director_request=director_request,
        )
        worker.reasoning.connect(self._on_reasoning)
        worker.content.connect(self._on_content)
        worker.completed.connect(self._on_completed)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        worker.image_described.connect(self._on_image_described)
        worker.image_analysis_failed.connect(self._on_image_analysis_failed)
        worker.director_finished.connect(self._on_director_finished)
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

        self._pending_delivery = replace(
            delivery,
            diagnostic_task_id=self._diagnostic_task_id,
            request_started_at=self._request_started_at,
        )
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
            if (
                segment.kind in {"dialogue", "narration"} and segment.text
            )
            or segment.kind == "image"
        )
        self._delivery_reasoning = delivery.reasoning
        self._record_delivery(delivery, "delivery_started", outcome="started")
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
        if not self._first_content_seen:
            self._first_content_seen = True
            self._record(
                "first_response_chunk",
                duration_ms=self._elapsed_request_ms(),
            )
        self.content_accumulated.emit(text)

    def _on_image_described(self, description: str) -> None:
        self._record(
            "image_understanding_completed",
            provider=self._diagnostic_image_provider,
            details={"output_characters": len(description)},
        )
        if self._turn_id:
            self.image_described.emit(self._turn_id, description)

    def _on_image_analysis_failed(self, error_code: str) -> None:
        self._record(
            "image_understanding_completed",
            outcome="error",
            error_code=error_code,
            provider=self._diagnostic_image_provider,
        )
        self.image_analysis_failed.emit(error_code)

    def _on_director_finished(self, status: str) -> None:
        self._record(
            "director_completed",
            outcome="ok" if status == "used" else status,
            error_code=("" if status in {"used", "skipped"} else f"director_{status}"),
        )

    def _on_completed(self, answer: str) -> None:
        self._record(
            "model_completed",
            duration_ms=self._elapsed_request_ms(),
            details={"output_characters": len(answer)},
        )
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
        self._record(
            "model_completed",
            outcome="cancelled",
            duration_ms=self._elapsed_request_ms(),
        )
        self.turn_aborted.emit(self._turn_id or "", self._request_kind, "")

    def _on_failed(self, error_code: str) -> None:
        self._notification_pending = False
        self._record(
            "model_completed",
            outcome="error",
            duration_ms=self._elapsed_request_ms(),
            error_code=error_code,
        )
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
        self._record(
            "stream_cleaned_up",
            duration_ms=self._elapsed_request_ms(),
        )
        self._request_kind = "user"
        self._diagnostic_task_id = ""
        self._diagnostic_provider = ""
        self._diagnostic_image_provider = ""
        self._diagnostic_model = ""
        self._request_started_at = 0.0
        self._first_content_seen = False
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
        if index == 0:
            self._record_delivery(
                delivery,
                "first_visible_segment",
                duration_ms=(
                    max(0.0, (monotonic() - delivery.request_started_at) * 1000)
                    if delivery.request_started_at
                    else None
                ),
                details={"segment_count": len(delivery.plan.segments)},
            )
        self.delivery_segment.emit(index, segment, self._delivery_reasoning)
        self._delivery_reasoning = ""
        if self._notification_pending:
            self._notification_pending = False
            self.delivery_notification.emit()
        if (
            segment.kind == "dialogue"
            and segment.text
            and self._tts_auto_play_check is not None
            and self._tts_auto_play_check()
        ):
            self.delivery_speech.emit(
                f"turn:{delivery.turn_id}:segment:{index}",
                segment.text,
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
        self._record_delivery(
            delivery,
            "delivery_completed",
            duration_ms=(
                max(0.0, (monotonic() - delivery.request_started_at) * 1000)
                if delivery.request_started_at
                else None
            ),
            details={"segment_count": len(delivery.plan.segments)},
        )
        self.delivery_finished.emit(delivery)

    def _elapsed_request_ms(self) -> float | None:
        if not self._request_started_at:
            return None
        return max(0.0, (monotonic() - self._request_started_at) * 1000)

    def _record(
        self,
        stage: str,
        *,
        outcome: str = "ok",
        duration_ms: float | None = None,
        error_code: str = "",
        provider: str | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        self._diagnostics.record(
            "text_chat",
            stage,
            outcome=outcome,
            duration_ms=duration_ms,
            error_code=error_code,
            provider=(
                self._diagnostic_provider if provider is None else provider
            ),
            model=self._diagnostic_model,
            request_kind=self._request_kind,
            task_id=self._diagnostic_task_id,
            details=details,
        )

    def _record_delivery(
        self,
        delivery: ReplyDelivery,
        stage: str,
        *,
        outcome: str = "ok",
        duration_ms: float | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        self._diagnostics.record(
            "text_chat",
            stage,
            outcome=outcome,
            duration_ms=duration_ms,
            request_kind=delivery.request_kind,
            task_id=delivery.diagnostic_task_id,
            details=details,
        )

    @staticmethod
    def _reply_delay_ms(
        segment: ReplySegment,
        *,
        first: bool = False,
    ) -> int:
        """按下一段内容估算真人组织和输入消息所需的等待时间。"""

        text = segment.text.strip() or segment.prompt.strip()
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
        if self._delivery is not None:
            self._record_delivery(
                self._delivery,
                "delivery_completed",
                outcome="cancelled",
            )
        self._delivery = None
        self._pending_delivery = None
        self._delivery_segments.clear()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
