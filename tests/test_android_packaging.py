from __future__ import annotations

import importlib.util
import struct
import sys
import zipfile
from pathlib import Path

from deepseek_cli._version import __version__ as PROJECT_VERSION

PROJECT_ROOT = Path(__file__).resolve().parents[1]
ANDROID_DIR = PROJECT_ROOT / "packaging" / "android"
P4A_COMMIT = "0382d27de2f7315ed98e74884bafb30365decdee"


def _load_prepare_module():
    spec = importlib.util.spec_from_file_location(
        "prepare_android_stage",
        ANDROID_DIR / "prepare_android_stage.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_android_module(name: str):
    spec = importlib.util.spec_from_file_location(name, ANDROID_DIR / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_android_stage_contains_runtime_resources_and_dependencies(tmp_path):
    module = _load_prepare_module()
    stage = tmp_path / "stage"
    output = tmp_path / "output"
    wheels = tmp_path / "wheels"
    stage.mkdir()
    wheels.mkdir()
    pyside_wheel = (
        wheels
        / "pyside6-6.11.1-6.11.1-cp311-cp311-android_aarch64.whl"
    )
    shiboken_wheel = (
        wheels
        / "shiboken6-6.11.1-6.11.1-cp311-cp311-android_aarch64.whl"
    )
    pyside_wheel.touch()
    shiboken_wheel.touch()

    module.prepare_stage(
        project_root=PROJECT_ROOT,
        stage=stage,
        exec_directory=output,
        python_path=Path(sys.executable),
        pyside_wheel=pyside_wheel,
        shiboken_wheel=shiboken_wheel,
    )

    module.validate_stage(stage)
    assert (stage / "deepseek_cli" / "desktop" / "main.py").is_file()
    assert (stage / "certifi" / "cacert.pem").is_file()
    assert (
        stage
        / "deepseek_cli"
        / "desktop"
        / "resources"
        / "builtin_characters"
        / "lin_xiaoman.json"
    ).is_file()
    assert (
        stage
        / "deepseek_cli"
        / "desktop"
        / "resources"
        / "builtin_avatars"
        / "lin_xiaoman.png"
    ).is_file()
    deploy_spec = (stage / "pysidedeploy.spec").read_text(
        encoding="utf-8"
    )
    assert "__PROJECT_DIR__" not in deploy_spec
    assert "arch = aarch64" in deploy_spec
    assert "TextToSpeech" in deploy_spec
    android_main = (stage / "main.py").read_text(encoding="utf-8")
    assert 'os.environ["SSL_CERT_FILE"]' in android_main
    assert 'os.environ["SSL_CERT_DIR"]' in android_main
    assert "/system/etc/security/cacerts" in android_main
    assert "banverse-trusted-ca.pem" in android_main
    assert 'partial.write_bytes(b"".join(chunks))' in android_main
    assert "HTTPSHandler(context=context)" in android_main
    assert "install_opener" in android_main


def test_android_build_script_uses_official_qt_wheels_and_deployer():
    script = (ANDROID_DIR / "build_android.sh").read_text(encoding="utf-8")

    assert "download.qt.io/official_releases/QtForPython" in script
    assert "cp311-cp311-android_aarch64.whl" in script
    assert "--system-site-packages" in script
    assert "ANDROID_BUILD_PROXY_HOST" in script
    assert "ANDROID_GRADLE_MIRROR" in script
    assert "pyside6-android-deploy" in script
    assert 'CERTIFI_VERSION="2026.7.22"' in script
    assert '--ndk-path "${NDK_PATH}"' in script
    assert '--sdk-path "${SDK_PATH}"' in script
    assert "--init" in script
    assert "patch_buildozer_spec.py" in script
    assert "read_project_version.py" in script
    assert 'APP_VERSION="$("${PYTHON_BIN}"' in script
    assert f'APP_VERSION="{PROJECT_VERSION}"' not in script
    assert "BanVerse-${APP_VERSION}-android16-arm64-v8a-debug.apk" in script
    assert "deepseekchat-${APP_VERSION}-arm64-v8a-debug.apk" in script
    assert "reset --hard" not in script
    assert "find \"${EXEC_DIR}\" \"${STAGE_DIR}\"" not in script
    assert "max-page-size=16384" in script
    assert 'NDK_VERSION="28.2.13676358"' in script
    assert "Pkg.Revision = ${NDK_VERSION}" in script
    assert "build_shiboken_16k.sh" in script
    assert "check_apk_elf_alignment.py" in script
    assert "patch_p4a_16k.py" in script
    mirror = (ANDROID_DIR / "gradle_mirror.init.gradle").read_text(
        encoding="utf-8"
    )
    assert "maven.aliyun.com/repository/google" in mirror
    assert "maven.aliyun.com/repository/central" in mirror


def test_generated_buildozer_spec_includes_app_resources_and_sdk(tmp_path):
    module_spec = importlib.util.spec_from_file_location(
        "patch_buildozer_spec",
        ANDROID_DIR / "patch_buildozer_spec.py",
    )
    assert module_spec is not None and module_spec.loader is not None
    module = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(module)
    spec = tmp_path / "buildozer.spec"
    spec.write_text(
        (
            "[app]\n"
            "source.include_exts = py,png,jpg,qml,js\n"
            "android.permissions = android.permission.INTERNET\n"
            "requirements = python3,shiboken6,PySide6\n"
            "p4a.extra_args = --qt-libs=TextToSpeech,Widgets,Network,Concurrent,Multimedia,Core,Gui "
            "--load-local-libs=plugins_multimedia_ffmpegmediaplugin,plugins_multimedia_androidmediaplugin\n"
        ),
        encoding="utf-8",
    )
    p4a_source = tmp_path / "python-for-android"
    p4a_source.mkdir()
    build_dir = tmp_path / "android-build"

    module.patch_buildozer_spec(
        spec,
        app_version=PROJECT_VERSION,
        p4a_source_dir=p4a_source,
        build_dir=build_dir,
        p4a_commit=P4A_COMMIT,
    )

    rendered = spec.read_text(encoding="utf-8")
    assert "json" in rendered
    assert "pem" in rendered
    assert "wav" in rendered
    assert "android.api = 36" in rendered
    assert "android.minapi = 28" in rendered
    assert f"version = {PROJECT_VERSION}" in rendered
    assert "requirements = python3==3.11.13,hostpython3==3.11.13" in rendered
    assert "plugins_multimedia_ffmpegmediaplugin" not in rendered
    assert "plugins_multimedia_androidmediaplugin" in rendered
    assert (
        "--qt-libs=Core,Concurrent,Network,Gui,Widgets,Multimedia,TextToSpeech"
        in rendered
    )
    assert "package.name = deepseekchat" in rendered
    assert "title = 伴界 BanVerse" in rendered
    assert f"p4a.source_dir = {p4a_source.resolve()}" in rendered
    assert f"p4a.commit = {P4A_COMMIT}" in rendered
    assert f"build_dir = {build_dir.resolve()}" in rendered
    assert "warn_on_root = 0" in rendered


def _elf64_with_alignment(alignment: int) -> bytes:
    identity = b"\x7fELF" + bytes((2, 1, 1)) + bytes(9)
    header = struct.pack(
        "<16sHHIQQQIHHHHHH",
        identity,
        3,
        183,
        1,
        0,
        64,
        0,
        0,
        64,
        56,
        1,
        0,
        0,
        0,
    )
    program = struct.pack(
        "<IIQQQQQQ",
        1,
        5,
        0,
        0,
        0,
        64,
        64,
        alignment,
    )
    return header + program


def test_apk_elf_alignment_checker_rejects_4k_library(tmp_path):
    module = _load_android_module("check_apk_elf_alignment")
    apk = tmp_path / "test.apk"
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/good.so", _elf64_with_alignment(0x4000))
        archive.writestr("lib/arm64-v8a/bad.so", _elf64_with_alignment(0x1000))
        archive.writestr("lib/arm64-v8a/bundle.so", b"not an elf")

    failures = module.incompatible_libraries(apk)

    assert tuple(failures) == ("lib/arm64-v8a/bad.so",)


def test_replace_apk_library_preserves_other_members(tmp_path):
    module = _load_android_module("replace_apk_library")
    apk = tmp_path / "test.apk"
    library = tmp_path / "replacement.so"
    library.write_bytes(b"new-library")
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libshiboken6.abi3.so", b"old")
        archive.writestr("assets/private.tar", b"payload")

    module.replace_apk_library(
        apk,
        "lib/arm64-v8a/libshiboken6.abi3.so",
        library,
    )

    with zipfile.ZipFile(apk) as archive:
        assert archive.read("lib/arm64-v8a/libshiboken6.abi3.so") == b"new-library"
        assert archive.read("assets/private.tar") == b"payload"


def test_rewrite_apk_can_remove_ffmpeg_members(tmp_path):
    module = _load_android_module("replace_apk_library")
    apk = tmp_path / "test.apk"
    library = tmp_path / "replacement.so"
    library.write_bytes(b"new-library")
    with zipfile.ZipFile(apk, "w") as archive:
        archive.writestr("lib/arm64-v8a/libshiboken6.abi3.so", b"old")
        archive.writestr("lib/arm64-v8a/libavformat.so", b"ffmpeg")

    module.rewrite_apk(
        apk,
        replacements={"lib/arm64-v8a/libshiboken6.abi3.so": library},
        removals={"lib/arm64-v8a/libavformat.so"},
    )

    with zipfile.ZipFile(apk) as archive:
        assert "lib/arm64-v8a/libavformat.so" not in archive.namelist()

    module.rewrite_apk(
        apk,
        replacements={"lib/arm64-v8a/libshiboken6.abi3.so": library},
        removals={"lib/arm64-v8a/libavformat.so"},
    )


def test_patch_p4a_adds_16k_flags_idempotently(tmp_path):
    module = _load_android_module("patch_p4a_16k")
    root = tmp_path / "python-for-android"
    archs = root / "pythonforandroid" / "archs.py"
    android_mk = (
        root
        / "pythonforandroid"
        / "bootstraps"
        / "qt"
        / "build"
        / "jni"
        / "application"
        / "src"
        / "Android.mk"
    )
    archs.parent.mkdir(parents=True)
    android_mk.parent.mkdir(parents=True)
    archs.write_text(
        "class Arch:\n    common_ldflags = ['-L{ctx_libs_dir}']\n",
        encoding="utf-8",
    )
    android_mk.write_text(
        "LOCAL_LDFLAGS += -L$(PYTHON_LINK_ROOT) $(APPLICATION_ADDITIONAL_LDFLAGS)\n",
        encoding="utf-8",
    )

    module.patch_python_for_android(root)
    module.patch_python_for_android(root)

    assert archs.read_text(encoding="utf-8").count(module.LINKER_FLAG) == 1
    assert android_mk.read_text(encoding="utf-8").count(module.LINKER_FLAG) == 1
