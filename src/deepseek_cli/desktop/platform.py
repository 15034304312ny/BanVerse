"""桌面与 Android 运行时能力检测。"""

from __future__ import annotations

import os
import sys

from PySide6.QtCore import QSysInfo


def is_android_platform() -> bool:
    """检测真实 Android 运行时，并提供仅供 UI 测试的显式覆盖。"""

    override = os.environ.get("DEEPSEEK_CHAT_PLATFORM", "").strip().lower()
    if override:
        return override == "android"
    if sys.platform == "android" or "ANDROID_ARGUMENT" in os.environ:
        return True
    try:
        return QSysInfo.productType().strip().lower() == "android"
    except (AttributeError, RuntimeError):
        return False
