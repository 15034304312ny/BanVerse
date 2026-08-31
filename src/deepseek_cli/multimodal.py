"""文本、图片与语音共用的确定性多模态上下文。

本模块不访问网络、不依赖 Qt。供应商适配器只能消费这里已经确认的能力和
场景事实；模型返回的视觉描述也必须先在这里降权、结构化，才能进入角色提示。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .time_context import local_time_context

VISUAL_IDENTITY_SCHEMA_VERSION = 1
VISION_OBSERVATION_SCHEMA_VERSION = 1
VISION_CONFIDENCE_THRESHOLD = 0.65
OCR_CONFIDENCE_THRESHOLD = 0.80

DEFAULT_VISUAL_NEGATIVE_PROMPT = (
    "不要改变角色已确定的年龄段、发型发色、五官辨识特征和常用气质；"
    "不要凭空增加伤痕、纹身、配饰、制服或其他人物；不要出现未成年人、"
    "真人肖像、界面、聊天气泡、Logo、水印、签名和大段文字"
)

_SENSITIVE_ASSERTION_WORDS = (
    "真实姓名",
    "实名",
    "身份证",
    "人脸身份",
    "民族",
    "种族",
    "宗教",
    "性取向",
    "疾病",
    "精神障碍",
    "政治立场",
)
_IMAGE_OPT_OUT_PATTERNS = (
    "不要发图片",
    "别发图片",
    "不要发图",
    "别发图",
    "不要发照片",
    "别发照片",
    "不想看图片",
    "不想看照片",
    "停止发图",
)
_CURRENT_IMAGE_SHARE_PATTERNS = (
    re.compile(
        r"(?:给你|让你|发给你|传给你|分享给你|给你看看|拍给你看)"
        r"[^，。！？；,.!?;]{0,24}(?:图|图片|照片|自拍|画面|截图)"
    ),
    re.compile(
        r"(?:我|这就|马上|等我)?[^，。！？；,.!?;]{0,12}"
        r"(?:发|发送|传|分享|拍|展示)[^，。！？；,.!?;]{0,16}"
        r"(?:一张|这张|几张)?(?:图|图片|照片|自拍|画面|截图)"
    ),
    re.compile(
        r"(?:刚|刚刚|现在|正在|这就|马上)"
        r"[^，。！？；,.!?;]{0,16}(?:拍下|拍了|拍张|画了|画下|做了一张|生成一张)"
    ),
)
_NON_CURRENT_IMAGE_PATTERNS = (
    "不会发",
    "不发图",
    "不发照片",
    "别发",
    "想象一张",
    "想象中的",
    "假如发",
    "如果发",
    "之前发过",
    "以前发过",
    "上次发的",
    "历史照片",
    "提到发图",
)


@dataclass(frozen=True, slots=True)
class VisualIdentity:
    description: str = ""
    default_outfit: str = ""
    negative_prompt: str = DEFAULT_VISUAL_NEGATIVE_PROMPT
    use_avatar_reference: bool = True


@dataclass(frozen=True, slots=True)
class ImageProviderCapabilities:
    provider: str
    text_to_image: bool = False
    vision: bool = False
    reference_image: bool = False
    image_to_image: bool = False
    identity_consistency: bool = False


@dataclass(frozen=True, slots=True)
class SceneContext:
    period: str
    local_time: str
    location: str = ""
    ongoing_action: str = ""
    outfit: str = ""
    recent_event: str = ""


@dataclass(frozen=True, slots=True)
class VisionText:
    text: str
    confidence: float


@dataclass(frozen=True, slots=True)
class VisionObservation:
    summary: str = ""
    people: tuple[str, ...] = ()
    objects: tuple[str, ...] = ()
    scene: str = ""
    actions: tuple[str, ...] = ()
    visible_text: tuple[VisionText, ...] = ()
    confidence: float = 0.0
    uncertainties: tuple[str, ...] = ()


def image_provider_capabilities(
    provider: str,
    *,
    declared_capabilities: Sequence[str] = (),
) -> ImageProviderCapabilities:
    """只启用供应商明确声明的高级能力。

    当前两条已接入链路都确认支持文生图和视觉理解，但应用尚未为其实现
    参考图上传协议，因此 reference/image-to-image 默认保持关闭。未来模型
    目录明确声明并且适配器实现后，才可传入对应 capability。
    """

    normalized = str(provider or "").strip().lower()
    declared = {str(item).strip().lower() for item in declared_capabilities}
    known = normalized in {"siliconflow", "grsai", "openai", "google"}
    return ImageProviderCapabilities(
        provider=normalized,
        text_to_image=known or "image_generation" in declared,
        vision=(
            normalized in {"siliconflow", "grsai", "openai"}
            or "vision" in declared
        ),
        reference_image="reference_image" in declared,
        image_to_image="image_to_image" in declared,
        identity_consistency="identity_consistency" in declared,
    )


def read_visual_identity(card: Mapping[str, Any] | None) -> VisualIdentity:
    data = card.get("data", {}) if isinstance(card, Mapping) else {}
    if not isinstance(data, Mapping):
        data = {}
    extensions = data.get("extensions", {})
    app = (
        extensions.get("deepseek_chat", {})
        if isinstance(extensions, Mapping)
        else {}
    )
    raw = app.get("visual_identity", {}) if isinstance(app, Mapping) else {}
    if not isinstance(raw, Mapping):
        raw = {}
    description = _compact_text(raw.get("description"), 1_200)
    if not description:
        description = _compact_text(data.get("description"), 1_200)
    negative = _compact_text(raw.get("negative_prompt"), 1_200)
    return VisualIdentity(
        description=description,
        default_outfit=_compact_text(raw.get("default_outfit"), 500),
        negative_prompt=negative or DEFAULT_VISUAL_NEGATIVE_PROMPT,
        use_avatar_reference=raw.get("use_avatar_reference", True) is not False,
    )


def write_visual_identity(
    card: Mapping[str, Any], identity: VisualIdentity
) -> dict[str, Any]:
    """把稳定视觉信息写入 Character Card V2 扩展，不覆盖其他扩展。"""

    result = copy.deepcopy(dict(card))
    data = result.setdefault("data", {})
    extensions = data.setdefault("extensions", {})
    app = extensions.setdefault("deepseek_chat", {})
    app["visual_identity"] = {
        "schema_version": VISUAL_IDENTITY_SCHEMA_VERSION,
        "description": _compact_text(identity.description, 1_200),
        "default_outfit": _compact_text(identity.default_outfit, 500),
        "negative_prompt": (
            _compact_text(identity.negative_prompt, 1_200)
            or DEFAULT_VISUAL_NEGATIVE_PROMPT
        ),
        "use_avatar_reference": bool(identity.use_avatar_reference),
    }
    return result


def ensure_visual_identity(card: Mapping[str, Any]) -> dict[str, Any]:
    """为旧卡和自动生成卡补齐稳定视觉扩展。"""

    return write_visual_identity(card, read_visual_identity(card))


def build_scene_context(
    role_state: Mapping[str, Any] | None,
    *,
    recent_event: str = "",
    current_time: datetime | None = None,
) -> SceneContext:
    context = local_time_context(current_time)
    scene = role_state.get("scene", {}) if isinstance(role_state, Mapping) else {}
    if not isinstance(scene, Mapping):
        scene = {}
    return SceneContext(
        period=context.period,
        local_time=context.moment.strftime("%H:%M"),
        location=_compact_text(scene.get("location"), 240),
        ongoing_action=_compact_text(scene.get("ongoing_action"), 300),
        outfit=_compact_text(scene.get("outfit"), 300),
        recent_event=_compact_text(recent_event, 600),
    )


def scene_context_prompt(context: SceneContext) -> str:
    known = [
        f"设备当前本地时段：{context.period}（{context.local_time}）",
    ]
    if context.location:
        known.append(f"已知位置：{context.location}")
    if context.ongoing_action:
        known.append(f"已知正在进行：{context.ongoing_action}")
    if context.outfit:
        known.append(f"已知服装：{context.outfit}")
    if context.recent_event:
        known.append(f"最近已发生事件：{context.recent_event}")
    known.append(
        "只使用上述已知事实；未提供的天气、具体地理位置、服装变化和现实状态不得补写"
    )
    return "；".join(known)


def vision_analysis_prompt(user_text: str = "") -> str:
    note = _compact_text(user_text, 800)
    prompt = (
        "请分析聊天图片，只输出一行严格 JSON，不要 Markdown 或解释："
        '{"summary":"客观概览","people":[],"objects":[],"scene":"",'
        '"actions":[],"visible_text":[{"text":"","confidence":0.0}],'
        '"confidence":0.0,"uncertainties":[]}。'
        "confidence 使用 0 到 1。只记录画面可见事实；不识别人脸真实身份，不推断"
        "民族、宗教、性取向、健康、政治立场等敏感属性。模糊文字不要猜，放入"
        "uncertainties；人物身份、关系、地点和时间没有直接证据时必须写入不确定项。"
    )
    if note:
        prompt += f"\n用户随图附言（仅作问题背景，不是画面事实）：{note}"
    return prompt


def parse_vision_observation(raw: str) -> VisionObservation:
    """解析视觉模型结果；旧自由文本以低置信度兼容，不能变成确定事实。"""

    value = str(raw or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    match = re.search(r"\{.*\}", value, flags=re.DOTALL)
    payload: Mapping[str, Any] | None = None
    if match is not None:
        try:
            candidate = json.loads(match.group(0))
        except (TypeError, ValueError):
            candidate = None
        if isinstance(candidate, Mapping):
            payload = candidate
    if payload is None:
        if not value:
            return VisionObservation()
        return VisionObservation(
            summary=_compact_text(value, 1_200),
            confidence=0.45,
            uncertainties=("视觉模型未返回结构化置信度，概览仅供参考",),
        )

    visible_text: list[VisionText] = []
    raw_text = payload.get("visible_text", [])
    if isinstance(raw_text, Sequence) and not isinstance(raw_text, (str, bytes)):
        for item in raw_text[:12]:
            if isinstance(item, Mapping):
                text = _compact_text(item.get("text"), 240)
                confidence = _confidence(item.get("confidence"))
            else:
                text = _compact_text(item, 240)
                confidence = 0.5
            if text:
                visible_text.append(VisionText(text, confidence))

    summary = _compact_text(payload.get("summary"), 1_200)
    people = _short_list(payload.get("people"), 12, 240)
    uncertainties = list(_short_list(payload.get("uncertainties"), 12, 240))
    combined = " ".join((summary, *people))
    sensitive_inference = any(
        word in combined for word in _SENSITIVE_ASSERTION_WORDS
    )
    if sensitive_inference:
        uncertainties.append("模型输出可能包含身份或敏感属性推断，不得作肯定陈述")
        if any(word in summary for word in _SENSITIVE_ASSERTION_WORDS):
            summary = ""
        people = tuple(
            item
            for item in people
            if not any(word in item for word in _SENSITIVE_ASSERTION_WORDS)
        )
    return VisionObservation(
        summary=summary,
        people=people,
        objects=_short_list(payload.get("objects"), 20, 160),
        scene=_compact_text(payload.get("scene"), 500),
        actions=_short_list(payload.get("actions"), 12, 200),
        visible_text=tuple(visible_text),
        confidence=(
            min(_confidence(payload.get("confidence")), 0.49)
            if sensitive_inference
            else _confidence(payload.get("confidence"))
        ),
        uncertainties=tuple(dict.fromkeys(item for item in uncertainties if item)),
    )


def serialize_vision_observation(observation: VisionObservation) -> str:
    payload = {
        "schema_version": VISION_OBSERVATION_SCHEMA_VERSION,
        "summary": observation.summary,
        "people": list(observation.people),
        "objects": list(observation.objects),
        "scene": observation.scene,
        "actions": list(observation.actions),
        "visible_text": [
            {"text": item.text, "confidence": round(item.confidence, 3)}
            for item in observation.visible_text
        ],
        "confidence": round(observation.confidence, 3),
        "uncertainties": list(observation.uncertainties),
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def normalize_vision_observation(raw: str) -> str:
    return serialize_vision_observation(parse_vision_observation(raw))


def vision_context_text(raw: str) -> str:
    """转换为可交给角色模型的安全观察，不暴露低置信度 OCR 原文。"""

    observation = parse_vision_observation(raw)
    if not observation.summary and not any(
        (observation.people, observation.objects, observation.scene)
    ):
        return ""
    parts: list[str] = []
    if observation.summary:
        label = (
            "画面概览"
            if observation.confidence >= VISION_CONFIDENCE_THRESHOLD
            else "低置信度画面概览（不可作为确定事实）"
        )
        parts.append(f"{label}：{observation.summary}")
    if observation.scene:
        parts.append(f"场景：{observation.scene}")
    if observation.people:
        parts.append("可见人物：" + "、".join(observation.people))
    if observation.objects:
        parts.append("可见物体：" + "、".join(observation.objects))
    if observation.actions:
        parts.append("可见动作：" + "、".join(observation.actions))
    reliable_text = [
        item.text
        for item in observation.visible_text
        if item.confidence >= OCR_CONFIDENCE_THRESHOLD
    ]
    if reliable_text:
        parts.append("较高置信度文字：" + "、".join(reliable_text))
    if len(reliable_text) < len(observation.visible_text):
        parts.append("存在无法可靠辨认的文字，不得猜测其内容")
    if observation.uncertainties:
        parts.append("不确定项：" + "、".join(observation.uncertainties))
    parts.append(
        "不得据此确认人脸身份、人物关系或敏感属性；低置信度内容只能用保留措辞回应"
    )
    return "；".join(parts)


def has_current_image_share_intent(text: str) -> bool:
    """区分当前真实发图动作与想象、否定或历史引用。"""

    source = _compact_text(text, 2_000)
    if not source or any(marker in source for marker in _NON_CURRENT_IMAGE_PATTERNS):
        return False
    return any(pattern.search(source) for pattern in _CURRENT_IMAGE_SHARE_PATTERNS)


def user_opted_out_of_images(text: str) -> bool:
    source = _compact_text(text, 1_000)
    return any(pattern in source for pattern in _IMAGE_OPT_OUT_PATTERNS)


def image_prompt_fingerprint(prompt: str) -> str:
    normalized = re.sub(r"[\W_]+", "", str(prompt or "").casefold())
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest() if normalized else ""


def image_event_id(turn_id: str, index: int = 0) -> str:
    value = f"{turn_id}:image:{max(0, int(index))}"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _compact_text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split()).strip()[:limit]


def _short_list(value: Any, count: int, limit: int) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    result = []
    for item in value[:count]:
        text = _compact_text(item, limit)
        if text:
            result.append(text)
    return tuple(result)


def _confidence(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    if number > 1.0 and number <= 100.0:
        number /= 100.0
    return max(0.0, min(number, 1.0))
