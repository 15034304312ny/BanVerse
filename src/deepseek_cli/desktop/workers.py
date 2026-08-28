"""在后台线程中执行同步模型流。"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import re
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from datetime import datetime, timezone
from email.utils import format_datetime
from pathlib import Path
from threading import Event
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import Request, urlopen

from PySide6.QtCore import QObject, Signal, Slot

from ..chat_service import ChatEventType, ChatStreamService
from ..error_codes import image_error_code
from ..gateway import Message
from .assets import install_generated_image
from .image_service import image_context
from .index_tts2 import index_tts2_endpoint

SILICONFLOW_SPEECH_URL = "https://api.siliconflow.cn/v1/audio/speech"
XFYUN_SUPER_TTS_URL = (
    "wss://cbm01.cn-huabei-1.xf-yun.com/v1/private/mcd9m97e6"
)
MAX_TTS_AUDIO_BYTES = 40 * 1024 * 1024
LOGGER = logging.getLogger("banverse.startup")


class ChatWorker(QObject):
    reasoning = Signal(str)
    content = Signal(str)
    completed = Signal(str)
    cancelled = Signal()
    failed = Signal(str)
    image_described = Signal(str)
    image_analysis_failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service: ChatStreamService,
        model: str,
        history: Sequence[Message],
        user_text: str,
        *,
        system_prompt: str = "",
        example_messages: Sequence[Message] = (),
        post_history_prompt: str = "",
        temperature: float | None = None,
        image_service=None,
        image_path: str = "",
    ) -> None:
        super().__init__()
        self._service = service
        self._model = model
        self._history = list(history)
        self._user_text = user_text
        self._system_prompt = system_prompt
        self._example_messages = list(example_messages)
        self._post_history_prompt = post_history_prompt
        self._temperature = temperature
        self._image_service = image_service
        self._image_path = image_path
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            request_text = self._user_text
            if self._image_path:
                description = ""
                if self._image_service is not None:
                    try:
                        description = self._image_service.describe_image(
                            self._image_path, self._user_text
                        )
                    except Exception as exc:
                        self.image_analysis_failed.emit(self._error_code(exc))
                    else:
                        self.image_described.emit(description)
                request_text = image_context(self._user_text, description)
            for event in self._service.stream(
                self._model,
                self._history,
                request_text,
                cancel_event=self._cancel_event,
                system_prompt=self._system_prompt,
                example_messages=self._example_messages,
                post_history_prompt=self._post_history_prompt,
                temperature=self._temperature,
            ):
                if event.type is ChatEventType.REASONING:
                    self.reasoning.emit(event.text)
                elif event.type is ChatEventType.CONTENT:
                    self.content.emit(event.text)
                elif event.type is ChatEventType.COMPLETED:
                    self.completed.emit(event.text)
                elif event.type is ChatEventType.CANCELLED:
                    self.cancelled.emit()
                else:
                    self.failed.emit(event.error_code)
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancel_event.set()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        return image_error_code(exc)


class ImageGenerationWorker(QObject):
    completed = Signal(str)
    cancelled = Signal()
    failed = Signal(str)
    finished = Signal()

    def __init__(
        self,
        service,
        prompt: str,
        *,
        app_data_root: str | Path | None = None,
        image_installer: Callable[..., str] = install_generated_image,
    ) -> None:
        super().__init__()
        self._service = service
        self._prompt = prompt
        self._app_data_root = app_data_root
        self._image_installer = image_installer
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            image_bytes = self._service.generate_image(self._prompt)
            if self._cancel_event.is_set():
                self.cancelled.emit()
                return
            path = self._image_installer(
                image_bytes, app_data_root=self._app_data_root
            )
            if self._cancel_event.is_set():
                with suppress(OSError):
                    Path(path).unlink(missing_ok=True)
                self.cancelled.emit()
                return
            self.completed.emit(path)
        except Exception as exc:
            LOGGER.exception("Image generation failed")
            self.failed.emit(ChatWorker._error_code(exc))
        finally:
            self.finished.emit()

    def cancel(self) -> None:
        self._cancel_event.set()


class TtsSynthesisWorker(QObject):
    completed = Signal(int, str)
    cancelled = Signal(int)
    failed = Signal(int, str)
    finished = Signal()

    def __init__(
        self,
        request_id: int,
        text: str,
        voice: str,
        rate: str,
        pitch: str,
        volume: str,
        output_path: str,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._text = text
        self._voice = voice
        self._rate = rate
        self._pitch = pitch
        self._volume = volume
        self._output = Path(output_path)
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            asyncio.run(self._synthesize())
        except Exception as exc:
            self._cleanup()
            self.failed.emit(self._request_id, self._error_code(exc))
        finally:
            self.finished.emit()

    async def _synthesize(self) -> None:
        import edge_tts

        partial = self._output.with_suffix(".part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        communicate = edge_tts.Communicate(
            self._text,
            self._voice,
            rate=self._rate,
            pitch=self._pitch,
            volume=self._volume,
        )
        wrote_audio = False
        with partial.open("wb") as stream:
            async for chunk in communicate.stream():
                if self._cancel_event.is_set():
                    self._cleanup()
                    self.cancelled.emit(self._request_id)
                    return
                if chunk.get("type") == "audio" and chunk.get("data"):
                    stream.write(chunk["data"])
                    wrote_audio = True
        if self._cancel_event.is_set():
            self._cleanup()
            self.cancelled.emit(self._request_id)
            return
        if not wrote_audio or not partial.exists() or partial.stat().st_size == 0:
            self._cleanup()
            self.failed.emit(self._request_id, "tts_empty_audio")
            return
        partial.replace(self._output)
        self.completed.emit(self._request_id, str(self._output))

    def cancel(self) -> None:
        self._cancel_event.set()

    def _cleanup(self) -> None:
        for path in (self._output, self._output.with_suffix(".part")):
            with suppress(OSError):
                path.unlink(missing_ok=True)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        if "timeout" in name or "timeout" in message:
            return "tts_timeout"
        if "voice" in message or "403" in message:
            return "tts_invalid_voice"
        if "connect" in name or "network" in message:
            return "tts_network"
        return "tts_service_error"


class SiliconFlowTtsSynthesisWorker(QObject):
    """调用硅基流动 `/audio/speech` 并把二进制 MP3 写入缓存。"""

    completed = Signal(int, str)
    cancelled = Signal(int)
    failed = Signal(int, str)
    finished = Signal()

    def __init__(
        self,
        request_id: int,
        api_key: str,
        text: str,
        model: str,
        voice: str,
        speed: float,
        gain: float,
        output_path: str,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._api_key = api_key.strip()
        self._text = text
        self._model = model
        self._voice = voice
        self._speed = max(0.25, min(float(speed), 4.0))
        self._gain = max(-10.0, min(float(gain), 10.0))
        self._output = Path(output_path)
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            self._synthesize()
        except Exception as exc:
            self._cleanup()
            self.failed.emit(self._request_id, self._error_code(exc))
        finally:
            self.finished.emit()

    def _synthesize(self) -> None:
        if not self._api_key:
            raise ValueError("SiliconFlow API key is missing")
        payload = {
            "model": self._model,
            "input": self._text,
            "voice": self._voice,
            "response_format": "mp3",
            "sample_rate": 44100,
            "stream": False,
            "speed": self._speed,
            "gain": self._gain,
        }
        request = Request(
            SILICONFLOW_SPEECH_URL,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "Accept": "audio/mpeg",
            },
            method="POST",
        )
        partial = self._output.with_suffix(".part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        try:
            with urlopen(request, timeout=240) as response, partial.open(
                "wb"
            ) as stream:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    if self._cancel_event.is_set():
                        self._cleanup()
                        self.cancelled.emit(self._request_id)
                        return
                    written += len(chunk)
                    if written > MAX_TTS_AUDIO_BYTES:
                        raise RuntimeError("TTS audio response is too large")
                    stream.write(chunk)
        except HTTPError as exc:
            detail = exc.read(4_096).decode("utf-8", errors="replace")
            error = RuntimeError(
                f"SiliconFlow TTS HTTP {exc.code}: {detail}"
            )
            error.status_code = exc.code
            raise error from exc
        except URLError as exc:
            raise RuntimeError(
                f"SiliconFlow TTS network error: {exc.reason}"
            ) from exc
        if self._cancel_event.is_set():
            self._cleanup()
            self.cancelled.emit(self._request_id)
            return
        if written == 0:
            self._cleanup()
            self.failed.emit(self._request_id, "tts_empty_audio")
            return
        partial.replace(self._output)
        self.completed.emit(self._request_id, str(self._output))

    def cancel(self) -> None:
        self._cancel_event.set()

    def _cleanup(self) -> None:
        for path in (self._output, self._output.with_suffix(".part")):
            with suppress(OSError):
                path.unlink(missing_ok=True)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        if status_code in {401, 403} or "api key" in message:
            return "tts_authentication"
        if status_code == 429 or "quota" in message:
            return "tts_quota"
        if status_code == 400 and "voice" in message:
            return "tts_invalid_voice"
        if "timeout" in name or "timeout" in message:
            return "tts_timeout"
        if (
            "urlerror" in name
            or "connect" in message
            or "network" in message
        ):
            return "tts_network"
        return "tts_service_error"


class IndexTts2CatalogWorker(QObject):
    """检查 IndexTTS2 本地服务状态并读取预设。"""

    completed = Signal(object, str)
    failed = Signal(str)
    finished = Signal()

    def __init__(self, base_url: str) -> None:
        super().__init__()
        self._base_url = base_url

    @Slot()
    def run(self) -> None:
        try:
            health = self._get_json("health")
            payload = self._get_json("v1/presets")
            data = payload.get("data", [])
            presets = tuple(
                str(item.get("name", "")).strip()
                for item in data
                if isinstance(item, dict) and str(item.get("name", "")).strip()
            )
            self.completed.emit(
                presets,
                str(health.get("status", payload.get("status", "unknown"))),
            )
        except Exception as exc:
            self.failed.emit(str(exc)[:500])
        finally:
            self.finished.emit()

    def _get_json(self, path: str) -> dict:
        request = Request(
            index_tts2_endpoint(self._base_url, path),
            headers={"Accept": "application/json"},
        )
        try:
            with urlopen(request, timeout=5) as response:
                value = json.loads(response.read(1_048_576).decode("utf-8"))
        except HTTPError as exc:
            raise RuntimeError(f"IndexTTS2 HTTP {exc.code}") from exc
        except URLError as exc:
            raise RuntimeError(f"IndexTTS2 无法连接：{exc.reason}") from exc
        if not isinstance(value, dict):
            raise RuntimeError("IndexTTS2 返回了无效响应。")
        return value


class IndexTts2SynthesisWorker(QObject):
    """调用 IndexTTS2 本地 API 并保存 WAV 语音。"""

    completed = Signal(int, str)
    cancelled = Signal(int)
    failed = Signal(int, str)
    finished = Signal()

    def __init__(
        self,
        request_id: int,
        base_url: str,
        text: str,
        preset: str,
        emotion: str,
        output_path: str,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._base_url = base_url
        self._text = text.strip()
        self._preset = preset.strip()
        self._emotion = emotion.strip().lower() or "neutral"
        self._output = Path(output_path)
        self._cancel_event = Event()

    @Slot()
    def run(self) -> None:
        try:
            self._synthesize()
        except Exception as exc:
            self._cleanup()
            self.failed.emit(self._request_id, self._error_code(exc))
        finally:
            self.finished.emit()

    def _synthesize(self) -> None:
        if not self._text:
            raise ValueError("IndexTTS2 text is empty")
        if not self._preset:
            raise ValueError("IndexTTS2 preset is missing")
        payload = {
            "input": self._text,
            "preset": self._preset,
            "emotion": self._emotion,
            "emotion_weight": 0.65,
        }
        request = Request(
            index_tts2_endpoint(self._base_url, "v1/audio/speech"),
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Accept": "audio/wav",
            },
            method="POST",
        )
        partial = self._output.with_suffix(".part")
        partial.parent.mkdir(parents=True, exist_ok=True)
        written = 0
        prefix = b""
        try:
            with urlopen(request, timeout=900) as response, partial.open(
                "wb"
            ) as stream:
                while True:
                    chunk = response.read(64 * 1024)
                    if not chunk:
                        break
                    if self._cancel_event.is_set():
                        self._cleanup()
                        self.cancelled.emit(self._request_id)
                        return
                    if not prefix:
                        prefix = chunk[:4]
                    written += len(chunk)
                    if written > MAX_TTS_AUDIO_BYTES:
                        raise RuntimeError("IndexTTS2 audio response is too large")
                    stream.write(chunk)
        except HTTPError as exc:
            detail = exc.read(4_096).decode("utf-8", errors="replace")
            error = RuntimeError(f"IndexTTS2 HTTP {exc.code}: {detail}")
            error.status_code = exc.code
            raise error from exc
        except URLError as exc:
            raise RuntimeError(
                f"IndexTTS2 network error: {exc.reason}"
            ) from exc
        if self._cancel_event.is_set():
            self._cleanup()
            self.cancelled.emit(self._request_id)
            return
        if written == 0:
            self._cleanup()
            self.failed.emit(self._request_id, "tts_empty_audio")
            return
        if prefix != b"RIFF":
            raise RuntimeError("IndexTTS2 returned a non-WAV response")
        partial.replace(self._output)
        self.completed.emit(self._request_id, str(self._output))

    def cancel(self) -> None:
        self._cancel_event.set()

    def _cleanup(self) -> None:
        for path in (self._output, self._output.with_suffix(".part")):
            with suppress(OSError):
                path.unlink(missing_ok=True)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__.lower()
        message = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        if status_code == 404 or "preset" in message:
            return "tts_invalid_voice"
        if "timeout" in name or "timed out" in message:
            return "tts_timeout"
        if (
            "urlerror" in name
            or "connect" in message
            or "network" in message
            or "refused" in message
        ):
            return "tts_network"
        return "tts_service_error"


class XfyunSuperTtsSynthesisWorker(QObject):
    """调用讯飞超拟人 WebSocket API 并按 seq 重排 MP3 音频帧。"""

    completed = Signal(int, str)
    cancelled = Signal(int)
    failed = Signal(int, str)
    finished = Signal()

    def __init__(
        self,
        request_id: int,
        app_id: str,
        api_password: str,
        text: str,
        voice: str,
        speed: int,
        pitch: int,
        volume: int,
        output_path: str,
        *,
        api_key: str = "",
        api_secret: str = "",
        auth_date: str = "",
        websocket_factory=None,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._app_id = app_id.strip()
        self._api_password = api_password.strip()
        self._api_key = api_key.strip()
        self._api_secret = api_secret.strip()
        self._auth_date = auth_date.strip()
        self._text = self._sanitize_text(text)
        self._voice = voice.strip()
        self._speed = max(0, min(int(speed), 100))
        self._pitch = max(0, min(int(pitch), 100))
        self._volume = max(0, min(int(volume), 100))
        self._output = Path(output_path)
        self._cancel_event = Event()
        self._websocket_factory = websocket_factory

    @Slot()
    def run(self) -> None:
        try:
            self._synthesize()
        except Exception as exc:
            LOGGER.warning(
                "Xfyun TTS failed; voice=%s code=%s error=%s",
                self._voice,
                getattr(exc, "error_code", ""),
                str(exc)[:500],
            )
            self._cleanup()
            self.failed.emit(self._request_id, self._error_code(exc))
        finally:
            self.finished.emit()

    def _synthesize(self) -> None:
        if not self._app_id:
            raise ValueError("Xfyun APPID is missing")
        if not self._api_password and not (
            self._api_key and self._api_secret
        ):
            raise ValueError(
                "Xfyun API Password or APIKey/APISecret is missing"
            )
        if not self._voice:
            raise ValueError("Xfyun voice is missing")
        encoded = self._text.encode("utf-8")
        if not encoded or len(encoded) > 64 * 1024:
            raise ValueError("Xfyun TTS text is empty or exceeds 64K")
        factory = self._websocket_factory
        if factory is None:
            from websocket import create_connection

            factory = create_connection
        connection_url = XFYUN_SUPER_TTS_URL
        headers = None
        if self._api_password:
            headers = {"x-api-key": self._api_password}
        else:
            connection_url = self._signed_url()
        connection_options = {
            "header": headers,
            "timeout": 30,
        }
        ca_bundle = os.environ.get("SSL_CERT_FILE", "").strip()
        if ca_bundle:
            connection_options["sslopt"] = {"ca_certs": ca_bundle}
        connection = factory(connection_url, **connection_options)
        chunks: dict[int, bytes] = {}
        total = 0
        deadline = time.monotonic() + 240
        try:
            settimeout = getattr(connection, "settimeout", None)
            if callable(settimeout):
                settimeout(1)
            connection.send(
                json.dumps(
                    self._payload(encoded),
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
            )
            final_received_at: float | None = None
            while True:
                if self._cancel_event.is_set():
                    self.cancelled.emit(self._request_id)
                    return
                now = time.monotonic()
                if now >= deadline:
                    raise TimeoutError("Xfyun TTS response timed out")
                if final_received_at is not None and now >= (
                    final_received_at + 0.75
                ):
                    break
                try:
                    frame = connection.recv()
                except Exception as exc:
                    if "timeout" in type(exc).__name__.lower():
                        continue
                    if final_received_at is not None:
                        break
                    raise
                if frame in {None, ""}:
                    if final_received_at is not None:
                        break
                    continue
                response = json.loads(
                    frame.decode("utf-8")
                    if isinstance(frame, bytes)
                    else frame
                )
                header = response.get("header", {})
                code = int(header.get("code", 0) or 0)
                if code:
                    error = RuntimeError(
                        f"Xfyun TTS {code}: {header.get('message', '')}"
                    )
                    error.error_code = code
                    raise error
                audio = response.get("payload", {}).get("audio", {})
                if isinstance(audio, dict) and audio.get("audio"):
                    try:
                        data = base64.b64decode(
                            audio["audio"], validate=True
                        )
                    except (TypeError, ValueError) as exc:
                        raise RuntimeError(
                            "Xfyun TTS returned invalid audio"
                        ) from exc
                    seq = int(audio.get("seq", len(chunks)))
                    if seq not in chunks:
                        chunks[seq] = data
                        total += len(data)
                    if total > MAX_TTS_AUDIO_BYTES:
                        raise RuntimeError(
                            "Xfyun TTS audio response is too large"
                        )
                    if int(audio.get("status", 0) or 0) == 2:
                        final_received_at = final_received_at or time.monotonic()
                if int(header.get("status", 0) or 0) == 2:
                    final_received_at = final_received_at or time.monotonic()
        finally:
            with suppress(Exception):
                connection.close()
        if self._cancel_event.is_set():
            self.cancelled.emit(self._request_id)
            return
        if not chunks or total <= 0:
            self.failed.emit(self._request_id, "tts_empty_audio")
            return
        self._output.parent.mkdir(parents=True, exist_ok=True)
        partial = self._output.with_suffix(".part")
        with partial.open("wb") as stream:
            for seq in sorted(chunks):
                stream.write(chunks[seq])
        partial.replace(self._output)
        self.completed.emit(self._request_id, str(self._output))

    def _payload(self, encoded_text: bytes) -> dict:
        oral = {}
        if self._voice.lower().startswith("x4_"):
            oral = {
                "spark_assist": 0,
                "stop_split": 1,
                "remain": 1,
            }
        return {
            "header": {"app_id": self._app_id, "status": 2},
            "parameter": {
                "oral": oral,
                "tts": {
                    "vcn": self._voice,
                    "speed": self._speed,
                    "volume": self._volume,
                    "pitch": self._pitch,
                    "bgs": 0,
                    "reg": 0,
                    "rdn": 0,
                    "rhy": 0,
                    "audio": {
                        "encoding": "lame",
                        "sample_rate": 24000,
                        "channels": 1,
                        "bit_depth": 16,
                        "frame_size": 0,
                    },
                },
            },
            "payload": {
                "text": {
                    "encoding": "utf8",
                    "compress": "raw",
                    "format": "plain",
                    "status": 2,
                    "seq": 0,
                    # 文档示例写作普通字符串，但当前生产接口会明确校验
                    # payload.text.text 必须是 Base64；以实测协议为准。
                    "text": base64.b64encode(encoded_text).decode("ascii"),
                }
            },
        }

    def _signed_url(self) -> str:
        """按讯飞官方 ws 鉴权方式二生成 HMAC-SHA256 URL。"""

        parsed = urlsplit(XFYUN_SUPER_TTS_URL)
        date = self._auth_date or format_datetime(
            datetime.now(timezone.utc),
            usegmt=True,
        )
        signature_origin = (
            f"host: {parsed.netloc}\n"
            f"date: {date}\n"
            f"GET {parsed.path} HTTP/1.1"
        )
        digest = hmac.new(
            self._api_secret.encode("utf-8"),
            signature_origin.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        signature = base64.b64encode(digest).decode("ascii")
        authorization_origin = (
            f'api_key="{self._api_key}", algorithm="hmac-sha256", '
            f'headers="host date request-line", signature="{signature}"'
        )
        authorization = base64.b64encode(
            authorization_origin.encode("utf-8")
        ).decode("ascii")
        query = urlencode(
            {
                "host": parsed.netloc,
                "date": date,
                "authorization": authorization,
            }
        )
        return f"{XFYUN_SUPER_TTS_URL}?{query}"

    def cancel(self) -> None:
        self._cancel_event.set()

    def _cleanup(self) -> None:
        for path in (self._output, self._output.with_suffix(".part")):
            with suppress(OSError):
                path.unlink(missing_ok=True)

    @staticmethod
    def _sanitize_text(text: str) -> str:
        value = re.sub(r"[\x00-\x09\x0b-\x1f\x7f]", " ", text)
        value = re.sub(r"\[p\d{1,4}\]", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\[=[^\]\r\n]{1,32}\]", "", value)
        value = re.sub(
            r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]",
            "",
            value,
        )
        return " ".join(value.split()).strip()

    @staticmethod
    def _error_code(exc: Exception) -> str:
        code = getattr(exc, "error_code", None)
        message = str(exc).lower()
        name = type(exc).__name__.lower()
        if code == 11200 or code == 10010 or "licc" in message:
            return "tts_voice_not_enabled"
        if code in {10313} or (
            "appid" in message
            or "api password" in message
            or "apikey" in message
            or "apisecret" in message
        ):
            return "tts_authentication"
        if code == 11201 or "quota" in message:
            return "tts_quota"
        if "timeout" in name or "timeout" in message:
            return "tts_timeout"
        if "connect" in message or "websocket" in name or "ssl" in message:
            return "tts_network"
        if (
            "voice" in message
            or "parameter.tts.vcn" in message
            or code == 10139
        ):
            return "tts_invalid_voice"
        return "tts_service_error"
