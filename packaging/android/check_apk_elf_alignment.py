"""Validate that every ELF LOAD segment in an APK supports 16 KB pages."""

from __future__ import annotations

import argparse
import struct
import zipfile
from pathlib import Path

ELF_MAGIC = b"\x7fELF"
LOAD_SEGMENT = 1
PAGE_SIZE = 16_384


def elf_load_alignments(data: bytes) -> tuple[tuple[int, int, int], ...]:
    if not data.startswith(ELF_MAGIC):
        return ()
    elf_class = data[4]
    byte_order = "<" if data[5] == 1 else ">"
    if elf_class == 2:
        header = struct.unpack_from(f"{byte_order}16sHHIQQQIHHHHHH", data)
        program_offset, entry_size, entry_count = header[5], header[9], header[10]
        layout = f"{byte_order}IIQQQQQQ"
        indexes = (0, 2, 3, 7)
    elif elf_class == 1:
        header = struct.unpack_from(f"{byte_order}16sHHIIIIIHHHHHH", data)
        program_offset, entry_size, entry_count = header[5], header[9], header[10]
        layout = f"{byte_order}IIIIIIII"
        indexes = (0, 1, 2, 7)
    else:
        raise ValueError(f"Unsupported ELF class: {elf_class}")
    segments: list[tuple[int, int, int]] = []
    for index in range(entry_count):
        start = program_offset + index * entry_size
        fields = struct.unpack_from(layout, data, start)
        segment_type, offset, virtual_address, alignment = (
            fields[position] for position in indexes
        )
        if segment_type == LOAD_SEGMENT:
            segments.append((offset, virtual_address, alignment))
    return tuple(segments)


def incompatible_libraries(apk: Path) -> dict[str, tuple[tuple[int, int, int], ...]]:
    incompatible = {}
    with zipfile.ZipFile(apk) as archive:
        for name in archive.namelist():
            if not name.startswith("lib/") or not name.endswith(".so"):
                continue
            segments = elf_load_alignments(archive.read(name))
            failures = tuple(
                segment
                for segment in segments
                if segment[2] < PAGE_SIZE
                or segment[0] % PAGE_SIZE != segment[1] % PAGE_SIZE
            )
            if failures:
                incompatible[name] = failures
    return incompatible


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    arguments = parser.parse_args()
    failures = incompatible_libraries(arguments.apk)
    for name, segments in failures.items():
        rendered = ", ".join(
            f"offset=0x{offset:x}/vaddr=0x{address:x}/align=0x{alignment:x}"
            for offset, address, alignment in segments
        )
        print(f"{name}: {rendered}")
    if failures:
        return 1
    print("All APK ELF libraries are compatible with 16 KB page sizes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
