"""读取 BanVerse 的唯一版本号来源，供各平台构建脚本复用。"""

from __future__ import annotations

import re
import runpy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
VERSION_PATTERN = re.compile(
    r"^[0-9]+\.[0-9]+\.[0-9]+(?:[-+][0-9A-Za-z.-]+)?$"
)


def project_version(root: Path | None = None) -> str:
    """读取 ``src/deepseek_cli/_version.py`` 中的 ``__version__``。"""

    root = Path(root or PROJECT_ROOT)
    version_file = root / "src" / "deepseek_cli" / "_version.py"
    if not version_file.is_file():
        raise RuntimeError(f"找不到版本文件：{version_file}")
    namespace = runpy.run_path(str(version_file))
    value = str(namespace.get("__version__", "")).strip()
    if not VERSION_PATTERN.fullmatch(value):
        raise RuntimeError(f"无效的项目版本号：{value!r}")
    return value


def main() -> int:
    print(project_version())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
