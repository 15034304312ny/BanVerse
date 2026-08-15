"""发布入口复用唯一版本源的回归测试。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

from deepseek_cli._version import __version__
from deepseek_cli.branding import PRODUCT_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _load_check_module():
    path = PROJECT_ROOT / "packaging" / "check_version_consistency.py"
    spec = importlib.util.spec_from_file_location(
        "check_version_consistency", path
    )
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_project_version_consistent() -> None:
    check = _load_check_module()
    assert check.version_mismatches(PROJECT_ROOT) == []
    assert check.project_version(PROJECT_ROOT) == __version__
    assert __version__ == PRODUCT_VERSION


def test_installer_preserves_user_data_and_requires_injected_version() -> None:
    installer = (PROJECT_ROOT / "packaging" / "installer.iss").read_text(
        encoding="utf-8"
    )

    assert "#ifndef MyAppVersion" in installer
    assert "[UninstallDelete]" not in installer
    assert "{userappdata}" not in installer
