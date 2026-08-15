"""确保正式构建来自干净、已标记版本标签的 Git 提交。"""

from __future__ import annotations

import runpy
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _git(root: Path, *arguments: str) -> str:
    result = subprocess.run(
        ["git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    return result.stdout.strip()


def release_source_problems(root: Path | None = None) -> list[str]:
    root = Path(root or PROJECT_ROOT)
    reader = runpy.run_path(
        str(root / "packaging" / "read_project_version.py")
    )
    version = reader["project_version"](root)
    problems = []
    status = _git(root, "status", "--porcelain", "--untracked-files=normal")
    if status:
        problems.append("源码工作区包含未提交修改")
    expected_tag = f"v{version}"
    tags = set(_git(root, "tag", "--points-at", "HEAD").splitlines())
    if expected_tag not in tags:
        problems.append(f"当前提交缺少发布标签 {expected_tag}")
    return problems


def main() -> int:
    problems = release_source_problems()
    if problems:
        print("错误：当前源码不能用于正式构建：")
        for problem in problems:
            print(f"  - {problem}")
        return 1
    print("发布源码校验通过：工作区干净且版本标签指向当前提交。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
