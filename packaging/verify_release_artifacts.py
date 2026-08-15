"""校验正式产物来自当前干净且已打标签的源码，并记录 SHA-256。"""

from __future__ import annotations

import argparse
import hashlib
import json
import runpy
import subprocess
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _git(*arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=PROJECT_ROOT,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _project_version() -> str:
    version_file = PROJECT_ROOT / "src" / "deepseek_cli" / "_version.py"
    namespace = runpy.run_path(str(version_file))
    return str(namespace["__version__"])


def verify_release(*, require_android: bool = True) -> Path:
    version = _project_version()
    status = _git("status", "--porcelain", "--untracked-files=normal")
    if status:
        raise RuntimeError("源码工作区不干净，拒绝生成正式产物清单。")
    commit = _git("rev-parse", "HEAD")
    commit_timestamp = int(_git("show", "-s", "--format=%ct", "HEAD"))
    tags = set(_git("tag", "--points-at", "HEAD").splitlines())
    expected_tag = f"v{version}"
    if expected_tag not in tags:
        raise RuntimeError(f"当前提交缺少发布标签 {expected_tag}。")

    paths = [
        PROJECT_ROOT / "dist" / f"BanVerse-{version}.exe",
        PROJECT_ROOT / "dist" / f"BanVerse-{version}-Setup.exe",
    ]
    if require_android:
        paths.append(
            PROJECT_ROOT
            / "dist"
            / "android"
            / f"BanVerse-{version}-android16-arm64-v8a-debug.apk"
        )
    missing = [str(path) for path in paths if not path.is_file()]
    if missing:
        raise RuntimeError("缺少发布产物：\n" + "\n".join(missing))

    artifacts = []
    for path in paths:
        stat = path.stat()
        if stat.st_mtime < commit_timestamp:
            raise RuntimeError(
                f"产物早于发布提交，必须重新构建：{path}"
            )
        artifacts.append(
            {
                "path": path.relative_to(PROJECT_ROOT).as_posix(),
                "bytes": stat.st_size,
                "sha256": _sha256(path),
                "modified_at": datetime.fromtimestamp(
                    stat.st_mtime, tz=timezone.utc
                ).isoformat(),
            }
        )
    manifest = {
        "product": "伴界 BanVerse",
        "version": version,
        "git_commit": commit,
        "git_tag": expected_tag,
        "git_commit_timestamp": datetime.fromtimestamp(
            commit_timestamp, tz=timezone.utc
        ).isoformat(),
        "verified_at": datetime.now(timezone.utc).isoformat(),
        "artifacts": artifacts,
    }
    output = (
        PROJECT_ROOT
        / "build"
        / "release"
        / f"BanVerse-{version}-manifest.json"
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return output


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--without-android",
        action="store_true",
        help="只校验 Windows EXE 与安装包",
    )
    arguments = parser.parse_args()
    output = verify_release(require_android=not arguments.without_android)
    print(f"发布产物校验通过：{output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
