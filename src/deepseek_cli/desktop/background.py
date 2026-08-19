"""后台摘要与自主发图管线，与主窗口的 UI 编排解耦。

主窗口一度直接管理四条异步管线（主管线、分段投递、摘要、自主发图）的
线程与状态。本模块把其中两条纯后台管线收敛为独立的 QObject 控制器，
各自维护队列、线程与生命周期；主窗口只负责注入依赖与接收结果回调。
"""

from __future__ import annotations

from collections import deque
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QThread

from ..character_cards import CharacterCardError
from ..model_catalog import MODEL_CHAT
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
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._chats = chats
        self._characters = characters
        self._settings = settings
        self._create_text_service = create_text_service
        self._text_api_key = text_api_key
        self._refresh = refresh
        self._queue: deque[SummaryJob] = deque()
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._job: SummaryJob | None = None
        self._shutting_down = False

    @property
    def thread(self) -> QThread | None:
        return self._thread

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def enqueue(self, conversation_id: str, answer: str) -> None:
        if self._shutting_down:
            return
        conversation = self._chats.get_conversation(conversation_id)
        if conversation is None:
            return
        character = (
            self._characters.get(conversation.character_id)
            if conversation.character_id
            else None
        )
        if character is not None and self._settings.get_bool(
            "role_memory_enabled", True
        ):
            user_text = self._chats.latest_completed_user_text(
                conversation_id
            )
            job = SummaryJob(
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
            job = SummaryJob(
                conversation_id,
                summary_request(answer),
                SUMMARY_SYSTEM_PROMPT,
            )
        self._queue = deque(
            queued
            for queued in self._queue
            if queued.conversation_id != conversation_id
        )
        self._queue.append(job)
        self.start_next()

    def enqueue_pending(self) -> None:
        for conversation_id, answer in self._chats.pending_summary_jobs():
            self.enqueue(conversation_id, answer)
        self.start_next()

    def start_next(self) -> None:
        if self._shutting_down or self._thread is not None or not self._queue:
            return
        api_key = self._text_api_key()
        if not api_key:
            return
        job = self._queue.popleft()
        if self._chats.get_conversation(job.conversation_id) is None:
            self.start_next()
            return
        self._job = job
        worker = ChatWorker(
            self._create_text_service(api_key),
            MODEL_CHAT,
            (),
            job.request_text,
            system_prompt=job.system_prompt,
            temperature=0.2,
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
        if job.updates_role_state:
            result = parse_role_postprocess(text)
            summary = result.summary
            if summary and result.role_state:
                self._chats.set_role_state(job.conversation_id, result.role_state)
        else:
            summary = clean_ai_summary(text)
        if summary:
            self._chats.set_ai_summary(job.conversation_id, summary)
        else:
            self._chats.mark_summary_failed(job.conversation_id)
        self._refresh()

    def _on_failed(self, _error_code: str = "") -> None:
        if self._shutting_down:
            return
        job = self._job
        if job is not None:
            self._chats.mark_summary_failed(job.conversation_id)
            self._refresh()

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


class CharacterDiscoveryRunner(QObject):
    """在后台生成一张受控角色卡；持久化由 UI 线程回调完成。"""

    def __init__(
        self,
        *,
        create_text_service: Callable[[str], Any],
        text_api_key: Callable[[], str],
        on_generated: Callable[[dict], None],
        on_error: Callable[[str], None],
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._create_text_service = create_text_service
        self._text_api_key = text_api_key
        self._on_generated = on_generated
        self._on_error = on_error
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
            temperature=1.1,
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
    decision_request: str = ""
    fallback_prompt: str = ""
    segment_index: int | None = None


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
        media_root=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._chats = chats
        self._characters = characters
        self._settings = settings
        self._create_text_service = create_text_service
        self._create_image_service = create_image_service
        self._text_api_key = text_api_key
        self._image_api_key = image_api_key
        self._refresh = refresh
        self._on_image_saved = on_image_saved
        self._on_image_error = on_image_error
        self._media_root = media_root
        self._queue: deque[AutonomousImageJob] = deque()
        self._thread: QThread | None = None
        self._worker: ChatWorker | ImageGenerationWorker | None = None
        self._job: AutonomousImageJob | None = None
        self._decision = AutonomousImageDecision()
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
        # 当前轮次加前三个已完成轮次构成冷却窗口，避免角色连续刷图。
        return self._chats.recent_window_has_assistant_image(
            conversation_id, window=4
        )

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
    ) -> None:
        fallback = fallback_prompt.strip()[:1_500]
        if (
            self._shutting_down
            or not self._enabled()
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
            )
        self._queue = deque(
            job for job in self._queue if job.turn_id != turn_id
        )
        self._queue.append(
            AutonomousImageJob(
                conversation_id,
                turn_id,
                decision_request=request,
                fallback_prompt=fallback,
                segment_index=segment_index,
            )
        )
        self.start_next()

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
            if direct_action:
                self._start_generation(job, job.fallback_prompt)
                return
            if not can_decide:
                continue
            self._start_decision(job)
            return

    def _start_decision(self, job: AutonomousImageJob) -> None:
        self._decision = AutonomousImageDecision()
        worker = ChatWorker(
            self._create_text_service(self._text_api_key()),
            MODEL_CHAT,
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

    def _on_decision_failed(self, _error_code: str = "") -> None:
        # 决策失败时回退到显式提示词；两者皆无则不生成。
        self._decision = AutonomousImageDecision()

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
            self._start_generation(job, selected_prompt)
            return
        self._job = None
        self.start_next()

    def _start_generation(self, job: AutonomousImageJob, prompt: str) -> None:
        worker = ImageGenerationWorker(
            self._create_image_service(),
            prompt,
            app_data_root=self._media_root,
        )
        worker.completed.connect(self._on_generated)
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
            return
        self._on_image_saved(job.conversation_id)

    def _on_failed(self, error_code: str) -> None:
        job = self._job
        if self._shutting_down or job is None:
            return
        self._on_image_error(job.conversation_id, error_code)

    def _generation_finished(self) -> None:
        self._dispose_phase()
        self._job = None
        self._decision = AutonomousImageDecision()
        self.start_next()

    def _dispose_phase(self) -> None:
        self._worker, self._thread = dispose_worker(
            self._worker, self._thread
        )

    def shutdown(self) -> None:
        self._shutting_down = True
        self._queue.clear()
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(1500)
