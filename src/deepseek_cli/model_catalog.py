"""DeepSeek 模型目录与兼容别名。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ModelInfo:
    id: str
    label: str
    supports_reasoning: bool = False


MODEL_CHAT = "deepseek-v4-flash"
MODEL_REASONER = "deepseek-v4-pro"

MODELS = (
    ModelInfo(MODEL_CHAT, "DeepSeek V4 Flash · deepseek-v4-flash"),
    ModelInfo(
        MODEL_REASONER,
        "DeepSeek V4 Pro · deepseek-v4-pro",
        supports_reasoning=True,
    ),
)
MODEL_IDS = frozenset(model.id for model in MODELS)
MODEL_ALIASES = {
    "chat": MODEL_CHAT,
    "deepseek-chat": MODEL_CHAT,
    MODEL_CHAT: MODEL_CHAT,
    "reasoner": MODEL_REASONER,
    "deepseek-reasoner": MODEL_REASONER,
    MODEL_REASONER: MODEL_REASONER,
}


def resolve_model(value: str) -> str | None:
    """严格解析用户可输入的短名称、旧名称或当前模型 ID。"""

    return MODEL_ALIASES.get(value.strip().lower())


def resolve_provider_model(value: str) -> str:
    """按照 DeepSeek Anthropic 兼容规则映射 provider 模型。"""

    normalized = value.strip().lower()
    resolved = MODEL_ALIASES.get(normalized)
    if resolved is not None:
        return resolved
    if normalized.startswith("claude-opus"):
        return MODEL_REASONER
    if normalized.startswith(("claude-sonnet", "claude-haiku")):
        return MODEL_CHAT
    return MODEL_CHAT


def model_label(model_id: str) -> str:
    for model in MODELS:
        if model.id == model_id:
            return model.label
    return model_id


def model_supports_reasoning(model_id: str) -> bool:
    """按目录能力判断，而不是让调用方硬编码某个模型 ID。"""

    resolved = resolve_model(model_id)
    return any(
        model.id == resolved and model.supports_reasoning for model in MODELS
    )


def text_provider_models(
    provider: str,
    configured_model: str = "",
) -> tuple[ModelInfo, ...]:
    """Return UI choices matching the model the selected provider will call."""

    if provider.strip().lower() == "grsai":
        actual_model = configured_model.strip() or "未配置模型"
        return (ModelInfo(MODEL_CHAT, f"GRS AI · {actual_model}"),)
    return MODELS
