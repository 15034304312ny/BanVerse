"""为 pyside6-android-deploy 准备一个最小、可复现的源码目录。"""

from __future__ import annotations

import argparse
import importlib.util
import shutil
from pathlib import Path

PYSIDE_VERSION = "6.11.1"
REQUIRED_QT_MODULES = {
    "Core",
    "Gui",
    "Widgets",
    "Network",
    "Multimedia",
    "TextToSpeech",
}
REQUIRED_RESOURCES = (
    "app_icon.png",
    "message_notification.wav",
    "builtin_characters",
    "builtin_avatars",
)


def _copy_source(source: Path, destination: Path) -> None:
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            "*.pyo",
            ".pytest_cache",
        ),
    )


def _websocket_package_dir() -> Path:
    module = importlib.util.find_spec("websocket")
    if module is None or module.origin is None:
        raise RuntimeError(
            "缺少 websocket-client；请先安装 websocket-client==1.9.0"
        )
    package_dir = Path(module.origin).resolve().parent
    if not (package_dir / "__init__.py").is_file():
        raise RuntimeError(f"websocket-client 包目录无效：{package_dir}")
    return package_dir


def _certifi_package_dir() -> Path:
    module = importlib.util.find_spec("certifi")
    if module is None or module.origin is None:
        raise RuntimeError("缺少 certifi；Android HTTPS 需要可信 CA 证书包")
    package_dir = Path(module.origin).resolve().parent
    if not (package_dir / "cacert.pem").is_file():
        raise RuntimeError(f"certifi CA 证书包无效：{package_dir}")
    return package_dir


def _render_spec(
    template: Path,
    destination: Path,
    *,
    project_dir: Path,
    exec_directory: Path,
    python_path: Path,
    pyside_wheel: Path,
    shiboken_wheel: Path,
    sdk_path: str,
    ndk_path: str,
) -> None:
    replacements = {
        "__PROJECT_DIR__": str(project_dir),
        "__EXEC_DIR__": str(exec_directory),
        "__PYTHON_PATH__": str(python_path),
        "__PYSIDE_WHEEL__": str(pyside_wheel),
        "__SHIBOKEN_WHEEL__": str(shiboken_wheel),
        "__SDK_PATH__": sdk_path,
        "__NDK_PATH__": ndk_path,
    }
    rendered = template.read_text(encoding="utf-8")
    for placeholder, value in replacements.items():
        rendered = rendered.replace(placeholder, value)
    unresolved = [
        placeholder
        for placeholder in replacements
        if placeholder in rendered
    ]
    if unresolved:
        raise RuntimeError(
            f"Android 部署配置仍有未替换项：{', '.join(unresolved)}"
        )
    destination.write_text(rendered, encoding="utf-8")


def validate_stage(stage: Path) -> None:
    package = stage / "deepseek_cli"
    resources = package / "desktop" / "resources"
    missing = [
        str(path)
        for path in (
            stage / "main.py",
            stage / "app_icon.png",
            stage / "pysidedeploy.spec",
            package / "__init__.py",
            stage / "websocket" / "__init__.py",
            stage / "certifi" / "__init__.py",
            stage / "certifi" / "cacert.pem",
            *(resources / name for name in REQUIRED_RESOURCES),
        )
        if not path.exists()
    ]
    if missing:
        raise RuntimeError(
            "Android staging 缺少文件：\n- " + "\n- ".join(missing)
        )
    spec = (stage / "pysidedeploy.spec").read_text(encoding="utf-8")
    modules_line = next(
        (
            line.partition("=")[2].strip()
            for line in spec.splitlines()
            if line.strip().startswith("modules =")
        ),
        "",
    )
    modules = {item.strip() for item in modules_line.split(",") if item.strip()}
    missing_modules = REQUIRED_QT_MODULES - modules
    if missing_modules:
        raise RuntimeError(
            "Android 部署配置缺少 Qt 模块："
            + ", ".join(sorted(missing_modules))
        )


def prepare_stage(
    *,
    project_root: Path,
    stage: Path,
    exec_directory: Path,
    python_path: Path,
    pyside_wheel: Path,
    shiboken_wheel: Path,
    sdk_path: str = "",
    ndk_path: str = "",
) -> None:
    project_root = project_root.resolve()
    stage = stage.resolve()
    exec_directory = exec_directory.resolve()
    if not (project_root / "pyproject.toml").is_file():
        raise RuntimeError(f"项目根目录无效：{project_root}")
    if any(stage.iterdir()):
        raise RuntimeError(f"staging 目录必须为空：{stage}")
    for wheel in (pyside_wheel, shiboken_wheel):
        if not wheel.resolve().is_file():
            raise RuntimeError(f"Android wheel 不存在：{wheel}")

    package_source = project_root / "src" / "deepseek_cli"
    android_dir = project_root / "packaging" / "android"
    _copy_source(package_source, stage / "deepseek_cli")
    _copy_source(_websocket_package_dir(), stage / "websocket")
    _copy_source(_certifi_package_dir(), stage / "certifi")
    shutil.copy2(android_dir / "main.py", stage / "main.py")
    shutil.copy2(
        package_source / "desktop" / "resources" / "app_icon.png",
        stage / "app_icon.png",
    )
    exec_directory.mkdir(parents=True, exist_ok=True)
    _render_spec(
        android_dir / "pysidedeploy.spec.template",
        stage / "pysidedeploy.spec",
        project_dir=stage,
        exec_directory=exec_directory,
        python_path=python_path.resolve(),
        pyside_wheel=pyside_wheel.resolve(),
        shiboken_wheel=shiboken_wheel.resolve(),
        sdk_path=sdk_path,
        ndk_path=ndk_path,
    )
    validate_stage(stage)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project-root", type=Path, required=True)
    parser.add_argument("--stage", type=Path, required=True)
    parser.add_argument("--exec-directory", type=Path, required=True)
    parser.add_argument("--python-path", type=Path, required=True)
    parser.add_argument("--pyside-wheel", type=Path, required=True)
    parser.add_argument("--shiboken-wheel", type=Path, required=True)
    parser.add_argument("--sdk-path", default="")
    parser.add_argument("--ndk-path", default="")
    return parser


def main() -> int:
    arguments = _parser().parse_args()
    prepare_stage(
        project_root=arguments.project_root,
        stage=arguments.stage,
        exec_directory=arguments.exec_directory,
        python_path=arguments.python_path,
        pyside_wheel=arguments.pyside_wheel,
        shiboken_wheel=arguments.shiboken_wheel,
        sdk_path=arguments.sdk_path,
        ndk_path=arguments.ndk_path,
    )
    print(f"Android staging 校验通过：{arguments.stage.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
