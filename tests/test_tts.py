import json
from urllib.parse import parse_qs, urlsplit

import deepseek_cli.desktop.workers as workers_module
from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.index_tts2 import (
    DEFAULT_INDEXTTS2_BASE_URL,
    normalize_index_tts2_base_url,
)
from deepseek_cli.desktop.tts import (
    XFYUN_DEFAULT_TUNING,
    SpeechController,
)
from deepseek_cli.desktop.workers import (
    IndexTts2SynthesisWorker,
    SiliconFlowTtsSynthesisWorker,
    XfyunSuperTtsSynthesisWorker,
)
from deepseek_cli.desktop.xfyun_catalog import (
    available_voice_options,
    serialize_available_voices,
)
from deepseek_cli.tts import (
    TtsProfile,
    detect_emotion,
    extract_speech_segments,
    prepare_speech_text,
    read_tts_profile,
    resolve_effective_profile,
    write_tts_profile,
)


def test_profile_round_trip_preserves_other_extensions():
    card = empty_card("角色")
    card["data"]["extensions"] = {"other": {"kept": True}}
    profile = TtsProfile(
        voice="zh-CN-YunxiNeural",
        rate=10,
        pitch=-3,
        volume=4,
        emotion_preset="gentle",
        auto_emotion=False,
        index_tts2_preset="BanVerse_测试角色_讯飞音色",
    )

    updated = write_tts_profile(card, profile)

    assert updated["data"]["extensions"]["other"] == {"kept": True}
    assert read_tts_profile(updated) == profile


def test_prepare_speech_text_removes_markdown_code_and_urls():
    text = "# 标题\n\n**重点** [文档](https://example.com)\n```python\nprint('x')\n```\n* 项目"

    result = prepare_speech_text(text)

    assert "#" not in result
    assert "**" not in result
    assert "https://" not in result
    assert "print" not in result
    assert "标题" in result and "重点 文档" in result and "项目" in result


def test_speech_segments_skip_actions_and_narration_but_keep_dialogue():
    text = (
        "（她轻声笑了笑，把热茶推过来。）别担心，我在。\n"
        "她走到窗边。\n"
        "“今晚可能会下雨。”她轻声说。\n"
        "（她忽然提高声音。）停下！别再往前了！"
    )

    segments = extract_speech_segments(text)
    spoken = " ".join(segment.text for segment in segments)

    assert "把热茶推过来" not in spoken
    assert "她走到窗边" not in spoken
    assert "她轻声说" not in spoken
    assert "别担心，我在。" in spoken
    assert "今晚可能会下雨。" in spoken
    assert "停下！" in spoken
    assert segments[0].emotion == "tender"
    assert segments[-1].volume_delta > 0


def test_action_aware_prosody_can_be_disabled_per_character():
    segment = extract_speech_segments(
        "（她咬着牙大喊。）你现在就停下！"
    )[0]

    automatic = resolve_effective_profile(
        TtsProfile(auto_emotion=True),
        segment.text,
        emotion_override=segment.emotion,
        rate_delta=segment.rate_delta,
        pitch_delta=segment.pitch_delta,
        volume_delta=segment.volume_delta,
    )
    fixed = resolve_effective_profile(
        TtsProfile(auto_emotion=False),
        segment.text,
        emotion_override=segment.emotion,
        rate_delta=segment.rate_delta,
        pitch_delta=segment.pitch_delta,
        volume_delta=segment.volume_delta,
    )

    assert automatic.emotion == "angry"
    assert automatic.volume != fixed.volume
    assert fixed.emotion == "neutral"


def test_emotion_and_effective_parameters_are_bounded():
    assert detect_emotion("太好了，恭喜你！") == "happy"
    assert detect_emotion("普通说明文字") == "neutral"

    result = resolve_effective_profile(
        TtsProfile(rate=48, pitch=48, volume=48, emotion_preset="cheerful"),
        "太好了，恭喜你！",
    )

    assert result.rate == "+50%"
    assert result.pitch == "+50Hz"
    assert result.volume == "+50%"
    assert result.emotion == "happy"


def test_siliconflow_tts_maps_edge_profiles_and_controls():
    assert (
        SpeechController._mapped_voice("zh-CN-XiaoyiNeural")
        == "diana"
    )
    assert (
        SpeechController._mapped_voice("zh-CN-YunjianNeural")
        == "charles"
    )
    assert SpeechController._speed_from_rate("+25%") == 1.25
    assert SpeechController._gain_from_volume("-20%") == -4.0
    assert "<|endofprompt|>" in SpeechController._siliconflow_input(
        "今天见面吧。", "happy"
    )
    assert SpeechController._normalized_percent("+25%") == 0.25
    assert SpeechController._normalized_percent("-150%") == -1.0
    assert SpeechController._normalized_percent("invalid") == 0.0


def test_speech_controller_switches_between_edge_and_siliconflow(qapp):
    class Settings:
        values = {
            "tts_provider": "siliconflow",
            "siliconflow_tts_model": "FunAudioLLM/CosyVoice2-0.5B",
            "siliconflow_tts_voice": "auto",
        }

        def get(self, key, default=""):
            return self.values.get(key, default)

    class Credentials:
        @staticmethod
        def get_siliconflow_api_key():
            return "sf-secret"

    settings = Settings()
    controller = SpeechController(
        qapp, settings=settings, credentials=Credentials()
    )

    assert controller._provider() == "siliconflow"
    assert controller._siliconflow_api_key() == "sf-secret"
    assert controller._siliconflow_voice(
        "zh-CN-XiaoyiNeural"
    ).endswith(":diana")
    settings.values["tts_provider"] = "edge"
    assert controller._provider() == "edge"

    controller.shutdown()


def test_speech_controller_supports_xfyun_mapping_and_fixed_tuning(qapp):
    class Settings:
        values = {
            "tts_provider": "xfyun",
            "xfyun_tts_app_id": "test-app",
            "xfyun_tts_voice": "auto",
        }

        def get(self, key, default=""):
            return self.values.get(key, default)

    class Credentials:
        @staticmethod
        def get_xfyun_tts_api_password():
            return "xfyun-password"

    controller = SpeechController(
        qapp, settings=Settings(), credentials=Credentials()
    )

    assert controller._provider() == "xfyun"
    assert controller._xfyun_app_id() == "test-app"
    assert controller._xfyun_api_password() == "xfyun-password"
    assert controller._xfyun_voice(
        "zh-CN-XiaoyiNeural"
    ) == "x5_lingxiaoxuan_flow"
    assert controller._xfyun_voice(
        "zh-CN-YunjianNeural"
    ) == "x5_lingfeiyi_flow"
    assert XFYUN_DEFAULT_TUNING == (50, 50, 50)

    segments = extract_speech_segments(
        "（她轻声笑了笑。）第一句。第二句！"
    )
    merged = SpeechController._segments_for_provider("xfyun", segments)
    assert len(merged) == 1
    assert merged[0].text == "第一句。第二句！"
    assert SpeechController._segments_for_provider("edge", segments) == segments

    controller.shutdown()


def test_speech_controller_supports_local_indextts2_presets(qapp):
    class Settings:
        values = {
            "tts_provider": "indextts2",
            "indextts2_base_url": "http://localhost:7861/",
            "indextts2_preset": "global-preset",
        }

        def get(self, key, default=""):
            return self.values.get(key, default)

    controller = SpeechController(qapp, settings=Settings(), credentials=None)

    assert controller._provider() == "indextts2"
    assert controller._index_tts2_base_url() == DEFAULT_INDEXTTS2_BASE_URL
    assert controller._index_tts2_preset(TtsProfile()) == "global-preset"
    assert (
        controller._index_tts2_preset(
            TtsProfile(index_tts2_preset="character-preset")
        )
        == "character-preset"
    )
    segments = extract_speech_segments("第一句。第二句！")
    merged = SpeechController._segments_for_provider("indextts2", segments)
    assert len(merged) == 1
    assert merged[0].text == "第一句。第二句！"

    controller.shutdown()


def test_indextts2_url_only_accepts_loopback_addresses():
    assert (
        normalize_index_tts2_base_url("localhost:7861/")
        == DEFAULT_INDEXTTS2_BASE_URL
    )
    assert normalize_index_tts2_base_url("http://127.0.0.2:9000") == (
        "http://127.0.0.2:9000"
    )
    try:
        normalize_index_tts2_base_url("https://example.com")
    except ValueError as exc:
        assert "本机" in str(exc) or "回环" in str(exc)
    else:
        raise AssertionError("A remote IndexTTS2 URL must be rejected")


def test_xfyun_auto_voice_uses_only_account_verified_catalog(qapp):
    class Settings:
        values = {
            "tts_provider": "xfyun",
            "xfyun_tts_app_id": "test-app",
            "xfyun_tts_voice": "x6_lingxiaoli_pro",
            "xfyun_tts_available_voices": serialize_available_voices(
                (
                    "x5_lingxiaoxuan_flow",
                    "x6_xiaonaigoudidi_mini",
                )
            ),
        }

        def get(self, key, default=""):
            return self.values.get(key, default)

    class Credentials:
        get_xfyun_tts_api_password = staticmethod(lambda: "password")

    controller = SpeechController(
        qapp, settings=Settings(), credentials=Credentials()
    )

    assert controller._xfyun_voice(
        "zh-CN-XiaoyiNeural"
    ) == "x5_lingxiaoxuan_flow"
    assert controller._xfyun_voice(
        "zh-CN-YunjianNeural"
    ) == "x6_xiaonaigoudidi_mini"
    options = available_voice_options(
        ("x5_lingxiaoxuan_flow",), current="x6_lingxiaoli_pro"
    )
    assert any("已验证" in label for label, _value in options)
    assert any("未开通" in label for label, _value in options)

    controller.shutdown()


def test_siliconflow_tts_worker_posts_binary_speech_and_saves_mp3(
    tmp_path, monkeypatch
):
    calls = []
    audio = b"ID3\x04\x00\x00test-audio"

    class Response:
        def __init__(self):
            self._sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            if self._sent:
                return b""
            self._sent = True
            return audio

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(workers_module, "urlopen", fake_urlopen)
    output = tmp_path / "speech.mp3"
    completed = []
    failed = []
    worker = SiliconFlowTtsSynthesisWorker(
        7,
        "sf-secret",
        "请用开心语气说。<|endofprompt|>你好",
        "FunAudioLLM/CosyVoice2-0.5B",
        "FunAudioLLM/CosyVoice2-0.5B:diana",
        1.2,
        2.0,
        str(output),
    )
    worker.completed.connect(lambda request_id, path: completed.append((request_id, path)))
    worker.failed.connect(lambda request_id, code: failed.append((request_id, code)))

    worker.run()

    assert failed == []
    assert completed == [(7, str(output))]
    assert output.read_bytes() == audio
    request, timeout = calls[0]
    payload = json.loads(request.data)
    assert request.full_url.endswith("/v1/audio/speech")
    assert request.get_header("Authorization") == "Bearer sf-secret"
    assert timeout == 240
    assert payload == {
        "model": "FunAudioLLM/CosyVoice2-0.5B",
        "input": "请用开心语气说。<|endofprompt|>你好",
        "voice": "FunAudioLLM/CosyVoice2-0.5B:diana",
        "response_format": "mp3",
        "sample_rate": 44100,
        "stream": False,
        "speed": 1.2,
        "gain": 2.0,
    }


def test_indextts2_worker_posts_preset_and_saves_wav(tmp_path, monkeypatch):
    calls = []
    audio = b"RIFF\x10\x00\x00\x00WAVEfmt test"

    class Response:
        def __init__(self):
            self._sent = False

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            if self._sent:
                return b""
            self._sent = True
            return audio

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(workers_module, "urlopen", fake_urlopen)
    output = tmp_path / "speech.wav"
    completed = []
    failed = []
    worker = IndexTts2SynthesisWorker(
        9,
        DEFAULT_INDEXTTS2_BASE_URL,
        "今天过得怎么样？",
        "BanVerse_林小满_讯飞聆小糖",
        "tender",
        str(output),
    )
    worker.completed.connect(
        lambda request_id, path: completed.append((request_id, path))
    )
    worker.failed.connect(
        lambda request_id, code: failed.append((request_id, code))
    )

    worker.run()

    assert failed == []
    assert completed == [(9, str(output))]
    assert output.read_bytes() == audio
    request, timeout = calls[0]
    assert request.full_url == (
        "http://127.0.0.1:7861/v1/audio/speech"
    )
    assert timeout == 900
    assert json.loads(request.data) == {
        "input": "今天过得怎么样？",
        "preset": "BanVerse_林小满_讯飞聆小糖",
        "emotion": "tender",
        "emotion_weight": 0.65,
    }


def test_xfyun_super_tts_worker_authenticates_and_reorders_audio_frames(
    tmp_path,
):
    calls = []
    sent_payloads = []

    def response(seq, content, status):
        return json.dumps(
            {
                "header": {
                    "code": 0,
                    "message": "success",
                    "status": status,
                },
                "payload": {
                    "audio": {
                        "encoding": "lame",
                        "sample_rate": 24000,
                        "status": status,
                        "seq": seq,
                        "audio": __import__("base64").b64encode(
                            content
                        ).decode("ascii"),
                    }
                },
            }
        )

    class Connection:
        frames = [response(1, b"second", 2), response(0, b"first", 1)]

        def settimeout(self, value):
            assert value == 1

        def send(self, value):
            sent_payloads.append(json.loads(value))

        def recv(self):
            return self.frames.pop(0)

        def close(self):
            return None

    def factory(url, *, header, timeout):
        calls.append((url, header, timeout))
        return Connection()

    output = tmp_path / "xfyun.mp3"
    completed = []
    failed = []
    worker = XfyunSuperTtsSynthesisWorker(
        9,
        "appid",
        "api-password",
        "你好🙂",
        "x6_lingxiaoxuan_pro",
        62,
        54,
        58,
        str(output),
        websocket_factory=factory,
    )
    worker.completed.connect(
        lambda request_id, path: completed.append((request_id, path))
    )
    worker.failed.connect(
        lambda request_id, code: failed.append((request_id, code))
    )

    worker.run()

    assert failed == []
    assert completed == [(9, str(output))]
    assert output.read_bytes() == b"firstsecond"
    url, headers, timeout = calls[0]
    assert url.endswith("/v1/private/mcd9m97e6")
    assert headers == {"x-api-key": "api-password"}
    assert timeout == 30
    payload = sent_payloads[0]
    assert payload["header"] == {"app_id": "appid", "status": 2}
    assert payload["parameter"]["oral"] == {}
    assert payload["parameter"]["tts"]["vcn"] == "x6_lingxiaoxuan_pro"
    assert payload["parameter"]["tts"]["rhy"] == 0
    assert payload["parameter"]["tts"]["audio"] == {
        "encoding": "lame",
        "sample_rate": 24000,
        "channels": 1,
        "bit_depth": 16,
        "frame_size": 0,
    }
    decoded = __import__("base64").b64decode(
        payload["payload"]["text"]["text"]
    ).decode("utf-8")
    assert decoded == "你好"


def test_xfyun_super_tts_worker_builds_official_hmac_auth_url(tmp_path):
    worker = XfyunSuperTtsSynthesisWorker(
        10,
        "appid",
        "",
        "你好",
        "x6_lingxiaoxuan_flow",
        50,
        50,
        50,
        str(tmp_path / "xfyun-hmac.mp3"),
        api_key="official-api-key",
        api_secret="official-api-secret",
        auth_date="Thu, 31 Jul 2026 02:00:00 GMT",
    )

    parsed = urlsplit(worker._signed_url())
    query = parse_qs(parsed.query)
    authorization = __import__("base64").b64decode(
        query["authorization"][0]
    ).decode("utf-8")

    assert parsed.scheme == "wss"
    assert parsed.path == "/v1/private/mcd9m97e6"
    assert query["host"] == ["cbm01.cn-huabei-1.xf-yun.com"]
    assert query["date"] == ["Thu, 31 Jul 2026 02:00:00 GMT"]
    assert 'api_key="official-api-key"' in authorization
    assert 'algorithm="hmac-sha256"' in authorization
    assert 'headers="host date request-line"' in authorization
    assert 'signature="' in authorization


def test_xfyun_x4_payload_disables_oralization_and_strips_controls(tmp_path):
    audio = __import__("base64").b64encode(b"mp3").decode("ascii")
    sent = []

    class Connection:
        frames = [
            json.dumps(
                {
                    "header": {"code": 0, "status": 2},
                    "payload": {
                        "audio": {
                            "status": 2,
                            "seq": 0,
                            "audio": audio,
                        },
                    },
                }
            )
        ]

        def settimeout(self, _value):
            return None

        def send(self, value):
            sent.append(json.loads(value))

        def recv(self):
            return self.frames.pop(0)

        def close(self):
            return None

    worker = XfyunSuperTtsSynthesisWorker(
        11,
        "appid",
        "password",
        "银行[=hang2]今天[p220]",
        "x4_lingxiaoli_oral",
        50,
        50,
        50,
        str(tmp_path / "x4.mp3"),
        websocket_factory=lambda *_args, **_kwargs: Connection(),
    )

    worker.run()

    assert sent[0]["parameter"]["oral"] == {
        "spark_assist": 0,
        "stop_split": 1,
        "remain": 1,
    }
    assert sent[0]["parameter"]["tts"]["rhy"] == 0
    decoded = __import__("base64").b64decode(
        sent[0]["payload"]["text"]["text"]
    ).decode("utf-8")
    assert decoded == "银行今天"
    assert (tmp_path / "x4.mp3").read_bytes() == b"mp3"
