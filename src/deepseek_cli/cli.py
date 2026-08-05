"""控制台入口。"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from typing import TextIO

from .anthropic_gateway import AnthropicDeepSeekGateway
from .app import ChatApplication


def main(
    *,
    environ: Mapping[str, str] | None = None,
    output: TextIO = sys.stdout,
) -> int:
    """创建依赖并启动 CLI。"""

    environment = os.environ if environ is None else environ
    api_key = environment.get("DEEPSEEK_API_KEY", "").strip()
    if not api_key:
        output.write(
            "错误：未设置 DEEPSEEK_API_KEY。请先配置环境变量后再运行。\n"
        )
        return 2

    try:
        gateway = AnthropicDeepSeekGateway(api_key=api_key)
    except Exception:
        output.write("错误：无法初始化 DeepSeek 客户端，请检查本地依赖和配置。\n")
        return 1

    ChatApplication(gateway, output=output).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
