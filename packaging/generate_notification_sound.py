"""生成项目内置的轻快双音消息提示音。"""

from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

SAMPLE_RATE = 44_100
DURATION_SECONDS = 0.46


def _bell(t: float, start: float, frequencies: tuple[float, ...]) -> float:
    local = t - start
    if local < 0:
        return 0.0
    attack = min(1.0, local / 0.008)
    decay = math.exp(-7.2 * local)
    fundamental = sum(
        math.sin(2 * math.pi * frequency * local)
        for frequency in frequencies
    ) / len(frequencies)
    sparkle = sum(
        0.16 * math.sin(2 * math.pi * frequency * 2.01 * local)
        for frequency in frequencies
    ) / len(frequencies)
    return attack * decay * (fundamental + sparkle)


def generate(output: Path) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    frames = bytearray()
    total = round(SAMPLE_RATE * DURATION_SECONDS)
    for index in range(total):
        t = index / SAMPLE_RATE
        sample = (
            0.52 * _bell(t, 0.0, (1046.50, 1318.51, 1567.98))
            + 0.34 * _bell(t, 0.105, (1318.51, 1760.00, 2093.00))
        )
        final_fade = min(1.0, (DURATION_SECONDS - t) / 0.055)
        sample = max(-0.88, min(0.88, sample * final_fade))
        frames.extend(struct.pack("<h", round(sample * 32767)))

    with wave.open(str(output), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE)
        stream.writeframes(frames)


if __name__ == "__main__":
    generate(
        Path(__file__).resolve().parents[1]
        / "src"
        / "deepseek_cli"
        / "desktop"
        / "resources"
        / "message_notification.wav"
    )
