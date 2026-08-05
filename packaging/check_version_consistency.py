"""校验项目各处版本号保持一致，防止发布时版本漂移。

当前版本号出现在三处，且打包产物（EXE/APK）不携带 ``pyproject.toml``，因此
运行时无法简单地从单一点读取：

- ``pyproject.toml`` 的 ``[project].version``（安装元数据与分发版本）
- ``src/deepseek_cli/branding.py`` 的 ``PRODUCT_VERSION``（运行时显示与 UA）
- ``packaging/android/build_android.sh`` 的 ``APP_VERSION``（APK 命名）

本脚本作为唯一权威校验点：本地可执行、pytest 回归（见
``tests/test_version_consistency.py``）与 Android 构建脚本都会调用它；
任意一处版本号改动后其余处未同步，校验即失败。
"""

from __future__ import annotations

import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_VERSION_PATTERNS = (
    ("pyproject.toml", re.compile(r'^version = "([^"]+)"', re.MULTILINE)),
    (
        "src/deepseek_cli/branding.py",
        re.compile(r'^PRODUCT_VERSION = "([^"]+)"', re.MULTILINE),
    ),
    (
        "packaging/android/build_android.sh",
        re.compile(r'^APP_VERSION="([^"]+)"', re.MULTILINE),
    ),
)


def collected_versions(root: Path | None = None) -> dict[str, str | None]:
    """按文件相对路径收集三处版本号；缺失或解析不到为 None。"""

    root = Path(root or PROJECT_ROOT)
    versions: dict[str, str | None] = {}
    for relative, pattern in _VERSION_PATTERNS:
        path = root / relative
        if not path.exists():
            versions[relative] = None
            continue
        match = pattern.search(path.read_text(encoding="utf-8"))
        versions[relative] = match.group(1) if match else None
    return versions


def version_mismatches(root: Path | None = None) -> list[str]:
    """以 pyproject.toml 为基准，返回其余各处版本不一致的说明列表。"""

    versions = collected_versions(root)
    expected = versions.get("pyproject.toml")
    if not expected:
        return ["pyproject.toml 中缺少 version 定义"]
    mismatches = []
    for relative, version in versions.items():
        if relative == "pyproject.toml":
            continue
        if version != expected:
            mismatches.append(
                f"{relative}: 版本 {version!r} 与 pyproject.toml 的 "
                f"{expected!r} 不一致"
            )
    return mismatches


def main() -> int:
    problems = version_mismatches()
    if problems:
        print("错误：项目版本号不一致：")
        for problem in problems:
            print(f"  - {problem}")
        print("请同步修改三处版本号后重试。")
        return 1
    versions = collected_versions()
    print(f"版本号一致：{versions['pyproject.toml']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
