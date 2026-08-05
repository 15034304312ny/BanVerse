"""版本号一致性回归测试。

防止 pyproject.toml / branding.py / build_android.sh 三处版本号漂移；
构建、打包前跑一遍 pytest 即可拦截。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

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
