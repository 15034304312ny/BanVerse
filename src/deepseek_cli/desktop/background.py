"""后台摘要与自主发图管线，与主窗口的 UI 编排解耦。

主窗口一度直接管理四条异步管线（主管线、分段投递、摘要、自主发图）的
线程与状态。本模块把其中两条纯后台管线收敛为独立的 QObject 控制器，
各自维护队列、线程与生命周期；主窗口只负责注入依赖与接收结果回调。
"""

from __future__ import annotations

import json
from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from time import monotonic
from typing import Any

from PySide6.QtCore import QObject, QThread

from ..character_cards import CharacterCardError
from ..diagnostics import DiagnosticRecorder
from ..model_catalog import MODEL_CHAT
from ..multimodal import (
    has_current_image_share_intent,
    image_event_id,
    image_prompt_fingerprint,
    user_opted_out_of_images,
)
from ..relationship_policy import (
    relationship_policy_for,
    stabilize_role_state,
)
from .ai_features import (
    AUTONOMOUS_IMAGE_SYSTEM_PROMPT,
    CHARACTER_DISCOVERY_SYSTEM_PROMPT,
    ROLE_MEMORY_SYSTEM_PROMPT,
    SUMMARY_SYSTEM_PROMPT,
    AutonomousImageDecision,
    autonomous_image_request,
    character_avatar_prompt,
    character_discovery_request,
    clean_ai_summary,
    deserialize_reply_segments,
    enrich_role_image_prompt,
    parse_autonomous_image_decision,
    parse_discovered_character,
    parse_role_postprocess,
    role_memory_request,
    summary_request,
)
from .assets import install_generated_avatar
from .workers import ChatWorker, ImageGenerationWorker


def launch_worker(
    parent: QObject,
    worker: QObject,
    *,
    thread_finished: Callable[[], None] | None = None,
) -> QThread:
    """在独立 QThread 中运行一个 QObject worker，统一线程引导样板。

    约定：``worker.run`` 是槽（由 ``thread.started`` 触发），任务完成后
    ``worker.finished`` 请求退出线程；线程退出后回调 ``thread_finished``。
    调用方需在调用前连接 worker 的业务信号。
    """

    thread = QThread(parent)
    worker.moveToThread(thread)
    thread.started.connect(worker.run)
    worker.finished.connect(thread.quit)
    if thread_finished is not None:
        thread.finished.connect(thread_finished)
    thread.start()
    return thread


def dispose_worker(
    worker: QObject | None, thread: QThread | None
) -> tuple[None, None]:
    """安排 worker 与线程延迟析构，返回 (None, None) 便于赋值。"""

    if worker is not None:
        worker.deleteLater()
    if thread is not None:
        thread.deleteLater()
    return None, None


@dataclass(frozen=True, slots=True)
class SummaryJob:
    conversation_id: str
    request_text: str
    system_prompt: str
    updates_role_state: bool = False
    turn_id: str = ""
    model: str = MODEL_CHAT


class SummaryRunner(QObject):
    """串行执行会话摘要与角色连续性状态的后台队列。

    每个任务占一个线程槽位，同一时刻最多一个摘要请求；新任务会替换同一
    会话的旧任务。角色会话的任务同时产出列表摘要与连续性状态。
    """

    def __init__(
        self,
        *,
        chats,
        characters,
        settings,
        create_text_service: Callable[[str], Any],
        text_api_key: Callable[[], str],
        refresh: Callable[[], None],
        text_model: Callable[[str], str] | None = None,
        text_provider: Callable[[], str] | None = None,
        sampling_options: Callable[[str, float], dict[str, float]] | None = None,
        diagnostics: DiagnosticRecorder | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._chats = chats
        self._characters = characters
        self._settings = settings
        self._create_text_service = create_text_service
        self._text_api_key = text_api_key
        self._refresh = refresh
        self._text_model = text_model or (lambda conversation_model: conversation_model)
        self._text_provider = text_provider or (
            lambda: self._settings.get("text_provider", "deepseek")
        )
        self._sampling_options = sampling_options or (
            lambda _model, temperature: {"temperature": temperature}
        )
        self._diagnostics = diagnostics
        self._queue: deque[SummaryJob] = deque()
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._job: SummaryJob | None = None
        self._job_previous_state_json = ""
        self._diagnostic_task_id = ""
        self._job_started_at = 0.0
        self._shutting_down = False

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def enqueue(
        self, conversation_id: str, answer: str, *, turn_id: str = ""
    ) -> None:
        if self._shutting_down:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None:
            return
        if not turn_id:
            latest_turn = self._chats.latest_completed_turn(conversation_id)
            turn_id = latest_turn.id if latest_turn is not None else ""
        character = (
            self._characters.get(conversation.character_id)
            if conversation.character_id
            else None
        )
        memory_enabled = self._settings.get_bool(
            "role_memory_enabled", True
        ) and (
            character is None
            or self._settings.get_bool(
                f"role_memory_character_{character.id}", True
            )
        )
        if character is not None and memory_enabled:
            job = SummaryJob(
                conversation_id=conversation_id,
                request_text=answer,
                system_prompt=ROLE_MEMORY_SYSTEM_PROMPT,
                updates_role_state=True,
                turn_id=turn_id,
                model=conversation.model,
            )
        else:
            job = SummaryJob(
                conversation_id=conversation_id,
                request_text=answer,
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                turn_id=turn_id,
                model=conversation.model,
            )
        # 普通会话只需保留最新列表摘要；角色连续性任务则必须逐轮处理，
        # 否则用户连续发送消息时，中间一轮形成的事实与关系变化会被跳过。
        if job.updates_role_state:
            self._queue = deque(
                queued
                for queued in self._queue
                if not (
                    queued.conversation_id == conversation_id
                    and queued.turn_id == turn_id
                )
            )
        else:
            self._queue = deque(
                queued
                for queued in self._queue
                if queued.conversation_id != conversation_id
            )
        self._queue.append(job)
        self.start_next()

    def enqueue_pending(self) -> None:
        for conversation_id, turn_id, answer in self._chats.pending_summary_jobs():
            self.enqueue(conversation_id, answer, turn_id=turn_id)
        self.start_next()

    def start_next(self) -> None:
        if self._shutting_down or self._thread is not None or not self._queue:
            return
        api_key = self._text_api_key()
        if not api_key:
            return
        job = self._queue.popleft()
        conversation = self._chats.get_conversation(job.conversation_id)
        if conversation is None:
            self.start_next()
            return
        if job.updates_role_state and not self._memory_enabled_for(
            conversation.character_id or ""
        ):
            job = SummaryJob(
                conversation_id=job.conversation_id,
                request_text=job.request_text,
                system_prompt=SUMMARY_SYSTEM_PROMPT,
                turn_id=job.turn_id,
                model=job.model,
            )
        request_text = summary_request(job.request_text)
        self._job_previous_state_json = ""
        if job.updates_role_state:
            conversation = self._chats.get_conversation(job.conversation_id)
            character = (
                self._characters.get(conversation.character_id)
                if conversation and conversation.character_id
                else None
            )
            turn = (
                self._chats.get_turn(job.conversation_id, job.turn_id)
                if job.turn_id
                else self._chats.latest_completed_turn(job.conversation_id)
            )
            if conversation is None or character is None or turn is None:
                self._chats.mark_summary_failed(job.conversation_id)
                self.start_next()
                return
            self._job_previous_state_json = conversation.role_state_json
            request_text = role_memory_request(
                character.name,
                conversation.role_state_json,
                turn.user_content,
                job.request_text,
                turn_id=turn.id,
            )
        self._job = job
        self._diagnostic_task_id = (
            self._diagnostics.new_task_id("summary")
            if self._diagnostics is not None
            else ""
        )
        self._job_started_at = monotonic()
        self._record_diagnostic(
            "request_started",
            outcome="started",
            details={"updates_role_state": job.updates_role_state},
        )
        model = self._text_model(job.model)
        worker = ChatWorker(
            self._create_text_service(api_key),
            model,
            (),
            request_text,
            system_prompt=job.system_prompt,
            **self._sampling_options(model, 0.2),
        )
        worker.completed.connect(self._on_completed)
        worker.failed.connect(self._on_failed)
        worker.cancelled.connect(self._on_failed)
        self._worker = worker
        self._thread = launch_worker(
            self, worker, thread_finished=self._finished
        )

    def _on_completed(self, text: str) -> None:
        if self._shutting_down:
            return
        job = self._job
        if job is None:
            return
        self._record_diagnostic(
            "model_completed",
            duration_ms=(monotonic() - self._job_started_at) * 1000,
            details={"output_characters": len(text)},
        )
        persistence_started = monotonic()
        if job.updates_role_state:
            result = parse_role_postprocess(
                text, processed_turn_id=job.turn_id
            )
            summary = result.summary
            conversation = self._chats.get_conversation(job.conversation_id)
            if (
                summary
                and result.role_state
                and conversation is not None
                and self._memory_enabled_for(
                    conversation.character_id or ""
                )
            ):
                turn = self._chats.get_turn(
                    job.conversation_id, job.turn_id
                )
                try:
                    previous_state = json.loads(
                        self._job_previous_state_json or "{}"
                    )
                except (TypeError, ValueError):
                    previous_state = {}
                if not isinstance(previous_state, dict):
                    previous_state = {}
                policy = relationship_policy_for(
                    self._settings, conversation.character_id or ""
                )
                role_state = stabilize_role_state(
                    previous_state,
                    result.role_state,
                    user_text=turn.user_content if turn is not None else "",
                    assistant_text=(
                        turn.assistant_content
                        if turn is not None
                        else job.request_text
                    ),
                    pace=policy.pace,
                )
                updated = self._chats.set_role_state_if_unchanged(
                    job.conversation_id,
                    role_state,
                    expected_json=self._job_previous_state_json,
                )
                if updated:
                    try:
                        retention_days = int(
                            self._settings.get("memory_retention_days", "365")
                        )
                    except ValueError:
                        retention_days = 365
                    try:
                        max_items = int(
                            self._settings.get("memory_max_items", "200")
                        )
                    except ValueError:
                        max_items = 200
                    self._chats.upsert_role_memories(
                        job.conversation_id,
                        job.turn_id,
                        turn.user_content if turn is not None else "",
                        role_state,
                        retention_days=max(0, retention_days),
                        max_items=max(10, max_items),
                    )
        else:
            summary = clean_ai_summary(text)
        if summary:
            self._chats.set_ai_summary(job.conversation_id, summary)
        else:
            self._chats.mark_summary_failed(job.conversation_id)
        self._record_diagnostic(
            "result_persisted",
            duration_ms=(monotonic() - persistence_started) * 1000,
            outcome="ok" if summary else "error",
            error_code="empty_summary" if not summary else "",
            details={"updates_role_state": job.updates_role_state},
        )
        self._refresh()

    def _memory_enabled_for(self, character_id: str) -> bool:
        return self._settings.get_bool(
            "role_memory_enabled", True
        ) and (
            not character_id
            or self._settings.get_bool(
                f"role_memory_character_{character_id}", True
            )
        )

    def _on_failed(self, _error_code: str = "") -> None:
        if self._shutting_down:
            return
        job = self._job
        if job is not None:
            self._record_diagnostic(
                "model_completed",
                outcome="error" if _error_code else "cancelled",
                error_code=_error_code,
                duration_ms=(monotonic() - self._job_started_at) * 1000,
            )
            self._chats.mark_summary_failed(job.conversation_id)
            self._refresh()

    def _finished(self) -> None:
        self._worker, self._thread = dispose_worker(
            self._worker, self._thread
        )
        self._job = None
        self._job_previous_state_json = ""
        self._diagnostic_task_id = ""
        self._job_started_at = 0.0
        self.start_next()

    def _record_diagnostic(
        self,
        stage: str,
        *,
        outcome: str = "ok",
        error_code: str = "",
        duration_ms: float | None = None,
        details: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        self._diagnostics.record(
            "summary_role_state",
            stage,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            provider=self._text_provider(),
            model=(self._text_model(self._job.model) if self._job else ""),
            request_kind="background",
            task_id=self._diagnostic_task_id,
            source_ref=(
                self._diagnostics.reference(self._job.turn_id, prefix="turn")
                if self._job is not None
                else ""
            ),
            details=details,
        )

    def shutdown(self) -> None:
        self._shutting_down = True
        self._queue.clear()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)


class CharacterDiscoveryRunner(QObject):
    """在后台生成一张受控角色卡；持久化由 UI 线程回调完成。"""

    def __init__(
        self,
        *,
        create_text_service: Callable[[str], Any],
        text_api_key: Callable[[], str],
        on_generated: Callable[[dict], None],
        on_error: Callable[[str], None],
        sampling_options: Callable[[str, float], dict[str, float]] | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._create_text_service = create_text_service
        self._text_api_key = text_api_key
        self._on_generated = on_generated
        self._on_error = on_error
        self._sampling_options = sampling_options or (
            lambda _model, temperature: {"temperature": temperature}
        )
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._existing_names: tuple[str, ...] = ()
        self._expected_gender = ""
        self._shutting_down = False

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def generate(
        self,
        existing_characters: tuple[tuple[str, str], ...],
        *,
        user_name: str,
        user_persona: str,
        desired_gender: str = "",
    ) -> bool:
        if self._shutting_down or self._thread is not None:
            return False
        api_key = self._text_api_key()
        if not api_key:
            return False
        self._existing_names = tuple(name for name, _ in existing_characters)
        self._expected_gender = desired_gender
        worker = ChatWorker(
            self._create_text_service(api_key),
            MODEL_CHAT,
            (),
            character_discovery_request(
                existing_characters,
                user_name=user_name,
                user_persona=user_persona,
                desired_gender=desired_gender,
            ),
            system_prompt=CHARACTER_DISCOVERY_SYSTEM_PROMPT,
            **self._sampling_options(MODEL_CHAT, 1.1),
        )
        worker.completed.connect(self._completed)
        worker.failed.connect(self._failed)
        worker.cancelled.connect(self._failed)
        self._worker = worker
        self._thread = launch_worker(
            self, worker, thread_finished=self._finished
        )
        return True

    def _completed(self, text: str) -> None:
        if self._shutting_down:
            return
        try:
            card = parse_discovered_character(
                text,
                existing_names=self._existing_names,
                expected_gender=self._expected_gender,
            )
        except CharacterCardError:
            self._on_error("invalid_character_card")
            return
        self._on_generated(card)

    def _failed(self, error_code: str = "") -> None:
        if not self._shutting_down:
            self._on_error(error_code or "character_generation_failed")

    def _finished(self) -> None:
        self._worker, self._thread = dispose_worker(
            self._worker, self._thread
        )
        self._existing_names = ()
        self._expected_gender = ""

    def shutdown(self) -> None:
        self._shutting_down = True
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)


@dataclass(frozen=True, slots=True)
class CharacterAvatarJob:
    character_id: str
    card: dict


class CharacterAvatarRunner(QObject):
    """串行生成随机角色头像；失败不会回滚已创建的角色。"""

    def __init__(
        self,
        *,
        create_image_service: Callable[[], Any],
        image_api_key: Callable[[], str],
        on_generated: Callable[[str, str], None],
        on_error: Callable[[str, str], None],
        app_data_root=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._create_image_service = create_image_service
        self._image_api_key = image_api_key
        self._on_generated = on_generated
        self._on_error = on_error
        self._app_data_root = app_data_root
        self._queue: deque[CharacterAvatarJob] = deque()
        self._thread: QThread | None = None
        self._worker: ImageGenerationWorker | None = None
        self._job: CharacterAvatarJob | None = None
        self._shutting_down = False

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def enqueue(self, character_id: str, card: dict) -> bool:
        if self._shutting_down or not self._image_api_key():
            return False
        if self._job is not None and self._job.character_id == character_id:
            return True
        self._queue = deque(
            job for job in self._queue if job.character_id != character_id
        )
        self._queue.append(CharacterAvatarJob(character_id, card))
        self.start_next()
        return True

    def start_next(self) -> None:
        if self._shutting_down or self._thread is not None:
            return
        while self._queue:
            job = self._queue.popleft()
            if not self._image_api_key():
                self._on_error(job.character_id, "image_api_key_missing")
                continue
            try:
                service = self._create_image_service()
            except Exception as exc:
                self._on_error(job.character_id, ChatWorker._error_code(exc))
                continue
            self._job = job
            worker = ImageGenerationWorker(
                service,
                character_avatar_prompt(job.card),
                app_data_root=self._app_data_root,
                image_installer=install_generated_avatar,
            )
            worker.completed.connect(self._generated)
            worker.failed.connect(self._failed)
            self._worker = worker
            self._thread = launch_worker(
                self, worker, thread_finished=self._finished
            )
            return

    def _generated(self, avatar_path: str) -> None:
        if self._shutting_down or self._job is None:
            with suppress(OSError):
                Path(avatar_path).unlink(missing_ok=True)
            return
        self._on_generated(self._job.character_id, avatar_path)

    def _failed(self, error_code: str) -> None:
        if not self._shutting_down and self._job is not None:
            self._on_error(self._job.character_id, error_code)

    def _finished(self) -> None:
        self._worker, self._thread = dispose_worker(
            self._worker, self._thread
        )
        self._job = None
        self.start_next()

    def shutdown(self) -> None:
        self._shutting_down = True
        self._queue.clear()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)


@dataclass(frozen=True, slots=True)
class AutonomousImageJob:
    conversation_id: str
    turn_id: str
    character_name: str = ""
    character_card: dict | None = None
    answer: str = ""
    role_state: dict | None = None
    decision_request: str = ""
    fallback_prompt: str = ""
    segment_index: int | None = None
    trigger: str = "semantic"
    current_time: datetime | None = None


class AutonomousImageRunner(QObject):
    """按两阶段状态机串行处理角色自主发图请求。

    每个任务先让模型做一次“是否发图 + 提示词”的语义决策，必要时再调用
    文生图服务；显式索图或角色动作给出的提示词可跳过决策直接生成。
    冷却窗口防止角色连续刷图。
    """

    def __init__(
        self,
        *,
        chats,
        characters,
        settings,
        create_text_service: Callable[[str], Any],
        create_image_service: Callable[[], Any],
        text_api_key: Callable[[], str],
        image_api_key: Callable[[], str],
        refresh: Callable[[], None],
        on_image_saved: Callable[[str], None],
        on_image_error: Callable[[str, str], None],
        text_model: Callable[[str], str] | None = None,
        text_provider: Callable[[], str] | None = None,
        media_root=None,
        diagnostics: DiagnosticRecorder | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._chats = chats
        self._characters = characters
        self._settings = settings
        self._create_text_service = create_text_service
        self._create_image_service = create_image_service
        self._text_api_key = text_api_key
        self._text_model = text_model or (lambda _model: MODEL_CHAT)
        self._resolved_text_provider = text_provider or (
            lambda: self._settings.get("text_provider", "deepseek")
        )
        self._image_api_key = image_api_key
        self._refresh = refresh
        self._on_image_saved = on_image_saved
        self._on_image_error = on_image_error
        self._media_root = media_root
        self._diagnostics = diagnostics
        self._queue: deque[AutonomousImageJob] = deque()
        self._thread: QThread | None = None
        self._worker: ChatWorker | ImageGenerationWorker | None = None
        self._job: AutonomousImageJob | None = None
        self._decision = AutonomousImageDecision()
        self._diagnostic_task_id = ""
        self._phase_started_at = 0.0
        self._job_outcome = "ok"
        self._job_error_code = ""
        self._shutting_down = False

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def _enabled(self) -> bool:
        return self._settings.get_bool("autonomous_images_enabled", True)

    def _recently_shared_image(self, conversation_id: str) -> bool:
        try:
            window = int(
                self._settings.get("autonomous_image_cooldown_turns", "4")
            )
        except ValueError:
            window = 4
        return self._chats.recent_window_has_assistant_image(
            conversation_id, window=max(1, min(window, 20))
        )

    def _daily_limit_reached(
        self, conversation_id: str, current_time: datetime
    ) -> bool:
        try:
            limit = int(
                self._settings.get("autonomous_image_daily_limit", "4")
            )
        except ValueError:
            limit = 4
        limit = max(0, min(limit, 20))
        if limit <= 0:
            return True
        today = current_time.astimezone().date()
        count = 0
        for turn in self._chats.list_turns(conversation_id):
            if not turn.assistant_image_path:
                continue
            try:
                created = datetime.fromisoformat(
                    turn.created_at.replace("Z", "+00:00")
                ).astimezone(current_time.tzinfo)
            except (TypeError, ValueError):
                continue
            if created.date() == today:
                count += 1
        return count >= limit

    def _recent_prompt_fingerprints(
        self, conversation_id: str, *, exclude_turn_id: str = ""
    ) -> set[str]:
        fingerprints: set[str] = set()
        for turn in self._chats.list_turns(conversation_id)[-8:]:
            if turn.id == exclude_turn_id:
                continue
            for segment in deserialize_reply_segments(
                turn.assistant_segments_json
            ):
                if segment.kind == "image" and segment.prompt:
                    value = image_prompt_fingerprint(segment.prompt)
                    if value:
                        fingerprints.add(value)
        return fingerprints

    def enqueue(
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
        fallback = fallback_prompt.strip()[:1_500]
        trigger = trigger if trigger in {"semantic", "explicit", "role_action", "retry"} else "semantic"
        # 旧调用方只传 fallback_prompt 表示用户已索图，保留其跳过冷却的语义。
        if fallback and trigger == "semantic":
            trigger = "explicit"
        now = datetime.now().astimezone()
        conversation = self._chats.get_conversation(conversation_id)
        turn = self._chats.get_turn(conversation_id, turn_id)
        if (
            self._shutting_down
            or not self._enabled()
            or not self._image_api_key()
            or conversation is None
            or turn is None
            or turn.status != "completed"
        ):
            return False
        explicit = trigger in {"explicit", "retry"}
        policy = relationship_policy_for(
            self._settings, conversation.character_id or ""
        )
        image_blocked = any(
            any(word in topic for word in ("图片", "照片", "自拍", "发图"))
            for topic in policy.blocked_topics
        )
        if (
            (user_opted_out_of_images(turn.user_content) or image_blocked)
            and not explicit
        ):
            self._fail_existing_event(turn_id, segment_index, "image_boundary")
            return False
        if self._daily_limit_reached(conversation_id, now):
            self._fail_existing_event(turn_id, segment_index, "image_daily_limit")
            return False
        if not explicit and self._recently_shared_image(conversation_id):
            self._fail_existing_event(turn_id, segment_index, "image_cooldown")
            return False
        if not fallback and not self._text_api_key():
            return False
        request = ""
        if self._text_api_key():
            history = self._chats.completed_history(
                conversation_id, max_turns=8
            )
            request = autonomous_image_request(
                character_name,
                character_card,
                history,
                answer,
                current_time=now,
                role_state=role_state,
            )
        self._queue = deque(
            job for job in self._queue if job.turn_id != turn_id
        )
        self._queue.append(
            AutonomousImageJob(
                conversation_id,
                turn_id,
                character_name=character_name,
                character_card=character_card,
                answer=answer,
                role_state=role_state,
                decision_request=request,
                fallback_prompt=fallback,
                segment_index=segment_index,
                trigger=trigger,
                current_time=now,
            )
        )
        self.start_next()
        return True

    def start_next(self) -> None:
        if self._shutting_down or self._thread is not None:
            return
        while self._queue:
            job = self._queue.popleft()
            conversation = self._chats.get_conversation(job.conversation_id)
            turn = self._chats.get_turn(job.conversation_id, job.turn_id)
            can_decide = bool(job.decision_request and self._text_api_key())
            direct_action = bool(job.fallback_prompt) and not can_decide
            if (
                conversation is None
                or turn is None
                or turn.status != "completed"
                or turn.assistant_image_path
                or not self._enabled()
                or not self._image_api_key()
                or (
                    not job.fallback_prompt
                    and (
                        not can_decide
                        or self._recently_shared_image(job.conversation_id)
                    )
                )
            ):
                continue
            self._job = job
            self._diagnostic_task_id = (
                self._diagnostics.new_task_id("image")
                if self._diagnostics is not None
                else ""
            )
            self._job_outcome = "ok"
            self._job_error_code = ""
            self._record_image_diagnostic("job_started", outcome="started")
            if direct_action:
                self._start_generation(
                    job, self._enriched_prompt(job, job.fallback_prompt)
                )
                return
            if not can_decide:
                continue
            self._start_decision(job)
            return

    def _start_decision(self, job: AutonomousImageJob) -> None:
        self._decision = AutonomousImageDecision()
        self._phase_started_at = monotonic()
        self._record_image_diagnostic(
            "decision_started", outcome="started", provider=self._text_provider()
        )
        worker = ChatWorker(
            self._create_text_service(self._text_api_key()),
            self._text_model(MODEL_CHAT),
            (),
            job.decision_request,
            system_prompt=AUTONOMOUS_IMAGE_SYSTEM_PROMPT,
        )
        worker.completed.connect(self._on_decision)
        worker.failed.connect(self._on_decision_failed)
        self._worker = worker
        self._thread = launch_worker(
            self, worker, thread_finished=self._decision_finished
        )

    def _on_decision(self, text: str) -> None:
        self._decision = parse_autonomous_image_decision(text)
        self._record_image_diagnostic(
            "decision_completed",
            duration_ms=(monotonic() - self._phase_started_at) * 1000,
            provider=self._text_provider(),
            details={"output_characters": len(text)},
        )

    def _on_decision_failed(self, _error_code: str = "") -> None:
        # 决策失败时回退到显式提示词；两者皆无则不生成。
        self._decision = AutonomousImageDecision()
        self._record_image_diagnostic(
            "decision_completed",
            outcome="error",
            error_code=_error_code,
            duration_ms=(monotonic() - self._phase_started_at) * 1000,
            provider=self._text_provider(),
        )

    def _decision_finished(self) -> None:
        decision = self._decision
        job = self._job
        self._dispose_phase()
        selected_prompt = (
            decision.prompt
            if decision.send_image and decision.prompt
            else (job.fallback_prompt if job is not None else "")
        )
        if (
            not self._shutting_down
            and job is not None
            and selected_prompt
            and self._chats.get_conversation(job.conversation_id) is not None
            and self._image_api_key()
        ):
            if (
                job.trigger == "semantic"
                and not has_current_image_share_intent(job.answer)
            ):
                self._record_image_diagnostic(
                    "job_completed",
                    outcome="skipped",
                    error_code="image_intent_missing",
                )
                self._job = None
                self._diagnostic_task_id = ""
                self.start_next()
                return
            enriched = self._enriched_prompt(job, selected_prompt)
            self._start_generation(job, enriched)
            return
        self._record_image_diagnostic(
            "job_completed", outcome="skipped"
        )
        self._job = None
        self._diagnostic_task_id = ""
        self.start_next()

    def _start_generation(self, job: AutonomousImageJob, prompt: str) -> None:
        if (
            job.trigger not in {"explicit", "retry"}
            and image_prompt_fingerprint(prompt)
            in self._recent_prompt_fingerprints(
                job.conversation_id, exclude_turn_id=job.turn_id
            )
        ):
            self._fail_existing_event(
                job.turn_id, job.segment_index, "image_duplicate"
            )
            self._record_image_diagnostic(
                "job_completed",
                outcome="skipped",
                error_code="image_duplicate",
            )
            self._job = None
            self._diagnostic_task_id = ""
            self.start_next()
            return
        try:
            segment_index = self._chats.ensure_assistant_image_event(
                job.turn_id,
                prompt,
                image_event_id(job.turn_id),
            )
            self._chats.set_assistant_image_status(
                job.turn_id, "pending", segment_index=segment_index
            )
            job = replace(job, segment_index=segment_index)
            self._job = job
            service = self._create_image_service()
        except Exception as exc:
            error_code = ChatWorker._error_code(exc)
            self._fail_existing_event(
                job.turn_id, job.segment_index, error_code
            )
            self._job_outcome = "error"
            self._job_error_code = error_code
            self._on_image_error(job.conversation_id, error_code)
            self._job = None
            self._diagnostic_task_id = ""
            self.start_next()
            return
        self._phase_started_at = monotonic()
        self._record_image_diagnostic(
            "generation_started",
            outcome="started",
            provider=self._image_provider(),
        )
        worker = ImageGenerationWorker(
            service,
            prompt,
            app_data_root=self._media_root,
        )
        worker.completed.connect(self._on_generated)
        worker.cancelled.connect(self._on_cancelled)
        worker.failed.connect(self._on_failed)
        self._worker = worker
        self._thread = launch_worker(
            self, worker, thread_finished=self._generation_finished
        )

    def _on_generated(self, image_path: str) -> None:
        job = self._job
        if self._shutting_down or job is None:
            return
        try:
            self._chats.set_assistant_image_path(
                job.turn_id,
                image_path,
                segment_index=job.segment_index,
            )
        except (KeyError, ValueError):
            self._job_outcome = "error"
            self._job_error_code = "turn_not_found"
            self._record_image_diagnostic(
                "generation_completed",
                outcome="error",
                error_code="turn_not_found",
                duration_ms=(monotonic() - self._phase_started_at) * 1000,
                provider=self._image_provider(),
            )
            return
        self._job_outcome = "ok"
        self._job_error_code = ""
        self._record_image_diagnostic(
            "generation_completed",
            duration_ms=(monotonic() - self._phase_started_at) * 1000,
            provider=self._image_provider(),
        )
        self._on_image_saved(job.conversation_id)

    def _on_failed(self, error_code: str) -> None:
        job = self._job
        if self._shutting_down or job is None:
            return
        self._job_outcome = "error"
        self._job_error_code = error_code
        self._fail_existing_event(job.turn_id, job.segment_index, error_code)
        self._record_image_diagnostic(
            "generation_completed",
            outcome="error",
            error_code=error_code,
            duration_ms=(monotonic() - self._phase_started_at) * 1000,
            provider=self._image_provider(),
        )
        self._on_image_error(job.conversation_id, error_code)

    def _on_cancelled(self) -> None:
        job = self._job
        if job is None or self._shutting_down:
            return
        self._job_outcome = "cancelled"
        self._job_error_code = "image_cancelled"
        self._set_existing_event_status(job, "cancelled")
        self._record_image_diagnostic(
            "generation_completed",
            outcome="cancelled",
            error_code="image_cancelled",
            duration_ms=(monotonic() - self._phase_started_at) * 1000,
            provider=self._image_provider(),
        )
        if not self._shutting_down:
            self._on_image_error(job.conversation_id, "image_cancelled")

    def _enriched_prompt(self, job: AutonomousImageJob, prompt: str) -> str:
        if "稳定视觉身份：" in prompt and "负面约束：" in prompt:
            return prompt
        return enrich_role_image_prompt(
            job.character_name,
            job.character_card or {},
            prompt,
            current_time=job.current_time,
            role_state=job.role_state,
            recent_event=job.answer,
        )

    def _fail_existing_event(
        self,
        turn_id: str,
        segment_index: int | None,
        error_code: str,
    ) -> None:
        if segment_index is None:
            return
        try:
            self._chats.set_assistant_image_status(
                turn_id,
                "failed",
                error_code,
                segment_index=segment_index,
            )
        except (KeyError, ValueError):
            return

    def _set_existing_event_status(
        self, job: AutonomousImageJob, status: str
    ) -> None:
        if job.segment_index is None:
            return
        try:
            self._chats.set_assistant_image_status(
                job.turn_id,
                status,
                segment_index=job.segment_index,
            )
        except (KeyError, ValueError):
            return

    def _generation_finished(self) -> None:
        self._dispose_phase()
        self._job = None
        self._decision = AutonomousImageDecision()
        self._record_image_diagnostic(
            "job_completed",
            outcome=self._job_outcome,
            error_code=self._job_error_code,
        )
        self._diagnostic_task_id = ""
        self._phase_started_at = 0.0
        self._job_outcome = "ok"
        self._job_error_code = ""
        self.start_next()

    def _text_provider(self) -> str:
        return self._resolved_text_provider()

    def _image_provider(self) -> str:
        return self._settings.get("image_provider", "siliconflow")

    def _record_image_diagnostic(
        self,
        stage: str,
        *,
        outcome: str = "ok",
        error_code: str = "",
        duration_ms: float | None = None,
        provider: str = "",
        details: dict[str, object] | None = None,
    ) -> None:
        if self._diagnostics is None:
            return
        self._diagnostics.record(
            "autonomous_image",
            stage,
            outcome=outcome,
            error_code=error_code,
            duration_ms=duration_ms,
            provider=provider,
            request_kind="background",
            task_id=self._diagnostic_task_id,
            details=details,
        )

    def _dispose_phase(self) -> None:
        self._worker, self._thread = dispose_worker(
            self._worker, self._thread
        )

    def shutdown(self) -> None:
        self._shutting_down = True
        pending_jobs = tuple(self._queue)
        self._queue.clear()
        if self._job is not None:
            self._set_existing_event_status(self._job, "cancelled")
        for job in pending_jobs:
            self._set_existing_event_status(job, "cancelled")
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
