"""IndexTTS2 本地服务的配置、预设与启动工具。"""

from __future__ import annotations

import ipaddress
import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


DEFAULT_INDEXTTS2_BASE_URL = "http://127.0.0.1:7861"
DEFAULT_INDEXTTS2_PRESET = "BanVerse_林小满_讯飞聆小糖"
INDEXTTS2_BUILTIN_PRESETS = (
    "BanVerse_谢昭宁_讯飞古风侠女",
    "BanVerse_白荼_讯飞聆小玥",
    "BanVerse_阮星遥_讯飞聆小璇",
    "BanVerse_洛弥莎_讯飞午夜电台",
    "BanVerse_周既明_讯飞贴心男友",
    DEFAULT_INDEXTTS2_PRESET,
)


def normalize_index_tts2_base_url(value: str) -> str:
    """只允许本机 HTTP 回环地址，避免将角色台词发往外部服务。"""

    candidate = str(value or "").strip() or DEFAULT_INDEXTTS2_BASE_URL
    if "://" not in candidate:
        candidate = f"http://{candidate}"
    parsed = urlsplit(candidate)
    if parsed.scheme.lower() != "http" or not parsed.hostname:
        raise ValueError("IndexTTS2 服务必须使用本机 HTTP 地址。")
    hostname = parsed.hostname.lower().rstrip(".")
    is_loopback = hostname == "localhost"
    if not is_loopback:
        try:
            is_loopback = ipaddress.ip_address(hostname).is_loopback
        except ValueError:
            is_loopback = False
    if not is_loopback:
        raise ValueError("IndexTTS2 服务地址仅允许 localhost 或回环 IP。")
    try:
        port = parsed.port or 7861
    except ValueError as exc:
        raise ValueError("IndexTTS2 服务端口无效。") from exc
    if not 1 <= port <= 65535:
        raise ValueError("IndexTTS2 服务端口无效。")
    host = "127.0.0.1" if hostname == "localhost" else hostname
    if ":" in host:
        host = f"[{host}]"
    path = parsed.path.rstrip("/")
    if path:
        raise ValueError("IndexTTS2 服务地址不应包含路径。")
    return urlunsplit(("http", f"{host}:{port}", "", "", ""))


def index_tts2_endpoint(base_url: str, path: str) -> str:
    return f"{normalize_index_tts2_base_url(base_url)}/{path.lstrip('/')}"


def serialize_index_tts2_presets(values) -> str:
    presets = [
        value
        for value in dict.fromkeys(str(item).strip() for item in values)
        if value
    ]
    return json.dumps(presets, ensure_ascii=False, separators=(",", ":"))


def deserialize_index_tts2_presets(value: str) -> tuple[str, ...]:
    try:
        payload = json.loads(value or "[]")
    except (TypeError, ValueError):
        return ()
    if not isinstance(payload, list):
        return ()
    return tuple(
        item
        for item in dict.fromkeys(
            str(item).strip()[:240] for item in payload
        )
        if item
    )


def discover_index_tts2_root(configured: str = "") -> Path | None:
    candidates: list[Path] = []
    if configured.strip():
        candidates.append(Path(configured.strip()).expanduser())
    environment = os.environ.get("BANVERSE_INDEXTTS2_ROOT", "").strip()
    if environment:
        candidates.append(Path(environment).expanduser())
    current = Path(__file__).resolve()
    candidates.extend(parent / "IndexTTS2" for parent in current.parents)
    candidates.extend((Path.cwd() / "IndexTTS2", Path.cwd().parent / "IndexTTS2"))
    for candidate in candidates:
        resolved = candidate.resolve()
        if (
            resolved.is_dir()
            and resolved.joinpath("banverse_api.py").is_file()
            and resolved.joinpath("checkpoints", "config.yaml").is_file()
        ):
            return resolved
    return None


def launch_index_tts2_service(
    root: str | Path, base_url: str, *, fp16: bool = True
) -> tuple[bool, str]:
    health_url = index_tts2_endpoint(base_url, "health")
    try:
        with urlopen(
            Request(health_url, headers={"Accept": "application/json"}),
            timeout=1,
        ) as response:
            if response.status == 200:
                return True, f"IndexTTS2 本地服务已在运行：{health_url}"
    except (OSError, URLError):
        pass
    project = Path(root).expanduser().resolve()
    script = project / "banverse_api.py"
    python = project / ".venv" / "Scripts" / "python.exe"
    if not script.is_file():
        return False, f"未找到本地服务脚本：{script}"
    if not python.is_file():
        return False, f"未找到 IndexTTS2 Python 环境：{python}"
    parsed = urlsplit(normalize_index_tts2_base_url(base_url))
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 7861
    output_dir = project / "outputs" / "api"
    output_dir.mkdir(parents=True, exist_ok=True)
    command = [
        str(python),
        str(script),
        "--host",
        host,
        "--port",
        str(port),
        "--model-dir",
        str(project / "checkpoints"),
    ]
    if fp16:
        command.append("--fp16")
    creationflags = 0
    kwargs = {"start_new_session": True}
    if sys.platform == "win32":
        creationflags = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
        kwargs = {}
    log_path = output_dir / "banverse_api.log"
    try:
        with log_path.open("a", encoding="utf-8") as log:
            subprocess.Popen(
                command,
                cwd=str(project),
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                creationflags=creationflags,
                **kwargs,
            )
    except OSError as exc:
        return False, f"IndexTTS2 服务启动失败：{exc}"
    return True, f"服务已启动，模型加载日志：{log_path}"
