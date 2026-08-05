"""Edge / 科大讯飞 / 硅基流动合成与分段播放控制器。"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import (
    QLocale,
    QObject,
    QStandardPaths,
    QThread,
    QUrl,
    Signal,
)
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtTextToSpeech import QTextToSpeech

from ..tts import (
    SpeechSegment,
    TtsProfile,
    extract_speech_segments,
    resolve_effective_profile,
)
from .index_tts2 import (
    DEFAULT_INDEXTTS2_BASE_URL,
    DEFAULT_INDEXTTS2_PRESET,
    normalize_index_tts2_base_url,
)
from .platform import is_android_platform
from .workers import (
    IndexTts2SynthesisWorker,
    SiliconFlowTtsSynthesisWorker,
    TtsSynthesisWorker,
    XfyunSuperTtsSynthesisWorker,
)
from .xfyun_catalog import (
    XFYUN_TTS_VOICE_OPTIONS,  # noqa: F401 - re-export 供设置页导入
    automatic_voice,
    deserialize_available_voices,
)

DEFAULT_SILICONFLOW_TTS_MODEL = "FunAudioLLM/CosyVoice2-0.5B"
SILICONFLOW_VOICE_OPTIONS = (
    ("自动匹配角色", "auto"),
    ("Anna · 沉稳女声", "anna"),
    ("Bella · 热情女声", "bella"),
    ("Claire · 温柔女声", "claire"),
    ("Diana · 活泼女声", "diana"),
    ("Alex · 沉稳男声", "alex"),
    ("Benjamin · 深沉男声", "benjamin"),
    ("Charles · 磁性男声", "charles"),
    ("David · 活泼男声", "david"),
)
DEFAULT_XFYUN_TTS_VOICE = "auto"
XFYUN_DEFAULT_TUNING = (50, 50, 50)
_SILICONFLOW_EMOTION_PROMPTS = {
    "happy": "请用开心、轻快的语气说",
    "sad": "请用低落、克制的语气说",
    "angry": "请用生气但清晰的语气说",
    "tender": "请用温柔、关心的语气说",
    "serious": "请用严肃、稳重的语气说",
    "calm": "请用平静、沉稳的语气说",
    "fearful": "请用紧张、略带害怕的语气说",
    "surprised": "请用惊讶、意外的语气说",
}


@dataclass(frozen=True, slots=True)
class SpeechRequest:
    message_key: str
    text: str
    profile: TtsProfile
    segments: tuple[SpeechSegment, ...]
    provider: str


class SpeechController(QObject):
    state_changed = Signal(str, str)
    failed = Signal(str, str)

    def __init__(
        self,
        parent: QObject | None = None,
        *,
        settings=None,
        credentials=None,
        edge_worker_factory=TtsSynthesisWorker,
        siliconflow_worker_factory=SiliconFlowTtsSynthesisWorker,
        xfyun_worker_factory=XfyunSuperTtsSynthesisWorker,
        index_tts2_worker_factory=IndexTts2SynthesisWorker,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._credentials = credentials
        self._edge_worker_factory = edge_worker_factory
        self._siliconflow_worker_factory = siliconflow_worker_factory
        self._xfyun_worker_factory = xfyun_worker_factory
        self._index_tts2_worker_factory = index_tts2_worker_factory
        self._native_tts = (
            QTextToSpeech(self) if is_android_platform() else None
        )
        if self._native_tts is not None:
            # Android PySide wheels can omit the scoped Territory enum even
            # though QLocale itself is available.  The locale-name overload
            # is supported by both desktop and Android builds.
            self._native_tts.setLocale(QLocale("zh_CN"))
            self._native_tts.stateChanged.connect(
                self._native_tts_state_changed
            )
        self._audio = QAudioOutput(self)
        self._player = QMediaPlayer(self)
        self._player.setAudioOutput(self._audio)
        self._player.playbackStateChanged.connect(self._playback_state_changed)
        self._player.mediaStatusChanged.connect(self._media_status_changed)
        self._player.errorOccurred.connect(self._playback_error)
        self._thread: QThread | None = None
        self._worker: QObject | None = None
        self._pending: SpeechRequest | None = None
        self._current: SpeechRequest | None = None
        self._request_id = 0
        self._segment_index = 0
        self._cache: dict[str, Path] = {}
        self._cache_dir = Path(
            QStandardPaths.writableLocation(
                QStandardPaths.StandardLocation.CacheLocation
            )
        ) / "tts"
        self._cache_dir.mkdir(parents=True, exist_ok=True)
        self._shutdown = False

    def speak(self, message_key: str, text: str, profile: TtsProfile) -> None:
        segments = extract_speech_segments(text)
        speech_text = "\n".join(segment.text for segment in segments).strip()
        if not message_key or not speech_text or not segments:
            return
        provider = self._provider()
        segments = self._segments_for_provider(provider, segments)
        speech_text = "".join(segment.text for segment in segments).strip()
        request = SpeechRequest(
            message_key,
            speech_text,
            profile,
            segments,
            provider,
        )
        self._shutdown = False
        if self._thread is not None:
            self._pending = request
            self._request_id += 1
            if self._worker is not None:
                self._worker.cancel()
            if self._current is not None:
                self.state_changed.emit(self._current.message_key, "idle")
            self._current = None
            return
        self.stop()
        self._start_request(request)

    def _start_request(self, request: SpeechRequest) -> None:
        self._current = request
        self._segment_index = 0
        if (
            request.provider == "siliconflow"
            and not self._siliconflow_api_key()
        ):
            self._provider_error(request, "tts_authentication")
            return
        if request.provider == "xfyun" and not self._xfyun_ready():
            self._provider_error(request, "tts_authentication")
            return
        self._start_segment()

    def _start_segment(self) -> None:
        if self._current is None:
            return
        request = self._current
        segment = request.segments[self._segment_index]
        spoken_text = segment.text
        effective = resolve_effective_profile(
            request.profile,
            segment.text,
            emotion_override=segment.emotion,
            rate_delta=segment.rate_delta,
            pitch_delta=segment.pitch_delta,
            volume_delta=segment.volume_delta,
        )
        if request.provider == "edge" and self._native_tts is not None:
            self._start_native_segment(request, spoken_text, effective)
            return
        provider_config = self._provider_config(
            request.provider, effective.voice, request.profile
        )
        cache_key = self._cache_key(
            segment, effective, provider_config
        )
        cached = self._cache.get(cache_key)
        if cached and cached.exists():
            self._play_file(request, cached)
            return
        suffix = ".wav" if request.provider == "indextts2" else ".mp3"
        output = self._cache_dir / f"{cache_key}{suffix}"
        if output.exists() and output.stat().st_size > 0:
            self._cache[cache_key] = output
            self._play_file(request, output)
            return
        self._request_id += 1
        self.state_changed.emit(request.message_key, "synthesizing")
        self._thread = QThread(self)
        if request.provider == "siliconflow":
            self._worker = self._siliconflow_worker_factory(
                self._request_id,
                self._siliconflow_api_key(),
                self._siliconflow_input(spoken_text, effective.emotion),
                self._siliconflow_model(),
                self._siliconflow_voice(effective.voice),
                self._speed_from_rate(effective.rate),
                self._gain_from_volume(effective.volume),
                str(output),
            )
        elif request.provider == "xfyun":
            speed, pitch, volume = XFYUN_DEFAULT_TUNING
            self._worker = self._xfyun_worker_factory(
                self._request_id,
                self._xfyun_app_id(),
                self._xfyun_api_password(),
                spoken_text,
                self._xfyun_voice(effective.voice),
                speed,
                pitch,
                volume,
                str(output),
                api_key=self._xfyun_api_key(),
                api_secret=self._xfyun_api_secret(),
            )
        elif request.provider == "indextts2":
            self._worker = self._index_tts2_worker_factory(
                self._request_id,
                self._index_tts2_base_url(),
                spoken_text,
                self._index_tts2_preset(request.profile),
                effective.emotion,
                str(output),
            )
        else:
            self._worker = self._edge_worker_factory(
                self._request_id,
                spoken_text,
                effective.voice,
                effective.rate,
                effective.pitch,
                effective.volume,
                str(output),
            )
        thread = self._thread
        worker = self._worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.completed.connect(
            lambda request_id, path: self._synthesis_completed(
                request_id, cache_key, Path(path)
            )
        )
        worker.cancelled.connect(self._synthesis_cancelled)
        worker.failed.connect(self._synthesis_failed)
        worker.finished.connect(thread.quit)
        thread.finished.connect(
            lambda finished_thread=thread, finished_worker=worker:
            self._thread_finished(finished_thread, finished_worker)
        )
        thread.start()

    def _provider_error(
        self, request: SpeechRequest, error_code: str
    ) -> None:
        self.state_changed.emit(request.message_key, "error")
        self.failed.emit(request.message_key, error_code)
        self._current = None

    @staticmethod
    def _segments_for_provider(
        provider: str, segments: tuple[SpeechSegment, ...]
    ) -> tuple[SpeechSegment, ...]:
        if provider not in {"xfyun", "indextts2"} or not segments:
            return segments
        # 一次原文合成，避免切成多个音频文件后形成额外停顿。
        text = "".join(segment.text for segment in segments).strip()
        text = re.sub(r"([，。！？；：、…])\s+", r"\1", text)
        first = segments[0]
        return (
            SpeechSegment(
                text,
                first.emotion,
                first.rate_delta,
                first.pitch_delta,
                first.volume_delta,
                first.action_cue,
            ),
        ) if text else ()

    def stop(self) -> None:
        self._pending = None
        self._request_id += 1
        current = self._current
        self._current = None
        if self._worker is not None:
            self._worker.cancel()
        if self._native_tts is not None:
            self._native_tts.stop()
        if self._player.playbackState() != QMediaPlayer.PlaybackState.StoppedState:
            self._player.stop()
        self._player.setSource(QUrl())
        if current is not None:
            self.state_changed.emit(current.message_key, "idle")
        self._segment_index = 0

    def reload_provider(self) -> None:
        """切换引擎时停止旧引擎，下一次播放使用新配置。"""

        self.stop()

    def shutdown(self) -> None:
        if self._shutdown:
            return
        self._shutdown = True
        self.stop()
        if self._thread is not None:
            self._thread.quit()
            self._thread.wait(3000)

    def _synthesis_completed(self, request_id: int, cache_key: str, path: Path) -> None:
        if request_id != self._request_id or self._current is None:
            return
        self._cache[cache_key] = path
        self._play_file(self._current, path)

    def _synthesis_cancelled(self, request_id: int) -> None:
        if request_id == self._request_id and self._current:
            self.state_changed.emit(self._current.message_key, "idle")

    def _synthesis_failed(self, request_id: int, error_code: str) -> None:
        if request_id != self._request_id or self._current is None:
            return
        key = self._current.message_key
        self.state_changed.emit(key, "error")
        self.failed.emit(key, error_code)

    def _thread_finished(self, thread: QThread, worker: QObject) -> None:
        worker.deleteLater()
        thread.deleteLater()
        if self._thread is not thread:
            return
        if self._worker is worker:
            self._worker = None
        self._thread = None
        if self._pending is not None and not self._shutdown:
            pending = self._pending
            self._pending = None
            self._start_request(pending)

    def _play_file(self, request: SpeechRequest, path: Path) -> None:
        self._current = request
        self._player.setSource(QUrl.fromLocalFile(str(path)))
        self._player.play()
        self.state_changed.emit(request.message_key, "playing")

    def _playback_state_changed(self, state) -> None:
        if state == QMediaPlayer.PlaybackState.PlayingState and self._current:
            self.state_changed.emit(self._current.message_key, "playing")

    def _media_status_changed(self, status) -> None:
        if status == QMediaPlayer.MediaStatus.EndOfMedia and self._current:
            if self._segment_index + 1 < len(self._current.segments):
                self._segment_index += 1
                self._player.setSource(QUrl())
                self._start_segment()
            else:
                self.state_changed.emit(
                    self._current.message_key, "finished"
                )
                self._current = None

    def _playback_error(self, *_args) -> None:
        if self._current:
            key = self._current.message_key
            self.state_changed.emit(key, "error")
            self.failed.emit(key, "tts_playback_error")

    def _start_native_segment(
        self, request: SpeechRequest, text: str, effective
    ) -> None:
        """Android 使用系统 TTS，避免打包 Edge TTS 的二进制依赖链。"""

        if self._native_tts is None:
            return
        self._native_tts.setRate(
            self._normalized_percent(effective.rate)
        )
        self._native_tts.setPitch(
            self._normalized_percent(effective.pitch)
        )
        self._native_tts.setVolume(
            max(
                0.0,
                min(
                    1.0
                    + self._normalized_percent(effective.volume),
                    1.0,
                ),
            )
        )
        self.state_changed.emit(request.message_key, "playing")
        self._native_tts.say(text)

    def _native_tts_state_changed(self, state) -> None:
        if self._current is None or self._native_tts is None:
            return
        if state == QTextToSpeech.State.Speaking:
            self.state_changed.emit(
                self._current.message_key, "playing"
            )
        elif state == QTextToSpeech.State.Ready:
            if self._segment_index + 1 < len(self._current.segments):
                self._segment_index += 1
                self._start_segment()
            else:
                key = self._current.message_key
                self._current = None
                self.state_changed.emit(
                    key, "finished"
                )
        elif state == QTextToSpeech.State.Error:
            key = self._current.message_key
            self._current = None
            self.state_changed.emit(key, "error")
            self.failed.emit(key, "tts_playback_error")

    def _provider(self) -> str:
        if self._settings is None:
            return "edge"
        value = str(self._settings.get("tts_provider", "edge")).lower()
        return (
            value
            if value in {"edge", "xfyun", "siliconflow", "indextts2"}
            else "edge"
        )

    def _siliconflow_api_key(self) -> str:
        getter = getattr(
            self._credentials, "get_siliconflow_tts_api_key", None
        )
        if not callable(getter):
            getter = getattr(
                self._credentials, "get_siliconflow_api_key", None
            )
        return str(getter() if callable(getter) else "").strip()

    def _xfyun_api_password(self) -> str:
        if self._xfyun_auth_method() != "password":
            return ""
        getter = getattr(
            self._credentials, "get_xfyun_tts_api_password", None
        )
        return str(getter() if callable(getter) else "").strip()

    def _xfyun_api_key(self) -> str:
        if self._xfyun_auth_method() != "hmac":
            return ""
        getter = getattr(
            self._credentials, "get_xfyun_tts_api_key", None
        )
        return str(getter() if callable(getter) else "").strip()

    def _xfyun_api_secret(self) -> str:
        if self._xfyun_auth_method() != "hmac":
            return ""
        getter = getattr(
            self._credentials, "get_xfyun_tts_api_secret", None
        )
        return str(getter() if callable(getter) else "").strip()

    def _xfyun_app_id(self) -> str:
        if self._settings is None:
            return ""
        return str(self._settings.get("xfyun_tts_app_id", "")).strip()

    def _xfyun_auth_method(self) -> str:
        if self._settings is None:
            return "password"
        value = str(
            self._settings.get("xfyun_tts_auth_method", "password")
        ).strip().lower()
        return value if value in {"password", "hmac"} else "password"

    def _xfyun_ready(self) -> bool:
        if not self._xfyun_app_id():
            return False
        if self._xfyun_auth_method() == "hmac":
            return bool(self._xfyun_api_key() and self._xfyun_api_secret())
        return bool(self._xfyun_api_password())

    def _index_tts2_base_url(self) -> str:
        value = (
            self._settings.get(
                "indextts2_base_url", DEFAULT_INDEXTTS2_BASE_URL
            )
            if self._settings is not None
            else DEFAULT_INDEXTTS2_BASE_URL
        )
        try:
            return normalize_index_tts2_base_url(str(value))
        except ValueError:
            return DEFAULT_INDEXTTS2_BASE_URL

    def _index_tts2_preset(self, profile: TtsProfile) -> str:
        character_preset = profile.index_tts2_preset.strip()
        if character_preset:
            return character_preset
        if self._settings is None:
            return DEFAULT_INDEXTTS2_PRESET
        return (
            str(
                self._settings.get(
                    "indextts2_preset", DEFAULT_INDEXTTS2_PRESET
                )
            ).strip()
            or DEFAULT_INDEXTTS2_PRESET
        )

    def _siliconflow_model(self) -> str:
        if self._settings is None:
            return DEFAULT_SILICONFLOW_TTS_MODEL
        return (
            str(
                self._settings.get(
                    "siliconflow_tts_model",
                    DEFAULT_SILICONFLOW_TTS_MODEL,
                )
            ).strip()
            or DEFAULT_SILICONFLOW_TTS_MODEL
        )

    def _siliconflow_voice(self, edge_voice: str) -> str:
        configured = (
            str(
                self._settings.get("siliconflow_tts_voice", "auto")
                if self._settings is not None
                else "auto"
            ).strip()
            or "auto"
        )
        if configured == "auto":
            configured = self._mapped_voice(edge_voice)
        if configured.startswith("speech:") or ":" in configured:
            return configured
        return f"{self._siliconflow_model()}:{configured}"

    def _xfyun_voice(self, edge_voice: str) -> str:
        configured = (
            str(
                self._settings.get(
                    "xfyun_tts_voice", DEFAULT_XFYUN_TTS_VOICE
                )
                if self._settings is not None
                else DEFAULT_XFYUN_TTS_VOICE
            ).strip()
            or DEFAULT_XFYUN_TTS_VOICE
        )
        available = deserialize_available_voices(
            self._settings.get("xfyun_tts_available_voices", "")
            if self._settings is not None
            else ""
        )
        if configured == "auto" or (
            available and configured not in available
        ):
            return automatic_voice(edge_voice, available)
        return configured

    @staticmethod
    def _mapped_voice(edge_voice: str) -> str:
        name = edge_voice.lower()
        if "yunjian" in name or "yunyang" in name:
            return "charles"
        if "yun" in name:
            return "david"
        if "xiaoyi" in name:
            return "diana"
        return "claire"

    @staticmethod
    def _mapped_xfyun_voice(edge_voice: str) -> str:
        return automatic_voice(edge_voice)

    @staticmethod
    def _siliconflow_input(text: str, emotion: str) -> str:
        instruction = _SILICONFLOW_EMOTION_PROMPTS.get(emotion)
        return (
            f"{instruction}。<|endofprompt|>{text}"
            if instruction
            else text
        )

    @staticmethod
    def _speed_from_rate(rate: str) -> float:
        try:
            percent = int(rate.removesuffix("%"))
        except ValueError:
            percent = 0
        return max(0.25, min(1.0 + percent / 100.0, 4.0))

    @staticmethod
    def _gain_from_volume(volume: str) -> float:
        try:
            percent = int(volume.removesuffix("%"))
        except ValueError:
            percent = 0
        return max(-10.0, min(percent / 5.0, 10.0))

    @staticmethod
    def _normalized_percent(value: str) -> float:
        try:
            percent = int(value.removesuffix("%"))
        except ValueError:
            percent = 0
        return max(-1.0, min(percent / 100.0, 1.0))

    def _provider_config(
        self, provider: str, edge_voice: str, profile: TtsProfile
    ) -> str:
        if provider == "siliconflow":
            return "\0".join(
                (
                    provider,
                    self._siliconflow_model(),
                    self._siliconflow_voice(edge_voice),
                )
            )
        if provider == "xfyun":
            return "\0".join(
                (
                    provider,
                    self._xfyun_auth_method(),
                    self._xfyun_app_id(),
                    self._xfyun_voice(edge_voice),
                    "plain-default-50-v1",
                )
            )
        if provider == "indextts2":
            return "\0".join(
                (
                    provider,
                    self._index_tts2_base_url(),
                    self._index_tts2_preset(profile),
                    "emotion-vector-v1",
                )
            )
        return provider

    @staticmethod
    def _cache_key(
        segment: SpeechSegment, effective, provider_config: str
    ) -> str:
        tuning = (
            ("50", "50", "50")
            if provider_config.startswith("xfyun\0")
            else (effective.rate, effective.pitch, effective.volume)
        )
        value = "\0".join(
            (
                provider_config,
                segment.text,
                effective.voice,
                effective.emotion,
                *tuning,
            )
        )
        return hashlib.sha256(value.encode("utf-8")).hexdigest()
