"""使用直连、断点续传和 SHA-256 校验下载大型构建依赖。"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import os
import time
import urllib.error
import urllib.request
import zlib
from pathlib import Path

CHUNK_SIZE = 4 * 1024 * 1024


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _crc32(path: Path) -> int:
    checksum = 0
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
            checksum = zlib.crc32(chunk, checksum)
    return checksum & 0xFFFFFFFF


def _opener():
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _download_range(
    url: str,
    output_path: Path,
    start: int,
    end: int,
) -> Path:
    expected_length = end - start + 1
    for attempt in range(1, 9):
        existing = output_path.stat().st_size if output_path.exists() else 0
        if existing == expected_length:
            return output_path
        if existing > expected_length:
            raise RuntimeError(f"分段文件尺寸异常：{output_path}")
        range_start = start + existing
        request = urllib.request.Request(
            url,
            headers={
                "Range": f"bytes={range_start}-{end}",
                "User-Agent": "BanVerse-Android-Builder/1.0",
            },
        )
        try:
            with _opener().open(request, timeout=120) as response:
                if response.status != 206:
                    raise RuntimeError(
                        f"服务器未接受 Range 请求：HTTP {response.status}"
                    )
                with output_path.open("ab") as output:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                    output.flush()
                    os.fsync(output.fileno())
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            urllib.error.URLError,
        ) as exc:
            if attempt == 8:
                raise
            print(
                f"分段 {start}-{end} 中断，重试 {attempt}/8：{exc}",
                flush=True,
            )
            time.sleep(3)
    return output_path


def _finish_download(
    partial: Path,
    destination: Path,
    expected_sha256: str,
) -> None:
    actual = _sha256(partial)
    if actual.lower() != expected_sha256.lower():
        raise RuntimeError(
            f"SHA-256 不匹配：期望 {expected_sha256}，实际 {actual}"
        )
    partial.replace(destination)
    print(f"下载与校验完成：{destination}", flush=True)


def _parallel_remainder(
    url: str,
    partial: Path,
    destination: Path,
    expected_sha256: str,
    total_bytes: int,
    connections: int,
) -> None:
    start = partial.stat().st_size if partial.exists() else 0
    if start > total_bytes:
        raise RuntimeError("断点文件大于远端文件")
    if start == total_bytes:
        _finish_download(partial, destination, expected_sha256)
        return
    remaining = total_bytes - start
    segment_size = (remaining + connections - 1) // connections
    segments: list[tuple[int, int, Path]] = []
    for index in range(connections):
        segment_start = start + index * segment_size
        if segment_start >= total_bytes:
            break
        segment_end = min(segment_start + segment_size - 1, total_bytes - 1)
        segment_path = partial.with_name(
            f"{partial.name}.{segment_start}-{segment_end}"
        )
        segments.append((segment_start, segment_end, segment_path))

    print(
        f"并行下载剩余 {remaining} 字节，共 {len(segments)} 段",
        flush=True,
    )
    with concurrent.futures.ThreadPoolExecutor(
        max_workers=len(segments)
    ) as executor:
        futures = [
            executor.submit(
                _download_range,
                url,
                segment_path,
                segment_start,
                segment_end,
            )
            for segment_start, segment_end, segment_path in segments
        ]
        for future in concurrent.futures.as_completed(futures):
            completed = future.result()
            print(f"分段完成：{completed.name}", flush=True)

    with partial.open("ab") as output:
        for segment_start, segment_end, segment_path in segments:
            expected = segment_end - segment_start + 1
            if segment_path.stat().st_size != expected:
                raise RuntimeError(f"分段尺寸不完整：{segment_path}")
            with segment_path.open("rb") as source:
                for chunk in iter(lambda: source.read(CHUNK_SIZE), b""):
                    output.write(chunk)
        output.flush()
        os.fsync(output.fileno())
    if partial.stat().st_size != total_bytes:
        raise RuntimeError("分段合并后的文件尺寸不正确")
    for _, _, segment_path in segments:
        segment_path.unlink()
    _finish_download(partial, destination, expected_sha256)


def download_slice(
    url: str,
    destination: Path,
    *,
    remote_start: int,
    length: int,
    expected_crc32: int,
    connections: int,
) -> None:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    local_offset = partial.stat().st_size if partial.exists() else 0
    if local_offset > length:
        raise RuntimeError("区间断点文件大于目标区间")
    if local_offset < length:
        remaining = length - local_offset
        segment_size = (remaining + connections - 1) // connections
        segments: list[tuple[int, int, Path]] = []
        first_byte = remote_start + local_offset
        final_byte = remote_start + length - 1
        for index in range(connections):
            segment_start = first_byte + index * segment_size
            if segment_start > final_byte:
                break
            segment_end = min(
                segment_start + segment_size - 1,
                final_byte,
            )
            segment_path = partial.with_name(
                f"{partial.name}.{segment_start}-{segment_end}"
            )
            segments.append((segment_start, segment_end, segment_path))
        print(
            f"并行提取远端区间，共 {len(segments)} 段",
            flush=True,
        )
        with concurrent.futures.ThreadPoolExecutor(
            max_workers=len(segments)
        ) as executor:
            futures = [
                executor.submit(
                    _download_range,
                    url,
                    segment_path,
                    segment_start,
                    segment_end,
                )
                for segment_start, segment_end, segment_path in segments
            ]
            for future in concurrent.futures.as_completed(futures):
                completed = future.result()
                print(f"分段完成：{completed.name}", flush=True)
        with partial.open("ab") as output:
            for segment_start, segment_end, segment_path in segments:
                expected = segment_end - segment_start + 1
                if segment_path.stat().st_size != expected:
                    raise RuntimeError(f"分段尺寸不完整：{segment_path}")
                with segment_path.open("rb") as source:
                    for chunk in iter(
                        lambda: source.read(CHUNK_SIZE),
                        b"",
                    ):
                        output.write(chunk)
            output.flush()
            os.fsync(output.fileno())
        for _, _, segment_path in segments:
            segment_path.unlink()
    if partial.stat().st_size != length:
        raise RuntimeError("提取后的区间尺寸不正确")
    actual_crc32 = _crc32(partial)
    if actual_crc32 != expected_crc32:
        raise RuntimeError(
            f"CRC32 不匹配：期望 {expected_crc32:08x}，"
            f"实际 {actual_crc32:08x}"
        )
    partial.replace(destination)
    print(f"区间提取与 CRC32 校验完成：{destination}", flush=True)


def download(
    url: str,
    destination: Path,
    expected_sha256: str,
    *,
    total_bytes: int = 0,
    connections: int = 1,
) -> None:
    destination = destination.resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_suffix(destination.suffix + ".part")
    if connections > 1:
        if total_bytes <= 0:
            raise ValueError("并行下载必须提供远端文件总大小")
        _parallel_remainder(
            url,
            partial,
            destination,
            expected_sha256,
            total_bytes,
            connections,
        )
        return
    opener = _opener()

    for attempt in range(1, 8):
        offset = partial.stat().st_size if partial.exists() else 0
        headers = {"User-Agent": "BanVerse-Android-Builder/1.0"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            request = urllib.request.Request(url, headers=headers)
            with opener.open(request, timeout=120) as response:
                append = offset > 0 and response.status == 206
                if offset and not append:
                    offset = 0
                total = response.headers.get("Content-Range", "").rpartition(
                    "/"
                )[2]
                if not total.isdigit():
                    total = str(
                        offset
                        + int(response.headers.get("Content-Length", "0"))
                    )
                total_bytes = int(total)
                mode = "ab" if append else "wb"
                downloaded = offset
                last_report = -1
                with partial.open(mode) as output:
                    while True:
                        chunk = response.read(CHUNK_SIZE)
                        if not chunk:
                            break
                        output.write(chunk)
                        downloaded += len(chunk)
                        percent = (
                            int(downloaded * 100 / total_bytes)
                            if total_bytes
                            else 0
                        )
                        if percent // 5 != last_report:
                            last_report = percent // 5
                            print(
                                f"{downloaded}/{total_bytes} ({percent}%)",
                                flush=True,
                            )
                    output.flush()
                    os.fsync(output.fileno())
            _finish_download(partial, destination, expected_sha256)
            return
        except (
            ConnectionError,
            OSError,
            TimeoutError,
            urllib.error.URLError,
        ) as exc:
            if attempt == 7:
                raise
            print(f"下载中断，5 秒后从断点重试：{exc}", flush=True)
            time.sleep(5)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("url")
    parser.add_argument("destination", type=Path)
    parser.add_argument("sha256")
    parser.add_argument("--size", type=int, default=0)
    parser.add_argument("--connections", type=int, default=1)
    parser.add_argument("--range-start", type=int, default=-1)
    parser.add_argument("--crc32", default="")
    arguments = parser.parse_args()
    connections = max(1, min(arguments.connections, 32))
    if arguments.range_start >= 0:
        if arguments.size <= 0 or not arguments.crc32:
            parser.error("区间下载必须提供 --size 和 --crc32")
        download_slice(
            arguments.url,
            arguments.destination,
            remote_start=arguments.range_start,
            length=arguments.size,
            expected_crc32=int(arguments.crc32, 16),
            connections=connections,
        )
    else:
        download(
            arguments.url,
            arguments.destination,
            arguments.sha256,
            total_bytes=arguments.size,
            connections=connections,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
