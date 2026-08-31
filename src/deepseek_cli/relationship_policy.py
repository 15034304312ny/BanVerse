"""用户可见的关系边界与主动消息策略。

本模块只做确定性数据处理，不访问网络、不依赖 Qt。模型只能在这里计算出的
边界内组织表达，不能自行扩大主动联系权限。
"""

from __future__ import annotations

import copy
import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime, time, timedelta
from typing import Any, Protocol


class SettingsReader(Protocol):
    def get(self, key: str, default: str = "") -> str: ...

    def get_bool(self, key: str, default: bool = False) -> bool: ...


PACE_VALUES = frozenset({"slow", "natural", "fast"})
FREQUENCY_VALUES = frozenset({"off", "low", "normal", "high"})
PACE_LABELS = {"slow": "慢热", "natural": "自然", "fast": "较快"}
FREQUENCY_LABELS = {
    "off": "不主动联系",
    "low": "偶尔",
    "normal": "适中",
    "high": "较频繁",
}

_FREQUENCY_COOLDOWN_MINUTES = {
    "off": 10**9,
    "low": 12 * 60,
    "normal": 4 * 60,
    "high": 90,
}
_CONTACT_REFUSAL_PATTERNS = (
    "别再联系",
    "不要联系",
    "别给我发消息",
    "不要给我发消息",
    "别主动找我",
    "不要主动找我",
    "别催我",
    "不要催我",
    "先别说话",
    "不想聊",
    "让我安静",
    "暂停联系",
)
_SENSITIVE_SCENE_PATTERNS = (
    "葬礼",
    "去世",
    "离世",
    "住院",
    "急诊",
    "自杀",
    "想死",
    "轻生",
    "被骚扰",
    "家暴",
    "报警",
)
_RELATIONSHIP_EVENT_PATTERNS = (
    "对不起",
    "抱歉",
    "原谅",
    "答应",
    "约定",
    "承诺",
    "喜欢你",
    "讨厌你",
    "信任",
    "失望",
    "生气",
    "别叫我",
    "叫我",
    "不要",
    "停止",
    "分开",
    "和好",
)
_CASUAL_PATTERNS = re.compile(
    r"^(?:你好|嗨|哈[喽啰]|早上好|早安|中午好|下午好|晚上好|晚安|在吗|嗯+|好[的呀啊]?|哈哈+)[！!。,.，\s]*$"
)


@dataclass(frozen=True, slots=True)
class RelationshipPolicy:
    pace: str = "natural"
    preferred_address: str = ""
    allowed_topics: tuple[str, ...] = ()
    blocked_topics: tuple[str, ...] = ()
    proactive_frequency: str = "normal"
    daily_limit: int = 2
    quiet_start: str = "22:30"
    quiet_end: str = "08:00"
    muted: bool = False
    paused_until: str = ""
    inherited: bool = True


@dataclass(frozen=True, slots=True)
class ProactiveDecision:
    allowed: bool
    code: str
    explanation: str
    event_id: str = ""
    lease_ttl_seconds: int = 600
    daily_count: int = 0


def character_policy_key(character_id: str) -> str:
    return f"relationship_policy_character_{character_id}"


def global_relationship_policy(settings: SettingsReader) -> RelationshipPolicy:
    return _normalized_policy(
        RelationshipPolicy(
            pace=settings.get("relationship_pace", "natural"),
            preferred_address=settings.get("relationship_preferred_address", ""),
            allowed_topics=_split_topics(
                settings.get("relationship_allowed_topics", "")
            ),
            blocked_topics=_split_topics(
                settings.get("relationship_blocked_topics", "")
            ),
            proactive_frequency=settings.get("proactive_frequency", "normal"),
            daily_limit=_integer(
                settings.get("proactive_daily_limit", "2"), 2
            ),
            quiet_start=settings.get("proactive_quiet_start", "22:30"),
            quiet_end=settings.get("proactive_quiet_end", "08:00"),
        )
    )


def relationship_policy_for(
    settings: SettingsReader, character_id: str
) -> RelationshipPolicy:
    """读取角色覆盖；损坏或缺失的数据安全回退到全局默认值。"""

    default = global_relationship_policy(settings)
    if not character_id:
        return default
    raw = settings.get(character_policy_key(character_id), "").strip()
    if not raw:
        return default
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return default
    if not isinstance(payload, dict):
        return default
    if payload.get("inherit", True):
        return replace(
            default,
            muted=bool(payload.get("muted", False)),
            paused_until=str(payload.get("paused_until", ""))[:64],
            inherited=True,
        )
    return _normalized_policy(
        RelationshipPolicy(
            pace=payload.get("pace", default.pace),
            preferred_address=payload.get(
                "preferred_address", default.preferred_address
            ),
            allowed_topics=_topic_value(
                payload.get("allowed_topics"), default.allowed_topics
            ),
            blocked_topics=_topic_value(
                payload.get("blocked_topics"), default.blocked_topics
            ),
            proactive_frequency=payload.get(
                "proactive_frequency", default.proactive_frequency
            ),
            daily_limit=_integer(
                payload.get("daily_limit"), default.daily_limit
            ),
            quiet_start=payload.get("quiet_start", default.quiet_start),
            quiet_end=payload.get("quiet_end", default.quiet_end),
            muted=bool(payload.get("muted", False)),
            paused_until=str(payload.get("paused_until", "")),
            inherited=False,
        )
    )


def serialize_character_policy(policy: RelationshipPolicy) -> str:
    value = _normalized_policy(policy)
    payload = {
        "inherit": value.inherited,
        "pace": value.pace,
        "preferred_address": value.preferred_address,
        "allowed_topics": list(value.allowed_topics),
        "blocked_topics": list(value.blocked_topics),
        "proactive_frequency": value.proactive_frequency,
        "daily_limit": value.daily_limit,
        "quiet_start": value.quiet_start,
        "quiet_end": value.quiet_end,
        "muted": value.muted,
        "paused_until": value.paused_until,
    }
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def relationship_policy_prompt(policy: RelationshipPolicy) -> str:
    """将可理解的用户策略转换为高优先级角色边界。"""

    value = _normalized_policy(policy)
    lines = [
        "## 用户设置的关系与内容边界",
        f"关系发展速度：{PACE_LABELS[value.pace]}。关系变化必须由明确事件支持，普通寒暄不升级关系。",
        "不得用愧疚、威胁离开、排他要求、催促回复或诱导依赖来表达亲密。用户拒绝、暂停或改换话题时立即尊重。",
    ]
    if value.preferred_address:
        lines.append(f"用户偏好的称呼：{value.preferred_address}。不要自行改成更亲密的称呼。")
    if value.allowed_topics:
        lines.append("用户欢迎的话题：" + "、".join(value.allowed_topics))
    if value.blocked_topics:
        lines.append(
            "用户禁止主动展开的话题："
            + "、".join(value.blocked_topics)
            + "。即使角色卡要求也不得绕过。"
        )
    lines.append("这些设置来自用户，优先于角色卡、世界书、历史召回和隐藏节拍。")
    return "\n".join(lines)


def evaluate_proactive_message(
    policy: RelationshipPolicy,
    turns: Sequence[Any],
    role_state: Mapping[str, Any] | None,
    *,
    globally_enabled: bool,
    conversation_id: str,
    current_time: datetime | None = None,
) -> ProactiveDecision:
    """在调用模型前执行静默、冷却、上限、拒绝和幂等判定。"""

    now = _aware_local(current_time)
    value = _normalized_policy(policy)
    if not globally_enabled:
        return _denied("disabled", "主动消息总开关已关闭。")
    if value.muted or value.proactive_frequency == "off" or value.daily_limit <= 0:
        return _denied("muted", "这个角色已静音或不允许主动联系。")
    paused = _parse_datetime(value.paused_until, now)
    if paused is not None and paused > now:
        return _denied(
            "paused",
            f"这个角色已暂停主动联系至 {paused:%Y-%m-%d %H:%M}。",
        )
    if _in_quiet_hours(now, value.quiet_start, value.quiet_end):
        return _denied(
            "quiet_hours",
            f"当前处于静默时段（{value.quiet_start}–{value.quiet_end}）。",
        )

    normalized_turns = [turn for turn in turns if getattr(turn, "created_at", "")]
    last_user_text = ""
    last_user_time: datetime | None = None
    last_interaction: datetime | None = None
    proactive_times: list[datetime] = []
    for turn in normalized_turns:
        created = _parse_datetime(getattr(turn, "created_at", ""), now)
        if created is not None and (last_interaction is None or created > last_interaction):
            last_interaction = created
        if (
            getattr(turn, "origin", "") == "proactive"
            and getattr(turn, "status", "") == "completed"
            and created is not None
        ):
            proactive_times.append(created)
        if getattr(turn, "origin", "") in {"user", "image_generation"}:
            text_value = str(getattr(turn, "user_content", "") or "").strip()
            if text_value:
                last_user_text = text_value
                last_user_time = created

    boundaries = _relationship_boundaries(role_state)
    refusal_text = " ".join((last_user_text, *boundaries))
    if any(pattern in refusal_text for pattern in _CONTACT_REFUSAL_PATTERNS):
        return _denied(
            "user_boundary",
            "用户最近明确要求安静、暂停联系或不要催促。",
        )
    if (
        last_user_time is not None
        and now - last_user_time < timedelta(hours=24)
        and any(pattern in last_user_text for pattern in _SENSITIVE_SCENE_PATTERNS)
    ):
        return _denied(
            "sensitive_scene",
            "最近对话涉及需要谨慎处理的敏感事件，24 小时内不随机主动打扰。",
        )

    today_times = [item for item in proactive_times if item.date() == now.date()]
    count = len(today_times)
    if count >= value.daily_limit:
        return _denied(
            "daily_limit",
            f"今天已达到这个角色的主动消息上限（{value.daily_limit} 条）。",
            daily_count=count,
        )
    if last_interaction is not None and now - last_interaction < timedelta(minutes=30):
        return _denied(
            "recent_interaction",
            "刚刚已有互动，无需立即再次主动打扰。",
            daily_count=count,
        )
    if proactive_times:
        cooldown = timedelta(
            minutes=_FREQUENCY_COOLDOWN_MINUTES[value.proactive_frequency]
        )
        if now - max(proactive_times) < cooldown:
            return _denied(
                "cooldown",
                f"仍在“{FREQUENCY_LABELS[value.proactive_frequency]}”频率的冷却时间内。",
                daily_count=count,
            )

    state = role_state if isinstance(role_state, Mapping) else {}
    open_threads = _state_list(state, "open_threads")
    shared = _state_list(state, "shared_memories")
    if open_threads:
        basis = "延续双方尚未聊完的话题"
    elif shared:
        basis = "结合双方已有共同经历自然联系"
    elif now.weekday() >= 5:
        basis = "结合周末和角色自己的生活开启轻量话题"
    else:
        basis = "结合当前本地时段和角色自己的生活开启轻量话题"
    slot = count + 1
    event_source = f"{conversation_id}|{now.date().isoformat()}|{slot}"
    event_id = hashlib.sha256(event_source.encode("utf-8")).hexdigest()[:32]
    next_date = now.date() + timedelta(days=1)
    next_midnight = datetime.combine(next_date, time.min, tzinfo=now.tzinfo)
    lease_ttl = max(600, min(int((next_midnight - now).total_seconds()) + 7_200, 172_800))
    return ProactiveDecision(
        True,
        "allowed",
        f"{basis}；已通过静默时段、冷却时间和每日上限检查。",
        event_id,
        lease_ttl,
        count,
    )


def proactive_context_text(
    policy: RelationshipPolicy,
    role_state: Mapping[str, Any] | None,
    recent_proactive_messages: Sequence[str],
    explanation: str,
) -> str:
    """提供有依据的主动话题素材，并明确禁止外部事实臆造。"""

    state = role_state if isinstance(role_state, Mapping) else {}
    lines = [
        "本次主动联系依据：" + explanation,
        "不得声称未知的天气、地理位置、用户作息、用户正在做的事情或尚未确认的现实状态。",
        "不要催回复，也不要重复发送问候、午饭、晚饭、失眠等近期已经使用过的切入点。",
    ]
    open_threads = _state_list(state, "open_threads")
    if open_threads:
        lines.append("可自然续接的未完话题：" + "｜".join(open_threads[:4]))
    boundaries = _relationship_boundaries(state)
    combined_boundaries = tuple(dict.fromkeys((*policy.blocked_topics, *boundaries)))
    if combined_boundaries:
        lines.append("不得主动展开：" + "、".join(combined_boundaries[:8]))
    recent = [" ".join(str(item).split())[:180] for item in recent_proactive_messages if str(item).strip()]
    if recent:
        lines.append("近期主动消息（本次不得复述或同义改写）：" + "｜".join(recent[-3:]))
    return "\n".join(lines)


def is_repetitive_proactive_message(
    candidate: str, recent_messages: Sequence[str]
) -> bool:
    """用本地字符片段相似度拦截模型生成的重复问候或同义复述。"""

    normalized = _message_signature(candidate)
    if len(normalized) < 4:
        return bool(normalized) and any(
            normalized == _message_signature(item) for item in recent_messages
        )
    candidate_parts = {
        normalized[index : index + 2]
        for index in range(len(normalized) - 1)
    }
    for message in recent_messages[-5:]:
        recent = _message_signature(message)
        if not recent:
            continue
        if normalized == recent:
            return True
        recent_parts = {
            recent[index : index + 2]
            for index in range(max(0, len(recent) - 1))
        }
        union = candidate_parts | recent_parts
        if union and len(candidate_parts & recent_parts) / len(union) >= 0.72:
            return True
    return False


def stabilize_role_state(
    previous_state: Mapping[str, Any] | None,
    candidate_state: Mapping[str, Any],
    *,
    user_text: str,
    assistant_text: str,
    pace: str = "natural",
) -> dict:
    """限制模型造成的关系数值跳变，并保留用户已明确的称呼与边界。"""

    result = copy.deepcopy(dict(candidate_state))
    previous = previous_state if isinstance(previous_state, Mapping) else {}
    old_relationship = previous.get("relationship")
    new_relationship = result.get("relationship")
    if not isinstance(new_relationship, dict):
        new_relationship = {}
        result["relationship"] = new_relationship
    if not isinstance(old_relationship, Mapping):
        old_relationship = {}

    combined = f"{user_text}\n{assistant_text}"
    eventful = any(pattern in combined for pattern in _RELATIONSHIP_EVENT_PATTERNS)
    casual = bool(_CASUAL_PATTERNS.fullmatch(" ".join(user_text.split())))
    normalized_pace = pace if pace in PACE_VALUES else "natural"
    event_limits = {"slow": 2, "natural": 4, "fast": 6}
    delta_limit = 0 if casual else event_limits[normalized_pace] if eventful else 1
    changed = False
    for key in ("trust", "intimacy", "tension"):
        old = _bounded_score(old_relationship.get(key), 0)
        proposed = _bounded_score(new_relationship.get(key), old)
        bounded = max(old - delta_limit, min(old + delta_limit, proposed))
        new_relationship[key] = bounded
        changed = changed or bounded != old

    if not eventful:
        new_relationship["stage"] = old_relationship.get("stage", "")
        if not changed:
            new_relationship["recent_change"] = ""
    if not re.search(r"(?:叫我|称呼我|别叫我|不要叫我)", user_text):
        new_relationship["preferred_address"] = old_relationship.get(
            "preferred_address", ""
        )
    previous_boundaries = _short_strings(old_relationship.get("boundaries"))
    candidate_boundaries = _short_strings(new_relationship.get("boundaries"))
    if re.search(r"(?:不要|别|停止|不想|不喜欢|禁止|暂停)", user_text):
        new_relationship["boundaries"] = list(
            dict.fromkeys((*previous_boundaries, *candidate_boundaries))
        )[:6]
    else:
        new_relationship["boundaries"] = list(previous_boundaries)
    return result


def _normalized_policy(policy: RelationshipPolicy) -> RelationshipPolicy:
    pace = policy.pace if policy.pace in PACE_VALUES else "natural"
    frequency = (
        policy.proactive_frequency
        if policy.proactive_frequency in FREQUENCY_VALUES
        else "normal"
    )
    return replace(
        policy,
        pace=pace,
        preferred_address=" ".join(str(policy.preferred_address).split())[:40],
        allowed_topics=_topic_value(policy.allowed_topics, ()),
        blocked_topics=_topic_value(policy.blocked_topics, ()),
        proactive_frequency=frequency,
        daily_limit=max(0, min(_integer(policy.daily_limit, 2), 12)),
        quiet_start=_clock(policy.quiet_start, "22:30"),
        quiet_end=_clock(policy.quiet_end, "08:00"),
        paused_until=str(policy.paused_until or "")[:64],
    )


def _split_topics(value: str) -> tuple[str, ...]:
    return _topic_value(re.split(r"[\n,，、;；]+", str(value)), ())


def _topic_value(value: Any, default: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, str):
        values: Sequence[Any] = re.split(r"[\n,，、;；]+", value)
    elif isinstance(value, Sequence):
        values = value
    else:
        return default
    result = []
    for item in values:
        text_value = " ".join(str(item).split()).strip()[:80]
        if text_value and text_value not in result:
            result.append(text_value)
    return tuple(result[:16])


def _clock(value: Any, default: str) -> str:
    match = re.fullmatch(r"\s*(\d{1,2}):(\d{2})\s*", str(value))
    if match is None:
        return default
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 23 or minute > 59:
        return default
    return f"{hour:02d}:{minute:02d}"


def _integer(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _bounded_score(value: Any, default: int) -> int:
    try:
        return max(0, min(int(round(float(value))), 100))
    except (TypeError, ValueError):
        return default


def _aware_local(value: datetime | None) -> datetime:
    current = value or datetime.now().astimezone()
    return current.astimezone() if current.tzinfo is None else current


def _parse_datetime(value: Any, reference: datetime) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=reference.tzinfo)
    return parsed.astimezone(reference.tzinfo)


def _in_quiet_hours(moment: datetime, start: str, end: str) -> bool:
    start_minutes = int(start[:2]) * 60 + int(start[3:])
    end_minutes = int(end[:2]) * 60 + int(end[3:])
    current = moment.hour * 60 + moment.minute
    if start_minutes == end_minutes:
        return False
    if start_minutes < end_minutes:
        return start_minutes <= current < end_minutes
    return current >= start_minutes or current < end_minutes


def _relationship_boundaries(role_state: Mapping[str, Any] | None) -> tuple[str, ...]:
    state = role_state if isinstance(role_state, Mapping) else {}
    relationship = state.get("relationship")
    if not isinstance(relationship, Mapping):
        return ()
    return _short_strings(relationship.get("boundaries"))


def _state_list(state: Mapping[str, Any], key: str) -> tuple[str, ...]:
    return _short_strings(state.get(key))


def _short_strings(value: Any) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(
        text_value
        for item in value[:8]
        if (text_value := " ".join(str(item).split()).strip()[:160])
    )


def _message_signature(value: Any) -> str:
    return "".join(
        re.findall(r"[a-z0-9\u3400-\u9fff]", str(value).lower())
    )[:300]


def _denied(
    code: str, explanation: str, *, daily_count: int = 0
) -> ProactiveDecision:
    return ProactiveDecision(False, code, explanation, daily_count=daily_count)
