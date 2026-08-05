from __future__ import annotations

import base64
import json
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage

from deepseek_cli.desktop.assets import (
    AvatarError,
    import_chat_image,
    install_generated_image,
)
from deepseek_cli.desktop.image_service import (
    GoogleImageService,
    GrsAiImageService,
    ImageServiceError,
    OpenAIImageService,
    SiliconFlowImageService,
    image_context,
)
import deepseek_cli.desktop.image_service as image_service_module
from deepseek_cli.desktop.workers import ChatWorker


def image_bytes(tmp_path, *, width=640, height=360) -> bytes:
    path = tmp_path / f"source-{width}x{height}.png"
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.magenta)
    assert image.save(str(path), "PNG")
    return path.read_bytes()


def test_chat_image_is_validated_scaled_and_saved_in_appdata(tmp_path, qapp):
    source = tmp_path / "large.png"
    image = QImage(2400, 1200, QImage.Format.Format_RGB32)
    image.fill(Qt.GlobalColor.green)
    assert image.save(str(source), "PNG")

    installed = import_chat_image(
        source, app_data_root=tmp_path / "appdata"
    )
    loaded = QImage(installed)

    assert loaded.width() == 2048
    assert loaded.height() == 1024
    assert tmp_path / "appdata" in Path(installed).parents


def test_invalid_chat_and_generated_images_are_rejected(tmp_path, qapp):
    invalid = tmp_path / "invalid.png"
    invalid.write_bytes(b"not an image")

    with pytest.raises(AvatarError):
        import_chat_image(invalid, app_data_root=tmp_path)
    with pytest.raises(AvatarError):
        install_generated_image(b"not an image", app_data_root=tmp_path)


def test_openai_service_sends_base64_vision_input_and_decodes_generation(
    tmp_path,
):
    source = tmp_path / "photo.png"
    source.write_bytes(image_bytes(tmp_path))

    class Responses:
        def __init__(self):
            self.kwargs = None

        def create(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(output_text="桌上有一束黄色鲜花。")

    class Images:
        def __init__(self, payload):
            self.payload = payload
            self.kwargs = None

        def generate(self, **kwargs):
            self.kwargs = kwargs
            return SimpleNamespace(
                data=[
                    SimpleNamespace(
                        b64_json=base64.b64encode(self.payload).decode("ascii")
                    )
                ]
            )

    responses = Responses()
    generated = image_bytes(tmp_path, width=512, height=512)
    images = Images(generated)
    client = SimpleNamespace(responses=responses, images=images)
    service = OpenAIImageService(
        "",
        client=client,
        vision_model="vision-test",
        image_model="image-test",
        image_quality="high",
        image_size="1536x1024",
    )

    description = service.describe_image(str(source), "这是哪里？")
    output = service.generate_image("一只在窗边睡觉的猫")

    assert description == "桌上有一束黄色鲜花。"
    vision = responses.kwargs["input"][0]["content"]
    assert responses.kwargs["model"] == "vision-test"
    assert vision[1]["image_url"].startswith("data:image/png;base64,")
    assert "这是哪里" in vision[0]["text"]
    assert output == generated
    assert images.kwargs == {
        "model": "image-test",
        "prompt": "一只在窗边睡觉的猫",
        "size": "1536x1024",
        "quality": "high",
    }


def test_imagegen_rejects_unknown_size_and_quality():
    service = OpenAIImageService(
        "",
        client=SimpleNamespace(),
        image_quality="ultra",
        image_size="999x999",
    )

    assert service.image_quality == "medium"
    assert service.image_size == "1024x1024"


def test_image_context_keeps_visible_text_and_marks_analysis_boundary():
    text = image_context("你觉得怎么样？", "一只橘猫趴在纸箱里。")

    assert text.startswith("你觉得怎么样？")
    assert "图片理解服务" in text
    assert "橘猫" in text
    assert "不要声称" in text


def test_openai_service_standard_http_path_uses_official_endpoints(
    tmp_path, monkeypatch
):
    source = tmp_path / "http-photo.png"
    source.write_bytes(image_bytes(tmp_path))
    generated = image_bytes(tmp_path, width=256, height=256)
    replies = [
        {
            "output": [
                {
                    "type": "message",
                    "content": [
                        {"type": "output_text", "text": "窗边有一盆绿植。"}
                    ],
                }
            ]
        },
        {
            "data": [
                {
                    "b64_json": base64.b64encode(generated).decode("ascii")
                }
            ]
        },
    ]
    calls = []

    class Response:
        def __init__(self, payload):
            self.payload = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.payload

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response(replies.pop(0))

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    service = OpenAIImageService(
        "http-secret",
        vision_model="vision-http",
        image_model="image-http",
    )

    assert service.describe_image(str(source)) == "窗边有一盆绿植。"
    assert service.generate_image("绿色植物插画") == generated
    assert calls[0][0].full_url.endswith("/v1/responses")
    assert calls[1][0].full_url.endswith("/v1/images/generations")
    assert calls[0][0].get_header("Authorization") == "Bearer http-secret"
    assert json.loads(calls[1][0].data)["model"] == "image-http"


def test_google_service_uses_official_interactions_api_and_decodes_image(
    tmp_path, monkeypatch
):
    generated = image_bytes(tmp_path, width=320, height=240)
    calls = []
    result = {
        "steps": [
            {
                "type": "model_output",
                "content": [
                    {
                        "type": "image",
                        "mime_type": "image/png",
                        "data": base64.b64encode(generated).decode("ascii"),
                    }
                ],
            }
        ]
    }

    class Response:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return json.dumps(result).encode("utf-8")

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response()

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    service = GoogleImageService(
        "google-secret",
        image_model="gemini-image-test",
        aspect_ratio="16:9",
        image_size="2K",
    )

    assert service.generate_image("明亮的海边早餐") == generated
    request, timeout = calls[0]
    payload = json.loads(request.data)
    assert request.full_url.endswith("/v1beta/interactions")
    assert request.get_header("X-goog-api-key") == "google-secret"
    assert timeout == 240
    assert payload == {
        "model": "gemini-image-test",
        "input": "明亮的海边早餐",
        "response_format": {
            "type": "image",
            "mime_type": "image/png",
            "aspect_ratio": "16:9",
            "image_size": "2K",
        },
    }


def test_google_service_rejects_unknown_aspect_ratio_and_size():
    service = GoogleImageService(
        "google-secret",
        aspect_ratio="7:5",
        image_size="8K",
    )

    assert service.aspect_ratio == "1:1"
    assert service.image_size == "1K"


def test_siliconflow_service_understands_and_immediately_downloads_image(
    tmp_path, monkeypatch
):
    source = tmp_path / "siliconflow-photo.png"
    source.write_bytes(image_bytes(tmp_path))
    generated = image_bytes(tmp_path, width=512, height=512)
    replies = [
        json.dumps(
            {
                "choices": [
                    {
                        "message": {
                            "content": "窗边有一只正在晒太阳的橘猫。"
                        }
                    }
                ]
            }
        ).encode("utf-8"),
        json.dumps(
            {"images": [{"url": "https://example.com/generated.png"}]}
        ).encode("utf-8"),
        generated,
    ]
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response(replies.pop(0))

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    service = SiliconFlowImageService(
        "sf-secret",
        vision_model="vision-test",
        image_model="image-test",
        image_size="1280x720",
    )

    assert (
        service.describe_image(str(source), "它在做什么？")
        == "窗边有一只正在晒太阳的橘猫。"
    )
    assert service.generate_image("现代都市夜景") == generated

    vision_request, vision_timeout = calls[0]
    generation_request, generation_timeout = calls[1]
    download_request, download_timeout = calls[2]
    vision_payload = json.loads(vision_request.data)
    generation_payload = json.loads(generation_request.data)
    assert vision_request.full_url.endswith("/v1/chat/completions")
    assert generation_request.full_url.endswith("/v1/images/generations")
    assert download_request.full_url == "https://example.com/generated.png"
    assert vision_request.get_header("Authorization") == "Bearer sf-secret"
    assert generation_request.get_header("Authorization") == "Bearer sf-secret"
    assert download_request.get_header("Authorization") is None
    assert (vision_timeout, generation_timeout, download_timeout) == (
        240,
        240,
        180,
    )
    content = vision_payload["messages"][0]["content"]
    assert content[0]["image_url"]["url"].startswith(
        "data:image/png;base64,"
    )
    assert "它在做什么" in content[1]["text"]
    assert generation_payload == {
        "model": "image-test",
        "prompt": "现代都市夜景",
        "image_size": "1280x720",
    }


def test_siliconflow_service_defaults_invalid_size():
    service = SiliconFlowImageService(
        "sf-secret", image_size="999x999"
    )

    assert service.image_size == "1024x1024"


def test_grsai_service_uses_chat_vision_and_image_generations(
    tmp_path, monkeypatch
):
    source = tmp_path / "grsai-photo.png"
    source.write_bytes(image_bytes(tmp_path))
    generated = image_bytes(tmp_path, width=512, height=512)
    replies = [
        json.dumps(
            {
                "choices": [
                    {"message": {"content": "桌上放着一杯热咖啡。"}}
                ]
            }
        ).encode("utf-8"),
        json.dumps(
            {"code": 0, "msg": "success", "data": {"id": "task-gpt"}}
        ).encode("utf-8"),
        json.dumps(
            {
                "code": 0,
                "msg": "success",
                "data": {
                    "id": "task-gpt",
                    "progress": 100,
                    "status": "succeeded",
                    "results": [
                        {"url": "https://example.com/grs-generated.png"}
                    ],
                },
            }
        ).encode("utf-8"),
        generated,
    ]
    calls = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response(replies.pop(0))

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    service = GrsAiImageService(
        "grs-secret",
        base_url="https://grsaiapi.com/v1/",
        vision_model="gemini-vision-test",
        image_model="gpt-image-2",
        image_size="1536x1024",
    )

    assert service.describe_image(str(source), "这是哪里？") == (
        "桌上放着一杯热咖啡。"
    )
    assert service.generate_image("都市咖啡馆随手拍") == generated

    vision_request = calls[0][0]
    generation_request = calls[1][0]
    result_request = calls[2][0]
    download_request = calls[3][0]
    assert vision_request.full_url == (
        "https://grsaiapi.com/v1/chat/completions"
    )
    assert generation_request.full_url == (
        "https://grsaiapi.com/v1/draw/completions"
    )
    assert result_request.full_url == (
        "https://grsaiapi.com/v1/draw/result"
    )
    assert download_request.full_url == (
        "https://example.com/grs-generated.png"
    )
    vision_payload = json.loads(vision_request.data)
    generation_payload = json.loads(generation_request.data)
    assert vision_payload["model"] == "gemini-vision-test"
    assert vision_payload["stream"] is False
    assert vision_payload["messages"][0]["content"][1][
        "image_url"
    ]["url"].startswith("data:image/png;base64,")
    assert generation_payload == {
        "model": "gpt-image-2",
        "prompt": "都市咖啡馆随手拍",
        "aspectRatio": "1536x1024",
        "quality": "auto",
        "webHook": "-1",
    }
    assert json.loads(result_request.data) == {"id": "task-gpt"}


def test_grsai_nano_banana_uses_async_draw_api_and_size_mapping(
    tmp_path, monkeypatch
):
    generated = image_bytes(tmp_path, width=640, height=360)
    replies = [
        json.dumps(
            {"code": 0, "msg": "success", "data": {"id": "task-nano"}}
        ).encode("utf-8"),
        json.dumps(
            {
                "code": 0,
                "data": {
                    "id": "task-nano",
                    "progress": 42,
                    "status": "running",
                    "results": [],
                },
            }
        ).encode("utf-8"),
        json.dumps(
            {
                "code": 0,
                "data": {
                    "id": "task-nano",
                    "progress": 100,
                    "status": "succeeded",
                    "results": [
                        {"url": "https://example.com/nano-result.png"}
                    ],
                },
            }
        ).encode("utf-8"),
        generated,
    ]
    calls = []
    sleeps = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response(replies.pop(0))

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        image_service_module.time, "sleep", lambda seconds: sleeps.append(seconds)
    )
    service = GrsAiImageService(
        "grs-secret",
        base_url="https://grsai.dakka.com.cn/v1/draw/nano-banana",
        image_model="nano-banana-2-2k-cl",
        image_size="2048x1152",
    )

    assert service.generate_image("明亮的都市天台夜景") == generated
    assert calls[0][0].full_url == (
        "https://grsai.dakka.com.cn/v1/draw/nano-banana"
    )
    assert json.loads(calls[0][0].data) == {
        "model": "nano-banana-2-2k-cl",
        "prompt": "明亮的都市天台夜景",
        "aspectRatio": "16:9",
        "imageSize": "2K",
        "webHook": "-1",
    }
    assert [json.loads(calls[index][0].data) for index in (1, 2)] == [
        {"id": "task-nano"},
        {"id": "task-nano"},
    ]
    assert calls[3][0].full_url == "https://example.com/nano-result.png"
    assert sleeps == [image_service_module.GRSAI_POLL_INTERVAL_SECONDS]
    assert [timeout for _, timeout in calls[:3]] == [240, 30, 30]


@pytest.mark.parametrize(
    ("model", "configured_size", "expected_size"),
    [
        ("nano-banana-2-cl", "2048x2048", "1K"),
        ("nano-banana-2-2k-cl", "1024x1024", "2K"),
        ("nano-banana-2-4k-cl", "1024x1024", "4K"),
        ("nano-banana-pro-4k-vip", "1536x1024", "4K"),
    ],
)
def test_grsai_nano_banana_honors_model_specific_resolution(
    model, configured_size, expected_size
):
    service = GrsAiImageService(
        "grs-secret",
        image_model=model,
        image_size=configured_size,
    )

    endpoint, payload = service._generation_request("测试画面")

    assert endpoint == "/draw/nano-banana"
    assert payload["imageSize"] == expected_size


def test_grsai_draw_failure_reports_provider_reason(monkeypatch):
    replies = [
        {"code": 0, "data": {"id": "failed-task"}},
        {
            "code": 0,
            "data": {
                "id": "failed-task",
                "status": "failed",
                "failure_reason": "input_moderation",
                "error": "Prompt rejected",
            },
        },
    ]

    class Response:
        def __init__(self, payload):
            self.body = json.dumps(payload).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    monkeypatch.setattr(
        image_service_module,
        "urlopen",
        lambda _request, _timeout=None, **_kwargs: Response(replies.pop(0)),
    )
    service = GrsAiImageService(
        "grs-secret", image_model="nano-banana-fast"
    )

    with pytest.raises(ImageServiceError, match="Prompt rejected") as captured:
        service.generate_image("违规测试")

    assert captured.value.error_code == "input_moderation"


def test_grsai_fast_falls_back_after_one_upstream_timeout(
    monkeypatch, tmp_path
):
    generated = image_bytes(tmp_path, width=512, height=512)
    replies = [
        json.dumps(
            {"code": 0, "data": {"id": "first-task"}}
        ).encode("utf-8"),
        json.dumps(
            {
                "code": 0,
                "data": {
                    "id": "first-task",
                    "status": "failed",
                    "failure_reason": "error",
                    "error": "google gemini timeout...",
                },
            }
        ).encode("utf-8"),
        json.dumps(
            {"code": 0, "data": {"id": "second-task"}}
        ).encode("utf-8"),
        json.dumps(
            {
                "code": 0,
                "data": {
                    "id": "second-task",
                    "status": "succeeded",
                    "results": [
                        {"url": "https://example.com/retry-success.png"}
                    ],
                },
            }
        ).encode("utf-8"),
        generated,
    ]
    calls = []
    sleeps = []

    class Response:
        def __init__(self, body):
            self.body = body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self, _limit):
            return self.body

    def fake_urlopen(request, timeout):
        calls.append((request, timeout))
        return Response(replies.pop(0))

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    monkeypatch.setattr(
        image_service_module.time,
        "sleep",
        lambda seconds: sleeps.append(seconds),
    )
    service = GrsAiImageService(
        "grs-secret", image_model="nano-banana-fast"
    )

    assert service.generate_image("重试测试") == generated
    submit_urls = [
        request.full_url
        for request, _ in calls
        if request.full_url.endswith("/draw/nano-banana")
    ]
    assert submit_urls == [
        "https://grsai.dakka.com.cn/v1/draw/nano-banana",
        "https://grsai.dakka.com.cn/v1/draw/nano-banana",
    ]
    submit_models = [
        json.loads(request.data)["model"]
        for request, _ in calls
        if request.full_url.endswith("/draw/nano-banana")
    ]
    assert submit_models == ["nano-banana-fast", "nano-banana-2"]
    assert sleeps == [image_service_module.GRSAI_POLL_INTERVAL_SECONDS]


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            ImageServiceError(
                "You exceeded your current quota",
                status_code=429,
                error_code="insufficient_quota",
            ),
            "image_quota",
        ),
        (
            ImageServiceError(
                "The model does not exist",
                status_code=404,
                error_code="model_not_found",
            ),
            "image_model_unavailable",
        ),
        (
            ImageServiceError("Unauthorized", status_code=401),
            "image_authentication",
        ),
        (
            ImageServiceError("Too many requests", status_code=429),
            "image_rate_limit",
        ),
        (
            ImageServiceError(
                "Free tier is unavailable; please enable billing",
                status_code=400,
                error_code="FAILED_PRECONDITION",
            ),
            "image_quota",
        ),
    ],
)
def test_image_api_errors_are_actionable(error, expected):
    assert ChatWorker._error_code(error) == expected


def test_openai_http_error_preserves_quota_code(monkeypatch):
    error_payload = json.dumps(
        {
            "error": {
                "code": "insufficient_quota",
                "message": "You exceeded your current quota.",
            }
        }
    ).encode("utf-8")

    def fake_urlopen(request, timeout):
        raise HTTPError(
            request.full_url,
            429,
            "Too Many Requests",
            {},
            BytesIO(error_payload),
        )

    monkeypatch.setattr(image_service_module, "urlopen", fake_urlopen)
    service = OpenAIImageService("quota-test")

    with pytest.raises(ImageServiceError) as captured:
        service.generate_image("一张测试图片")

    assert captured.value.status_code == 429
    assert captured.value.error_code == "insufficient_quota"
    assert ChatWorker._error_code(captured.value) == "image_quota"
