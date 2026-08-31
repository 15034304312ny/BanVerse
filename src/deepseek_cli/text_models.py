"""文本模型能力协商与安全采样参数。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .model_catalog import MODEL_CHAT, model_supports_reasoning, resolve_model


@dataclass(frozen=True, slots=True)
class TextModelCapabilities:
    """调用文本模型前可依赖的最小能力描述。"""

    text_generation: bool = True
    vision: bool = False
    reasoning: bool = False
    streaming: bool = True
    context_length: int | None = None
    sampling_parameters: frozenset[str] = frozenset()


def text_model_capabilities(
    provider: str,
    model: str,
    *,
    configured_model: str = "",
    catalog: tuple[Any, ...] = (),
) -> TextModelCapabilities:
    """Resolve capabilities without assuming unknown provider features.

    DeepSeek capabilities follow its documented chat endpoint.  GRS AI is
    OpenAI-compatible for chat and historically accepts ``temperature``;
    optional fields beyond that are enabled only when the cached catalog
    explicitly declares them.
    """

    normalized_provider = provider.strip().lower()
    if normalized_provider != "grsai":
        resolved = resolve_model(model) or MODEL_CHAT
        reasoning = model_supports_reasoning(resolved)
        return TextModelCapabilities(
            reasoning=reasoning,
            # DeepSeek documents temperature/top_p only for non-thinking mode.
            sampling_parameters=(
                frozenset()
                if reasoning
                else frozenset({"temperature", "top_p"})
            ),
        )

    actual_model = configured_model.strip()
    selected = next(
        (
            item
            for item in catalog
            if str(getattr(item, "provider", "")).lower() == "grsai"
            and str(getattr(item, "id", "")) == actual_model
        ),
        None,
    )
    capabilities = tuple(getattr(selected, "capabilities", ()))
    reasoning = "reasoning" in capabilities
    declared_sampling = frozenset(
        str(value)
        for value in getattr(selected, "sampling_parameters", ())
        if str(value).strip()
    )
    if not declared_sampling and not reasoning:
        # Provider-level compatibility already used by existing releases.
        declared_sampling = frozenset({"temperature"})
    return TextModelCapabilities(
        text_generation=selected is None or "chat" in capabilities,
        vision="vision" in capabilities,
        reasoning=reasoning,
        streaming=bool(getattr(selected, "streaming", True)),
        context_length=getattr(selected, "context_length", None),
        sampling_parameters=(
            frozenset() if reasoning else declared_sampling
        ),
    )


def safe_sampling_options(
    capabilities: TextModelCapabilities,
    *,
    temperature: float | None = None,
    top_p: float | None = None,
    frequency_penalty: float | None = None,
    presence_penalty: float | None = None,
    repetition_penalty: float | None = None,
) -> dict[str, float]:
    """Keep only declared parameters and clamp them to portable ranges."""

    requested = {
        "temperature": (temperature, 0.0, 2.0),
        "top_p": (top_p, 0.0, 1.0),
        "frequency_penalty": (frequency_penalty, -2.0, 2.0),
        "presence_penalty": (presence_penalty, -2.0, 2.0),
        "repetition_penalty": (repetition_penalty, 0.0, 2.0),
    }
    result: dict[str, float] = {}
    for name, (value, minimum, maximum) in requested.items():
        if value is None or name not in capabilities.sampling_parameters:
            continue
        result[name] = max(minimum, min(float(value), maximum))
    return result


def capability_summary(capabilities: TextModelCapabilities) -> str:
    """Return a concise, user-facing model capability summary."""

    badges = ["文本", "流式" if capabilities.streaming else "非流式"]
    if capabilities.vision:
        badges.append("视觉")
    if capabilities.reasoning:
        badges.append("推理")
    if capabilities.context_length:
        badges.append(f"上下文 {capabilities.context_length:,}")
    else:
        badges.append("上下文长度未声明")
    sampling = "/".join(sorted(capabilities.sampling_parameters))
    badges.append(f"采样 {sampling}" if sampling else "平台默认采样")
    return " · ".join(badges)
