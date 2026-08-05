"""DeepSeek 命令行会话的核心逻辑。"""

from __future__ import annotations

import sys
from collections.abc import Callable
from typing import TextIO

from .chat_service import ChatEventType, ChatStreamService
from .gateway import ChatGateway, Message
from .model_catalog import (
    MODEL_CHAT,
    MODEL_REASONER,  # noqa: F401 - re-export 供测试与第三方调用
    resolve_model,
)

_HELP = """可用命令：
  /help             显示帮助
  /clear            清空当前对话历史
  /model            查看当前模型
  /model chat       切换到 deepseek-v4-flash
  /model reasoner   切换到 deepseek-v4-pro
  /exit 或 /quit    退出程序"""


class ChatApplication:
    """管理命令、会话历史与流式输出。"""

    def __init__(
        self,
        gateway: ChatGateway,
        *,
        input_fn: Callable[[str], str] = input,
        output: TextIO = sys.stdout,
    ) -> None:
        self._gateway = gateway
        self._service = ChatStreamService(lambda: self._gateway)
        self._input = input_fn
        self._output = output
        self._model = MODEL_CHAT
        self._history: list[Message] = []

    @property
    def model(self) -> str:
        return self._model

    @property
    def history(self) -> tuple[Message, ...]:
        return tuple(self._history)

    def run(self) -> None:
        self._write("DeepSeek 对话 CLI（输入 /help 查看命令）\n")
        while True:
            try:
                text = self._input("你：").strip()
            except EOFError:
                self._write("\n已退出。\n")
                return
            except KeyboardInterrupt:
                self._write("\n已退出。\n")
                return

            if not text:
                continue
            if text.startswith("/"):
                if self._handle_command(text):
                    return
                continue
            self._chat(text)

    def _handle_command(self, command: str) -> bool:
        normalized = " ".join(command.lower().split())
        if normalized == "/help":
            self._write(f"{_HELP}\n")
        elif normalized == "/clear":
            self._history.clear()
            self._write("对话历史已清空。\n")
        elif normalized == "/model":
            self._write(f"当前模型：{self._model}\n")
        elif normalized.startswith("/model "):
            resolved = resolve_model(normalized.removeprefix("/model "))
            if resolved is None:
                self._write("无法识别该模型。输入 /help 查看可用模型。\n")
            else:
                self._model = resolved
                self._write(
                    f"已切换模型：{self._model}（上下文已保留）\n"
                )
        elif normalized in {"/exit", "/quit"}:
            self._write("已退出。\n")
            return True
        else:
            self._write("无法识别该命令。输入 /help 查看可用命令。\n")
        return False

    def _chat(self, user_text: str) -> None:
        pending_user = Message(role="user", content=user_text)
        reasoning_started = False
        answer_started = False

        try:
            for event in self._service.stream(
                self._model, self._history, user_text
            ):
                if event.type is ChatEventType.REASONING:
                    if not reasoning_started:
                        self._write("思考过程：")
                        reasoning_started = True
                    self._write(event.text)
                elif event.type is ChatEventType.CONTENT:
                    if not answer_started:
                        self._write(
                            "\n回答：" if reasoning_started else "助手："
                        )
                        answer_started = True
                    self._write(event.text)
                elif event.type is ChatEventType.COMPLETED:
                    if reasoning_started or answer_started:
                        self._write("\n")
                    self._history.extend(
                        (
                            pending_user,
                            Message(role="assistant", content=event.text),
                        )
                    )
                elif event.type is ChatEventType.CANCELLED:
                    self._write("\n已中断本次回答，对话历史未更改。\n")
                elif event.error_code == "empty_response":
                    if reasoning_started or answer_started:
                        self._write("\n")
                    self._write("未收到有效回答，对话历史未更改。\n")
                else:
                    self._write(
                        "\n请求失败，请检查网络、API 密钥或服务状态后重试。\n"
                    )
        except KeyboardInterrupt:
            self._write("\n已中断本次回答，对话历史未更改。\n")

    def _write(self, text: str) -> None:
        self._output.write(text)
        self._output.flush()
