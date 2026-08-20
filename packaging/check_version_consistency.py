"""校验所有发布入口都从 ``_version.py`` 读取版本，防止产物漂移。"""

from __future__ import annotations

import re
import runpy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

_REQUIRED_SNIPPETS = {
    "pyproject.toml": (
        'dynamic = ["version"]',
        'version = {attr = "deepseek_cli._version.__version__"}',
        'license = "Apache-2.0"',
    ),
    "src/deepseek_cli/branding.py": (
        "from ._version import __version__ as PRODUCT_VERSION",
    ),
    "packaging/deepseek_app.spec": (
        "read_project_version.py",
        'name=f"BanVerse-{app_version}"',
    ),
    "packaging/android/build_android.sh": (
        "read_project_version.py",
        "check_release_source.py",
        'APP_VERSION="$("${PYTHON_BIN}"',
    ),
    "packaging/android/patch_buildozer_spec.py": ("--app-version",),
    "packaging/installer.iss": (
        "#ifndef MyAppVersion",
        "BanVerse-{#MyAppVersion}-Setup",
    ),
    "packaging/verify_smoke.ps1": (
        "src\\deepseek_cli\\_version.py",
        '$ProcessName = "BanVerse-$Version"',
    ),
    "packaging/build_windows.ps1": (
        "read_project_version.py",
        "check_release_source.py",
        "sign_windows.ps1",
    ),
    "packaging/build_windows_self_signed.ps1": (
        "build_windows.ps1",
        'TrustMode = "SelfSigned"',
    ),
    "packaging/install_windows_signing_tools.ps1": (
        "Microsoft Windows SDK BuildTools NuGet signature",
        "Get-AuthenticodeSignature",
        "verify -All",
    ),
    "packaging/android/build_android_release.sh": (
        "BANVERSE_ANDROID_BUILD_VARIANT=release",
    ),
    "packaging/android/run_on_device.ps1": (
        "src\\deepseek_cli\\_version.py",
        '"dist\\android\\BanVerse-$version-android16-arm64-v8a-release.apk"',
    ),
}

_BANNED_PATTERNS = {
    "pyproject.toml": (re.compile(r'^version\s*=\s*"', re.MULTILINE),),
    "packaging/deepseek_app.spec": (
        re.compile(r'name\s*=\s*"BanVerse-[0-9]'),
    ),
    "packaging/android/build_android.sh": (
        re.compile(r'^APP_VERSION="[0-9]', re.MULTILINE),
    ),
    "packaging/installer.iss": (
        re.compile(r'BanVerse-[0-9]+\.[0-9]+\.[0-9]+'),
        re.compile(r'MyAppVersion\s+"[0-9]'),
    ),
    "packaging/verify_smoke.ps1": (
        re.compile(r'BanVerse-[0-9]+\.[0-9]+\.[0-9]+'),
    ),
    "README.md": (
        re.compile(r'BanVerse-[0-9]+\.[0-9]+\.[0-9]+'),
    ),
}


def project_version(root: Path | None = None) -> str:
    root = Path(root or PROJECT_ROOT)
    reader_path = root / "packaging" / "read_project_version.py"
    if not reader_path.is_file():
        raise RuntimeError(f"找不到版本读取器：{reader_path}")
    reader = runpy.run_path(str(reader_path))
    return reader["project_version"](root)


def collected_versions(root: Path | None = None) -> dict[str, str | None]:
    """保留原检查器接口；现在只返回唯一版本源。"""

    root = Path(root or PROJECT_ROOT)
    try:
        version = project_version(root)
    except (OSError, RuntimeError, ValueError):
        version = None
    return {"src/deepseek_cli/_version.py": version}


def version_mismatches(root: Path | None = None) -> list[str]:
    """返回发布入口未复用唯一版本源或仍含硬编码的说明。"""

    root = Path(root or PROJECT_ROOT)
    problems: list[str] = []
    try:
        project_version(root)
    except (OSError, RuntimeError, ValueError) as exc:
        problems.append(str(exc))

    for relative, snippets in _REQUIRED_SNIPPETS.items():
        path = root / relative
        if not path.is_file():
            problems.append(f"缺少发布文件：{relative}")
            continue
        text = path.read_text(encoding="utf-8")
        for snippet in snippets:
            if snippet not in text:
                problems.append(f"{relative}: 缺少动态版本引用 {snippet!r}")

    for relative, patterns in _BANNED_PATTERNS.items():
        path = root / relative
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8")
        for pattern in patterns:
            if pattern.search(text):
                problems.append(
                    f"{relative}: 仍包含硬编码版本（{pattern.pattern}）"
                )
    return problems


def main() -> int:
    problems = version_mismatches()
    if problems:
        print("错误：发布版本配置不一致：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print(f"版本单一来源校验通过：{project_version()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
