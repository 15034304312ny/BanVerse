"""Android 应用入口，包含尽可能早的启动崩溃记录。"""

from __future__ import annotations

import faulthandler
import os
import sys
import traceback
from contextlib import suppress
from pathlib import Path


def _configure_tls_ca_bundle() -> str:
    """合并 certifi 与 Android 系统 CA，供 Python/OpenSSL 校验。"""

    certifi_bundle: Path | None = None
    try:
        import certifi

        bundle = Path(certifi.where()).resolve()
        if bundle.is_file():
            certifi_bundle = bundle
    except (ImportError, OSError, RuntimeError):
        pass
    system_directory: Path | None = None
    for candidate in (
        Path("/system/etc/security/cacerts"),
        Path("/apex/com.android.conscrypt/cacerts"),
    ):
        if candidate.is_dir():
            system_directory = candidate
            break
    selected = certifi_bundle
    if certifi_bundle is not None and system_directory is not None:
        private_value = os.environ.get("ANDROID_PRIVATE", "").strip()
        if private_value:
            merged = Path(private_value) / "banverse-trusted-ca.pem"
            partial = merged.with_suffix(".part")
            try:
                chunks = [certifi_bundle.read_bytes().rstrip() + b"\n"]
                for certificate in sorted(system_directory.iterdir()):
                    if not certificate.is_file():
                        continue
                    data = certificate.read_bytes().strip()
                    if b"-----BEGIN CERTIFICATE-----" in data:
                        chunks.append(data + b"\n")
                partial.write_bytes(b"".join(chunks))
                partial.replace(merged)
                selected = merged
            except OSError:
                with suppress(OSError):
                    partial.unlink(missing_ok=True)
    if selected is not None:
        os.environ["SSL_CERT_FILE"] = str(selected)
        try:
            import ssl
            from urllib.request import (
                HTTPSHandler,
                build_opener,
                install_opener,
            )

            context = ssl.create_default_context(cafile=str(selected))
            install_opener(build_opener(HTTPSHandler(context=context)))
        except (OSError, RuntimeError, ValueError):
            pass
    if system_directory is not None:
        os.environ["SSL_CERT_DIR"] = str(system_directory)
    return str(selected) if selected is not None else ""


def _bootstrap_log() -> tuple[Path | None, object | None]:
    root_value = (
        os.environ.get("ANDROID_PRIVATE")
        or os.environ.get("ANDROID_ARGUMENT")
        or os.environ.get("DEEPSEEK_CHAT_LOG_DIR")
    )
    if not root_value:
        return None, None
    try:
        root = Path(root_value)
        root.mkdir(parents=True, exist_ok=True)
        path = root / "bootstrap.log"
        stream = path.open("a", encoding="utf-8", buffering=1)
        faulthandler.enable(stream, all_threads=True)
        return path, stream
    except (OSError, RuntimeError):
        return None, None


_TLS_CA_BUNDLE = _configure_tls_ca_bundle()
_BOOTSTRAP_LOG_PATH, _BOOTSTRAP_STREAM = _bootstrap_log()
if _BOOTSTRAP_STREAM is not None:
    print(
        f"TLS CA bundle: {_TLS_CA_BUNDLE or 'unavailable'}",
        file=_BOOTSTRAP_STREAM,
        flush=True,
    )
    print(
        f"TLS CA directory: {os.environ.get('SSL_CERT_DIR', 'unavailable')}",
        file=_BOOTSTRAP_STREAM,
        flush=True,
    )

try:
    from deepseek_cli.desktop.main import main
except BaseException:
    details = traceback.format_exc()
    print(details, file=sys.stderr, flush=True)
    if _BOOTSTRAP_STREAM is not None:
        print(details, file=_BOOTSTRAP_STREAM, flush=True)
    raise


if __name__ == "__main__":
    raise SystemExit(main())
