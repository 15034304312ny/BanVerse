from __future__ import annotations

import json

import pytest

from deepseek_cli.desktop.model_discovery import (
    ModelDiscoveryError,
    ProviderModelCatalog,
    deserialize_models,
    models_for_capability,
    serialize_models,
)


class FakeResponse:
    def __init__(self, payload: bytes):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def read(self, _limit=-1):
        return self.payload


def test_discovers_grsai_models_and_multimodal_capabilities():
    records = [
        {
            "name": "gemini-3.1-pro",
            "feature": "对话、识图、推理",
            "desc": "多模态模型",
        },
        {
            "name": "gpt-image-2",
            "feature": "文生图、图生图、1K",
            "desc": "绘图模型",
        },
    ]
    escaped = json.dumps(records, ensure_ascii=False).replace('"', '\\"')
    source = f'<script>self.__next_f.push([1,"7:{{\\"models\\":{escaped}}}"])</script>'

    catalog = ProviderModelCatalog(
        opener=lambda _request, timeout: FakeResponse(source.encode())
    )
    models = catalog.fetch("grsai")

    assert [model.id for model in models] == [
        "gemini-3.1-pro",
        "gpt-image-2",
    ]
    assert models[0].supports("chat")
    assert models[0].supports("vision")
    assert models[0].supports("reasoning")
    assert models[1].supports("image_generation")
    assert "多模态" in models[0].label


def test_grsai_english_catalog_falls_back_to_model_and_document_hints():
    chat = ProviderModelCatalog._grsai_model(
        {
            "name": "gemini-3.1-pro",
            "feature": "",
            "document": "https://grsai.ai/dashboard/documents/chat",
        }
    )
    image = ProviderModelCatalog._grsai_model(
        {
            "name": "gpt-image-2",
            "feature": "",
            "document": "https://grsai.ai/dashboard/documents/gpt-image",
        }
    )

    assert chat.supports("chat")
    assert chat.supports("vision")
    assert image.supports("image_generation")


def test_discovers_and_filters_siliconflow_model_types():
    payloads = {
        "type=text": [
            "Qwen/Qwen3-VL-8B-Instruct",
            "deepseek-ai/DeepSeek-V4-Flash",
            "Qwen/Qwen3-VL-Embedding-8B",
        ],
        "type=image": ["Tongyi-MAI/Z-Image-Turbo"],
        "type=audio": [
            "FunAudioLLM/CosyVoice2-0.5B",
            "TeleAI/TeleSpeechASR",
        ],
    }

    def opener(request, *, timeout):
        key = next(key for key in payloads if key in request.full_url)
        data = {
            "object": "list",
            "data": [{"id": model} for model in payloads[key]],
        }
        return FakeResponse(json.dumps(data).encode())

    models = ProviderModelCatalog(opener=opener).fetch(
        "siliconflow", api_key="key"
    )

    assert [model.id for model in models_for_capability(models, "vision")] == [
        "Qwen/Qwen3-VL-8B-Instruct"
    ]
    assert [
        model.id for model in models_for_capability(models, "image_generation")
    ] == ["Tongyi-MAI/Z-Image-Turbo"]
    assert [model.id for model in models_for_capability(models, "tts")] == [
        "FunAudioLLM/CosyVoice2-0.5B"
    ]
    assert not next(
        model
        for model in models
        if model.id == "Qwen/Qwen3-VL-Embedding-8B"
    ).supports("chat")


def test_model_catalog_cache_round_trip_and_missing_key_error():
    models = ProviderModelCatalog(
        opener=lambda _request, timeout: FakeResponse(
            b'{"object":"list","data":[]}'
        )
    )
    with pytest.raises(ModelDiscoveryError, match="API Key"):
        models.fetch("siliconflow")

    source = (
        '<script>self.__next_f.push([1,"7:{\\"models\\":'
        '[{\\"name\\":\\"gemini-test\\",'
        '\\"feature\\":\\"对话\\"}]}"])</script>'
    )
    discovered = ProviderModelCatalog(
        opener=lambda _request, timeout: FakeResponse(source.encode())
    ).fetch("grsai")
    assert deserialize_models(serialize_models(discovered)) == discovered
