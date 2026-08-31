from __future__ import annotations

from deepseek_cli.desktop.model_discovery import ProviderModel
from deepseek_cli.model_catalog import MODEL_CHAT, MODEL_REASONER
from deepseek_cli.text_models import (
    capability_summary,
    safe_sampling_options,
    text_model_capabilities,
)


def test_deepseek_sampling_capabilities_follow_thinking_mode():
    chat = text_model_capabilities("deepseek", MODEL_CHAT)
    reasoner = text_model_capabilities("deepseek", MODEL_REASONER)

    assert chat.sampling_parameters == frozenset({"temperature", "top_p"})
    assert not chat.reasoning
    assert reasoner.reasoning
    assert not reasoner.sampling_parameters


def test_unknown_grsai_model_uses_conservative_provider_baseline():
    capabilities = text_model_capabilities(
        "grsai", MODEL_CHAT, configured_model="custom-chat"
    )

    assert capabilities.text_generation
    assert capabilities.streaming
    assert capabilities.sampling_parameters == frozenset({"temperature"})
    assert capabilities.context_length is None
    assert "上下文长度未声明" in capability_summary(capabilities)


def test_cached_grsai_capabilities_filter_unsupported_sampling_fields():
    model = ProviderModel(
        "grsai",
        "known-chat",
        ("chat", "vision"),
        streaming=True,
        context_length=131_072,
        sampling_parameters=("top_p",),
    )
    capabilities = text_model_capabilities(
        "grsai",
        MODEL_CHAT,
        configured_model="known-chat",
        catalog=(model,),
    )

    assert safe_sampling_options(
        capabilities,
        temperature=1.3,
        top_p=1.4,
        frequency_penalty=0.5,
    ) == {"top_p": 1.0}
    assert capabilities.vision
    assert "131,072" in capability_summary(capabilities)
