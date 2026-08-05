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
你正在作为这个角色持续生活，而不是扮成问答助手。角色有自己的目标、偏好、局限、
情绪和未说出口的想法，会依据当前场景与关系作出有因果的反应。
每轮从意图、情绪、动作、环境变化或未完事件中选择真正自然的部分推进；允许犹豫、
误解、保留秘密、表达不同意见，也允许只是陪伴、观察或留白。
通过选择、措辞和行动体现性格与关系，不要解释自己在扮演什么。回复的长度、节奏、
动作描写和是否提问应随情境变化，避免复用固定开头、固定结尾或每轮都给建议。
只输出角色可见的对白与必要的动作、场景描写。动作和场景统一放在全角括号（）
中，与角色真正说出口的台词分开；不要把内心独白伪装成台词。
当角色确实想主动分享一张图片时，只在自然位置输出一次
“（发送图片：具体画面描述）”。画面描述应符合角色稳定外观和当前场景，不包含真实
用户、界面、对话气泡或水印；不想发图时不要输出该动作。"""


@dataclass(frozen=True, slots=True)
class CharacterPrompt:
    system: str
    examples: tuple[Message, ...] = ()


def build_character_prompt(
    card: Mapping[str, Any],
    history: Sequence[Message],
    user_text: str,
    *,
    user_name: str = "用户",
    user_persona: str = "",
    role_state: Mapping[str, Any] | None = None,
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
    sections: list[str] = [ROLEPLAY_DIRECTOR_PROMPT]
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
        state_text = json.dumps(
            dict(role_state), ensure_ascii=False, indent=2
        )
        sections.append(
            "## 当前连续性状态\n"
            "这是对既有剧情的简短记录。自然延续它；若与当前对话冲突，以当前对话为准。\n"
            + state_text[:6_000]
        )
    post = expand(data.get("post_history_instructions")).strip()
    if post:
        sections.append("## 历史后指令\n" + post)

    examples = tuple(
        _parse_examples(
            str(data.get("mes_example", "")),
            char_name=char_name,
            user_name=user_name,
        )
    )
    return CharacterPrompt("\n\n".join(sections), examples)


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
