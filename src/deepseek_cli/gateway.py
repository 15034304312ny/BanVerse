"""模型流式响应的抽象边界。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Protocol, Sequence


@dataclass(frozen=True, slots=True)
class Message:
    """发送给模型或保存到会话中的一条消息。"""

    role: str
    content: str


@dataclass(frozen=True, slots=True)
class StreamDelta:
    """一次流式增量；正文和推理内容可能同时为空。"""

    content: str = ""
    reasoning_content: str = ""


class ChatGateway(Protocol):
    """CLI 所依赖的最小模型网关协议。"""

    def stream_chat(
        self,
        model: str,
        messages: Sequence[Message],
        *,
        system_prompt: str = "",
        temperature: float | None = None,
    ) -> Iterable[StreamDelta]:
        """同步返回模型响应的流式增量。"""
