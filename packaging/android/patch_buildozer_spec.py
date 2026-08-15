"""补充 Qt Android 部署器未覆盖的应用资源和 SDK 约束。"""

from __future__ import annotations

import argparse
import configparser
import runpy
from pathlib import Path


# 版本唯一权威来源是 ``src/deepseek_cli/_version.py``；正常构建由
# build_android.sh 显式传入，直接调用脚本时才从项目根读取。
def _project_root(path: Path) -> Path:
    """从任意文件向上定位包含 pyproject.toml 的项目根。"""

    for parent in path.resolve().parents:
        if (parent / "pyproject.toml").is_file():
            return parent
    raise SystemExit("未找到 pyproject.toml（项目根）")


def _project_version(root: Path) -> str:
    reader_path = root / "packaging" / "read_project_version.py"
    if not reader_path.is_file():
        raise SystemExit(f"找不到版本读取器：{reader_path}")
    reader = runpy.run_path(str(reader_path))
    return reader["project_version"](root)


REQUIRED_EXTENSIONS = {
    "py",
    "png",
    "jpg",
    "jpeg",
    "webp",
    "json",
    "pem",
    "wav",
    "qml",
    "js",
}

# QtLoader invokes System.load() in this exact order.  Qt libraries that are
# pulled in only as ELF dependencies do not receive JNI_OnLoad, so Qt6Core must
# be loaded explicitly before Android-facing modules such as Multimedia.
QT_LIBRARY_LOAD_ORDER = (
    "Core",
    "Concurrent",
    "Network",
    "Gui",
    "Widgets",
    "Multimedia",
    "TextToSpeech",
)


def _comma_values(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def _order_qt_libraries(extra_args: str) -> str:
    marker = "--qt-libs="
    start = extra_args.find(marker)
    if start < 0:
        return extra_args
    values_start = start + len(marker)
    values_end = extra_args.find(" ", values_start)
    if values_end < 0:
        values_end = len(extra_args)
    libraries = _comma_values(extra_args[values_start:values_end])
    if not libraries:
        return extra_args
    known = [name for name in QT_LIBRARY_LOAD_ORDER if name in libraries]
    unknown = [name for name in libraries if name not in QT_LIBRARY_LOAD_ORDER]
    ordered = ",".join(known + unknown)
    return extra_args[:values_start] + ordered + extra_args[values_end:]


def patch_buildozer_spec(
    path: Path,
    *,
    app_version: str | None = None,
    p4a_source_dir: Path | None = None,
    build_dir: Path | None = None,
    p4a_commit: str | None = None,
) -> None:
    path = path.resolve()
    if not path.is_file():
        raise RuntimeError(f"Qt 未生成 buildozer.spec：{path}")
    config = configparser.RawConfigParser(
        strict=False,
        empty_lines_in_values=False,
    )
    config.optionxform = str
    with path.open("r", encoding="utf-8") as source:
        config.read_file(source)
    if not config.has_section("app"):
        raise RuntimeError("buildozer.spec 缺少 [app] 配置")

    extensions = set(
        _comma_values(config.get("app", "source.include_exts", fallback=""))
    )
    extensions.update(REQUIRED_EXTENSIONS)
    config.set("app", "source.include_exts", ",".join(sorted(extensions)))

    permissions = _comma_values(
        config.get("app", "android.permissions", fallback="")
    )
    normalized = {item.rsplit(".", 1)[-1].upper() for item in permissions}
    if "INTERNET" not in normalized:
        permissions.append("INTERNET")
    config.set("app", "android.permissions", ",".join(permissions))

    config.set("app", "title", "伴界 BanVerse")
    config.set("app", "package.name", "deepseekchat")
    config.set("app", "package.domain", "app.deepseekchat")
    if not app_version:
        project_root = _project_root(path)
        app_version = _project_version(project_root)
    config.set("app", "version", app_version)
    requirements = _comma_values(
        config.get("app", "requirements", fallback="")
    )
    requirements = [
        "python3==3.11.13" if item.lower() == "python3" else item
        for item in requirements
    ]
    if not any(item.lower().startswith("python3") for item in requirements):
        requirements.insert(0, "python3==3.11.13")
    if not any(item.lower().startswith("hostpython3") for item in requirements):
        requirements.insert(1, "hostpython3==3.11.13")
    config.set("app", "requirements", ",".join(requirements))
    extra_args = config.get("app", "p4a.extra_args", fallback="")
    extra_args = extra_args.replace(
        ",plugins_multimedia_ffmpegmediaplugin", ""
    ).replace(
        "plugins_multimedia_ffmpegmediaplugin,", ""
    ).replace(
        "plugins_multimedia_ffmpegmediaplugin", ""
    )
    extra_args = _order_qt_libraries(extra_args)
    if extra_args:
        config.set("app", "p4a.extra_args", extra_args)
    config.set("app", "android.api", "36")
    config.set("app", "android.minapi", "28")
    config.set("app", "android.accept_sdk_license", "True")
    if p4a_source_dir is not None:
        config.set(
            "app",
            "p4a.source_dir",
            str(p4a_source_dir.resolve()),
        )
    if p4a_commit:
        config.set("app", "p4a.commit", p4a_commit)
    if not config.has_section("buildozer"):
        config.add_section("buildozer")
    config.set("buildozer", "warn_on_root", "0")
    if build_dir is not None:
        config.set("buildozer", "build_dir", str(build_dir.resolve()))

    with path.open("w", encoding="utf-8", newline="\n") as destination:
        config.write(destination, space_around_delimiters=True)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("spec", type=Path)
    parser.add_argument("--app-version")
    parser.add_argument("--p4a-source-dir", type=Path)
    parser.add_argument("--build-dir", type=Path)
    parser.add_argument("--p4a-commit")
    arguments = parser.parse_args()
    patch_buildozer_spec(
        arguments.spec,
        app_version=arguments.app_version,
        p4a_source_dir=arguments.p4a_source_dir,
        build_dir=arguments.build_dir,
        p4a_commit=arguments.p4a_commit,
    )
    print(f"buildozer.spec 资源与 SDK 配置已校验：{arguments.spec}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
