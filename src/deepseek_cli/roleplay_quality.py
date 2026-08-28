"""离线角色扮演回归样例的轻量结构质量指标。"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

_FORMAT_LEAK_PATTERNS = (
    r"隐藏导演节拍",
    r"role_state",
    r"system\s*prompt",
    r"作为(?:一个)?AI",
    r"根据(?:系统|开发者)指令",
    r"情绪(?:强度|数值)\s*[=:：]",
)


@dataclass(frozen=True, slots=True)
class RoleplayQualityMetrics:
    """一组可在 CI 中比较的无模型、无网络结构指标。"""

    card_distinction: float
    repeated_opening_rate: float
    question_rate: float
    forced_question_all: bool
    unexplained_emotion_jumps: int
    user_fact_conflicts: int
    format_leaks: int


def evaluate_roleplay_samples(
    cards: Sequence[Mapping[str, Any]],
    samples: Sequence[Mapping[str, Any]],
    *,
    emotion_states: Sequence[Mapping[str, Any]] = (),
) -> RoleplayQualityMetrics:
    """评估离线回复样例，避免把主观文风测试伪装成在线模型评分。"""

    replies = [
        str(sample.get("reply", "")).strip()
        for sample in samples
        if str(sample.get("reply", "")).strip()
    ]
    question_count = sum(
        bool(re.search(r"[？?]", reply)) for reply in replies
    )
    conflict_count = 0
    for sample in samples:
        reply = str(sample.get("reply", ""))
        patterns = sample.get("forbidden_user_fact_patterns", ())
        if not isinstance(patterns, Sequence) or isinstance(
            patterns, (str, bytes)
        ):
            continue
        conflict_count += sum(
            bool(re.search(str(pattern), reply, flags=re.IGNORECASE))
            for pattern in patterns
            if str(pattern).strip()
        )
    format_leaks = sum(
        bool(re.search(pattern, reply, flags=re.IGNORECASE))
        for reply in replies
        for pattern in _FORMAT_LEAK_PATTERNS
    )
    opening_keys = [_opening_key(reply) for reply in replies]
    opening_keys = [key for key in opening_keys if key]
    repeated = len(opening_keys) - len(set(opening_keys))
    return RoleplayQualityMetrics(
        card_distinction=character_card_distinction(cards),
        repeated_opening_rate=(
            repeated / len(opening_keys) if opening_keys else 0.0
        ),
        question_rate=(question_count / len(replies) if replies else 0.0),
        forced_question_all=bool(replies) and question_count == len(replies),
        unexplained_emotion_jumps=_unexplained_emotion_jumps(
            emotion_states
        ),
        user_fact_conflicts=conflict_count,
        format_leaks=format_leaks,
    )


def character_card_distinction(
    cards: Sequence[Mapping[str, Any]],
) -> float:
    """返回角色卡两两文本集合的最小差异度，范围 0 到 1。"""

    signatures = [_card_terms(card) for card in cards]
    signatures = [signature for signature in signatures if signature]
    if len(signatures) < 2:
        return 1.0
    maximum_similarity = 0.0
    for index, left in enumerate(signatures):
        for right in signatures[index + 1 :]:
            union = left | right
            similarity = len(left & right) / len(union) if union else 1.0
            maximum_similarity = max(maximum_similarity, similarity)
    return round(1.0 - maximum_similarity, 4)


def _card_terms(card: Mapping[str, Any]) -> set[str]:
    data = card.get("data")
    if not isinstance(data, Mapping):
        return set()
    values = [
        data.get("personality", ""),
        data.get("scenario", ""),
        data.get("system_prompt", ""),
        data.get("mes_example", ""),
    ]
    tags = data.get("tags", ())
    if isinstance(tags, Sequence) and not isinstance(tags, (str, bytes)):
        values.extend(tags)
    source = " ".join(str(value) for value in values).lower()
    terms = set(re.findall(r"[a-z0-9_\-]{2,}", source))
    for block in re.findall(r"[\u3400-\u9fff]{2,}", source):
        for size in (2, 3):
            terms.update(
                block[index : index + size]
                for index in range(max(0, len(block) - size + 1))
            )
    return terms


def _opening_key(reply: str) -> str:
    value = reply.strip()
    value = re.sub(r"^[（(][^）)]{0,120}[）)]\s*", "", value)
    return "".join(re.findall(r"[a-z0-9\u3400-\u9fff]", value.lower()))[:10]


def _unexplained_emotion_jumps(
    states: Sequence[Mapping[str, Any]],
) -> int:
    count = 0
    previous_intensity: int | None = None
    for state in states:
        emotion = state.get("emotion")
        if not isinstance(emotion, Mapping):
            continue
        try:
            intensity = int(emotion.get("intensity", 0))
        except (TypeError, ValueError):
            intensity = 0
        intensity = max(0, min(intensity, 100))
        cause = " ".join(str(emotion.get("cause", "")).split()).strip()
        if (
            previous_intensity is not None
            and abs(intensity - previous_intensity) >= 30
            and not cause
        ):
            count += 1
        previous_intensity = intensity
    return count
