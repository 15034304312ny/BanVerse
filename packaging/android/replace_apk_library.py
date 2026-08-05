"""Replace one native APK library, then leave alignment/signing to the caller."""

from __future__ import annotations

import argparse
import shutil
import tempfile
import zipfile
from pathlib import Path


def rewrite_apk(
    apk: Path,
    *,
    replacements: dict[str, Path] | None = None,
    removals: set[str] | None = None,
) -> None:
    apk = apk.resolve()
    replacements = {
        name: path.resolve() for name, path in (replacements or {}).items()
    }
    removals = removals or set()
    if not apk.is_file() or any(not path.is_file() for path in replacements.values()):
        raise FileNotFoundError("APK or replacement library does not exist")
    with tempfile.TemporaryDirectory(prefix="deepseek-apk-") as temporary:
        output = Path(temporary) / apk.name
        found: set[str] = set()
        with zipfile.ZipFile(apk, "r") as source, zipfile.ZipFile(
            output, "w", allowZip64=True
        ) as destination:
            for info in source.infolist():
                if info.filename in removals:
                    found.add(info.filename)
                    continue
                if info.filename in replacements:
                    destination.writestr(
                        info,
                        replacements[info.filename].read_bytes(),
                        compress_type=info.compress_type,
                    )
                    found.add(info.filename)
                else:
                    destination.writestr(info, source.read(info.filename))
        # Replacement targets are mandatory.  Removals are intentionally
        # idempotent because a newer deployment recipe may already omit them.
        expected = set(replacements)
        if missing := expected - found:
            raise RuntimeError(f"APK members not found: {', '.join(sorted(missing))}")
        shutil.copy2(output, apk)


def replace_apk_library(apk: Path, member: str, library: Path) -> None:
    rewrite_apk(apk, replacements={member: library})


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("member")
    parser.add_argument("library", type=Path)
    parser.add_argument("--remove", action="append", default=[])
    arguments = parser.parse_args()
    rewrite_apk(
        arguments.apk,
        replacements={arguments.member: arguments.library},
        removals=set(arguments.remove),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
