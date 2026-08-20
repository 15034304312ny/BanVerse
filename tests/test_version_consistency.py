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
    assert 'Source: "..\\LICENSE"' in installer
    assert 'Source: "..\\NOTICE"' in installer
    assert 'Source: "..\\THIRD_PARTY_NOTICES.md"' in installer


def test_windows_release_signs_executable_installer_and_uninstaller() -> None:
    build = (PROJECT_ROOT / "packaging" / "build_windows.ps1").read_text(
        encoding="utf-8"
    )
    installer = (PROJECT_ROOT / "packaging" / "installer.iss").read_text(
        encoding="utf-8"
    )
    signer = (PROJECT_ROOT / "packaging" / "sign_windows.ps1").read_text(
        encoding="utf-8"
    )

    assert "[switch]$Sign" in build
    assert 'sign_windows.ps1"' in build
    assert '"/DBanVerseSignedBuild=1"' in build
    assert '"/Sbanverse=$InnoSignCommand"' in build
    assert '$InnoQuotePlaceholder = [char]36 + "q"' in build
    assert "-File {0}{2}{0} -Path {3}" in build
    assert "-VerifyOnly" in build

    assert "#ifdef BanVerseSignedBuild" in installer
    assert "SignTool=banverse" in installer
    assert "SignedUninstaller=yes" in installer
    assert "SignToolRetryCount=3" in installer
    assert "Flags: ignoreversion signonce" in installer

    assert "/fd SHA256" in signer
    assert "/td SHA256" in signer
    assert "/tr $Timestamp.AbsoluteUri" in signer
    assert signer.index("/tr $Timestamp.AbsoluteUri") < signer.index("/td SHA256")
    assert "/pa /all /v" in signer
    assert "TimeStamperCertificate" in signer
    assert "Get-AuthenticodeSignature" in signer
    assert "1.3.6.1.5.5.7.3.3" in signer
    assert "HasPrivateKey" in signer
    assert "BANVERSE_WINDOWS_CERT_SUBJECT" in signer
    assert 'ValidateSet("PublicCa", "SelfSigned")' in signer
    assert "AllowedSelfSignedStatuses" in signer
    assert "SignatureStatus]::NotTrusted" in signer
    assert "SignatureStatus]::HashMismatch" not in signer
    assert "PFX" not in signer.upper()
    assert "PASSWORD" not in signer.upper()


def test_release_manifest_requires_release_signed_android_name() -> None:
    verifier = (
        PROJECT_ROOT / "packaging" / "verify_release_artifacts.py"
    ).read_text(encoding="utf-8")

    assert "android16-arm64-v8a-release.apk" in verifier
    assert "android16-arm64-v8a-debug.apk" not in verifier


def test_project_declares_apache_license_and_notices() -> None:
    project = (PROJECT_ROOT / "pyproject.toml").read_text(encoding="utf-8")
    license_text = (PROJECT_ROOT / "LICENSE").read_text(encoding="utf-8")
    notice = (PROJECT_ROOT / "NOTICE").read_text(encoding="utf-8")
    third_party = (PROJECT_ROOT / "THIRD_PARTY_NOTICES.md").read_text(
        encoding="utf-8"
    )

    assert 'license = "Apache-2.0"' in project
    assert "Apache License" in license_text
    assert "Version 2.0, January 2004" in license_text
    assert "Copyright 2026 BanVerse contributors" in notice
    assert "PySide6" in third_party
    assert "LGPL-3.0" in third_party
