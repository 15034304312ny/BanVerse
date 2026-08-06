# -*- mode: python ; coding: utf-8 -*-

from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules, copy_metadata

project_root = Path(SPECPATH).parent
source_root = project_root / "src"
launcher = project_root / "packaging" / "deepseek_app_launcher.py"
app_icon = project_root / "packaging" / "app_icon.ico"
app_resources = source_root / "deepseek_cli" / "desktop" / "resources"
app_datas = [
    (str(app_resources), "deepseek_cli/desktop/resources"),
]

analysis = Analysis(
    [str(launcher)],
    pathex=[str(source_root)],
    binaries=[],
    datas=copy_metadata("keyring") + copy_metadata("edge-tts") + app_datas,
    hiddenimports=[
        "edge_tts",
        "websocket",
        "aiohttp",
        "certifi",
        "PySide6.QtMultimedia",
        "PySide6.QtTextToSpeech",
        "keyring.backends.Windows",
        "win32ctypes",
        "win32ctypes.pywin32",
        *collect_submodules("edge_tts"),
        *collect_submodules("win32ctypes"),
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["openai"],
    noarchive=False,
)
pyz = PYZ(analysis.pure)
exe = EXE(
    pyz,
    analysis.scripts,
    analysis.binaries,
    analysis.datas,
    [],
    name="BanVerse-1.0.0",
    icon=str(app_icon),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 压缩会显著增大 Windows Defender/EDR 误报概率并拖慢 onefile 首启
    # （需解压到临时目录）；关闭它以换取稳定的首次启动体验。
    upx=False,
    console=False,
    disable_windowed_traceback=False,
)
