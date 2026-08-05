from __future__ import annotations

import sys
import wave
from array import array
from importlib import resources


def test_bundled_notification_sound_is_short_bright_pcm_wav():
    path = resources.files("deepseek_cli.desktop").joinpath(
        "resources", "message_notification.wav"
    )

    with wave.open(str(path), "rb") as stream:
        assert stream.getnchannels() == 1
        assert stream.getsampwidth() == 2
        assert stream.getframerate() == 44_100
        duration = stream.getnframes() / stream.getframerate()
        frames = stream.readframes(stream.getnframes())

    samples = array("h")
    samples.frombytes(frames)
    if sys.byteorder != "little":
        samples.byteswap()

    assert 0.3 <= duration <= 0.6
    assert max(abs(sample) for sample in samples) > 8_000
    assert frames[-200:] != b"\0" * 200
