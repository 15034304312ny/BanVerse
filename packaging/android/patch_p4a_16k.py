"""Inject explicit 16 KB linker flags into a managed python-for-android tree."""

from __future__ import annotations

import argparse
from pathlib import Path

LINKER_FLAG = "-Wl,-z,max-page-size=16384"


def patch_python_for_android(root: Path) -> None:
    root = root.resolve()
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
    if not archs.is_file() or not android_mk.is_file():
        raise RuntimeError(f"Invalid python-for-android source tree: {root}")

    archs_text = archs.read_text(encoding="utf-8")
    if LINKER_FLAG not in archs_text:
        expected = "common_ldflags = ['-L{ctx_libs_dir}']"
        replacement = (
            "common_ldflags = [\n"
            "        '-L{ctx_libs_dir}',\n"
            f"        '{LINKER_FLAG}',\n"
            "    ]"
        )
        if expected not in archs_text:
            raise RuntimeError("Unable to locate p4a common_ldflags")
        archs.write_text(
            archs_text.replace(expected, replacement, 1),
            encoding="utf-8",
        )

    makefile_text = android_mk.read_text(encoding="utf-8")
    if LINKER_FLAG not in makefile_text:
        expected = "LOCAL_LDFLAGS += -L$(PYTHON_LINK_ROOT) $(APPLICATION_ADDITIONAL_LDFLAGS)"
        if expected not in makefile_text:
            raise RuntimeError("Unable to locate Qt bootstrap LOCAL_LDFLAGS")
        android_mk.write_text(
            makefile_text.replace(
                expected,
                f"{expected} {LINKER_FLAG}",
                1,
            ),
            encoding="utf-8",
        )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    arguments = parser.parse_args()
    patch_python_for_android(arguments.source)
    print(f"Patched python-for-android for 16 KB pages: {arguments.source.resolve()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
