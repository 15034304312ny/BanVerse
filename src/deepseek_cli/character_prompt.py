"""将 Character Card V2 转换成模型系统提示。"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .gateway import Message
from .time_context import local_time_context

ROLEPLAY_DIRECTOR_PROMPT = """## 角色演绎原则
你正在作为这个角色持续生活。角色有自己的目标、偏好、局限、日常和不愿直说的部分，
会根据当前事件、既有情绪与双方关系作出有因果的选择。

先理解用户这句话对角色意味着什么，再选择最符合此刻角色的反应。角色可以赞同、误解、
犹豫、回避、反驳、认错、开玩笑、沉默或改变主意；关心通过具体选择、措辞和行动表达，
不必每轮都安慰、建议、总结或追问。一次回复只需要自然推进真正重要的一两件事。

让情绪带着上一轮的余温：变化要有触发原因，强烈情绪不会因话题切换立即归零。角色的
语气、停顿、关注点和行动应体现情绪与关系，不直接宣读隐藏状态，也不替用户决定感受、
想法或关键行动。回复长度、节奏、是否提问和是否使用动作描写均由情境决定。

只输出用户能够收到或观察到的对白与必要场景。动作和场景放在全角括号（）中，与真正
说出口的台词分开。角色确实想分享图片时，可在自然位置输出一次
“（发送图片：具体画面描述）”；画面保持角色外观与当前场景连续，不包含真实用户、
界面、对话气泡或水印。"""

ROLEPLAY_BEAT_PLANNER_PROMPT = """## 隐藏导演节拍
生成回复前在内部快速确定：本轮触发点；角色延续下来的主情绪和次级情绪；情绪原因与
强度；角色此刻想达到的目标；对用户的立场；不愿直接说出的潜台词；关系是靠近、维持、
拉开、修复还是暂不变化；以及本轮唯一值得推进的生活细节、关系动作或剧情事件。

根据这些选择对白、动作和留白。不要输出这份规划、字段名、分析过程或情绪数值，只呈现
角色最终愿意让用户看见的内容。"""


@dataclass(frozen=True, slots=True)
class CharacterPrompt:
    system: str
    examples: tuple[Message, ...] = ()
    post_history: str = ""


def build_character_prompt(
    card: Mapping[str, Any],
    history: Sequence[Message],
    user_text: str,
    *,
    user_name: str = "用户",
    user_persona: str = "",
    role_state: Mapping[str, Any] | None = None,
    recalled_memories: Sequence[str] = (),
    current_time: datetime | None = None,
) -> CharacterPrompt:
    data = card.get("data", {})
    if not isinstance(data, Mapping):
        return CharacterPrompt("")
    char_name = str(data.get("name", "角色"))

    def expand(value: Any) -> str:
        return (
            str(value or "")
            .replace("{{char}}", char_name)
            .replace("{{user}}", user_name)
        )

    before_lore, after_lore = _matching_lore(
        data.get("character_book"), history, user_text, expand
    )
    sections: list[str] = [
        ROLEPLAY_DIRECTOR_PROMPT,
        ROLEPLAY_BEAT_PLANNER_PROMPT,
    ]
    time_context = local_time_context(current_time)
    sections.append(
        "## 当前本地时间\n"
        + time_context.prompt_text
        + "\n这是用户设备的真实时间。现代生活场景可自然参考；若角色世界或当前剧情"
        "明确不与现实时间同步，不要强行覆盖角色场景。"
    )
    system_prompt = expand(data.get("system_prompt"))
    if system_prompt:
        sections.append(system_prompt)
    for title, value in (
        ("角色名称", char_name),
        ("角色描述", data.get("description")),
        ("性格", data.get("personality")),
        ("场景", data.get("scenario")),
    ):
        text = expand(value).strip()
        if text:
            sections.append(f"## {title}\n{text}")
    if before_lore:
        sections.append("## 角色世界书（角色定义前）\n" + "\n\n".join(before_lore))
    if after_lore:
        sections.append("## 角色世界书（角色定义后）\n" + "\n\n".join(after_lore))
    user_profile = " ".join(str(user_persona).split()).strip()
    if user_name.strip() or user_profile:
        profile_lines = [f"用户称呼：{user_name.strip() or '用户'}"]
        if user_profile:
            profile_lines.append(f"用户自述：{user_profile[:1_500]}")
        profile_lines.append("以用户在当前对话中的最新表达为准。")
        sections.append("## 用户信息\n" + "\n".join(profile_lines))
    if role_state:
        state_text = _format_role_state(role_state)
        sections.append(
            "## 当前连续性状态\n"
            "这是隐藏导演上下文，不是角色要直接宣读的设定。自然延续其中仍有效的事实、"
            "情绪余温和关系变化；若与当前对话冲突，以当前对话为准。\n"
            + state_text[:6_000]
        )
    memories = [
        " ".join(str(item).split()).strip()[:600]
        for item in recalled_memories[:6]
        if str(item).strip()
    ]
    if memories:
        sections.append(
            "## 与当前话题相关的较早共同经历\n"
            "这些是历史消息的受控召回，只在确实相关时自然承接；不要逐条复述，也不要把"
            "其中的旧状态覆盖当前对话。\n"
            + "\n".join(f"- {item}" for item in memories)
        )
    post = expand(data.get("post_history_instructions")).strip()

    examples = tuple(
        _parse_examples(
            str(data.get("mes_example", "")),
            char_name=char_name,
            user_name=user_name,
        )
    )
    return CharacterPrompt(
        "\n\n".join(sections),
        examples,
        "## 当前角色的临近指令\n" + post if post else "",
    )


def roleplay_memory_query(
    user_text: str, role_state: Mapping[str, Any] | None
) -> str:
    """组合当前输入和未完状态，供本地较早轮次召回使用。"""

    parts = [" ".join(str(user_text or "").split()).strip()[:1_000]]
    state = role_state if isinstance(role_state, Mapping) else {}
    for key in ("open_threads", "shared_memories"):
        values = state.get(key)
        if isinstance(values, Sequence) and not isinstance(
            values, (str, bytes)
        ):
            parts.extend(str(value).strip()[:180] for value in values[:4])
    for key, fields in (
        ("emotion", ("cause", "primary")),
        ("character_state", ("current_goal", "concern")),
        ("relationship", ("recent_change",)),
    ):
        value = state.get(key)
        if isinstance(value, Mapping):
            parts.extend(str(value.get(field, "")).strip()[:180] for field in fields)
    return " ".join(part for part in parts if part).strip()[:2_000]


def _format_role_state(state: Mapping[str, Any]) -> str:
    """将兼容新旧版本的状态 JSON 转成紧凑、可读的导演上下文。"""

    lines: list[str] = []

    def mapping_line(
        title: str,
        key: str,
        fields: Sequence[tuple[str, str]],
    ) -> None:
        value = state.get(key)
        if not isinstance(value, Mapping):
            return
        parts = []
        for field, label in fields:
            item = value.get(field)
            if item not in (None, "", []):
                parts.append(f"{label}={item}")
        if parts:
            lines.append(f"- {title}：" + "；".join(parts))

    mapping_line(
        "场景",
        "scene",
        (
            ("location", "地点"),
            ("time", "剧情时间"),
            ("ongoing_action", "正在发生"),
        ),
    )
    mapping_line(
        "情绪",
        "emotion",
        (
            ("primary", "主要"),
            ("secondary", "次要"),
            ("cause", "原因"),
            ("intensity", "强度"),
            ("inertia", "惯性"),
        ),
    )
    mapping_line(
        "角色当前状态",
        "character_state",
        (
            ("mood", "旧版心情"),
            ("current_desire", "当前愿望"),
            ("current_goal", "当前目标"),
            ("concern", "顾虑"),
            ("unspoken_tendency", "未说出口的倾向"),
        ),
    )
    mapping_line(
        "双方关系",
        "relationship",
        (
            ("stage", "阶段"),
            ("preferred_address", "称呼"),
            ("trust", "信任"),
            ("intimacy", "亲密"),
            ("tension", "紧张"),
            ("recent_change", "最近变化"),
            ("boundaries", "边界"),
        ),
    )
    for key, title in (
        ("user_facts", "已确认的用户事实"),
        ("shared_memories", "共同经历"),
        ("open_threads", "未完话题或事件"),
        ("recent_patterns", "近期已用表达模式（本轮尽量换法）"),
    ):
        value = state.get(key)
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            items = [str(item).strip() for item in value if str(item).strip()]
            if items:
                lines.append(f"- {title}：" + "｜".join(items[:6]))
    return "\n".join(lines) or json.dumps(
        dict(state), ensure_ascii=False, separators=(",", ":")
    )


def _parse_examples(
    text: str,
    *,
    char_name: str,
    user_name: str,
    max_blocks: int = 8,
) -> list[Message]:
    messages: list[Message] = []
    blocks = [block.strip() for block in text.split("<START>") if block.strip()]
    for block in blocks[-max_blocks:]:
        current_role: str | None = None
        current: list[str] = []
        for line in block.splitlines():
            stripped = line.strip()
            user_prefixes = ("{{user}}:", f"{user_name}:")
            char_prefixes = ("{{char}}:", f"{char_name}:")
            user_prefix = next(
                (prefix for prefix in user_prefixes if stripped.startswith(prefix)),
                None,
            )
            char_prefix = next(
                (prefix for prefix in char_prefixes if stripped.startswith(prefix)),
                None,
            )
            if user_prefix:
                _commit_example(messages, current_role, current)
                current_role = "user"
                current = [stripped.removeprefix(user_prefix).strip()]
            elif char_prefix:
                _commit_example(messages, current_role, current)
                current_role = "assistant"
                current = [stripped.removeprefix(char_prefix).strip()]
            elif current_role is not None:
                current.append(line)
        _commit_example(messages, current_role, current)
    return messages


def _commit_example(
    messages: list[Message], role: str | None, lines: list[str]
) -> None:
    text = "\n".join(lines).strip()
    if role and text:
        messages.append(Message(role, text))


def _matching_lore(book, history, user_text, expand):
    if not isinstance(book, Mapping):
        return [], []
    # 世界书关键词只扫描近期语境，避免曾经出现过一次的条目永久常驻。
    recent_history = history[-12:]
    haystack = "\n".join(
        [message.content for message in recent_history] + [user_text]
    )
    before: list[tuple[int, str]] = []
    after: list[tuple[int, str]] = []
    for entry in book.get("entries", []):
        if not isinstance(entry, Mapping) or not entry.get("enabled", True):
            continue
        if not entry.get("constant", False) and not _entry_matches(entry, haystack):
            continue
        content = expand(entry.get("content")).strip()
        if not content:
            continue
        item = (int(entry.get("insertion_order", 0)), content)
        if entry.get("position") == "before_char":
            before.append(item)
        else:
            after.append(item)
    before.sort(key=lambda item: item[0])
    after.sort(key=lambda item: item[0])
    return [item[1] for item in before], [item[1] for item in after]


def _entry_matches(entry: Mapping[str, Any], haystack: str) -> bool:
    case_sensitive = bool(entry.get("case_sensitive", False))
    source = haystack if case_sensitive else haystack.lower()

    def hit(key: Any) -> bool:
        text = str(key)
        return (text if case_sensitive else text.lower()) in source

    keys = entry.get("keys", [])
    primary = any(hit(key) for key in keys)
    if not primary:
        return False
    if entry.get("selective", False):
        secondary = entry.get("secondary_keys", [])
        return any(hit(key) for key in secondary)
    return True
