from datetime import datetime, timedelta, timezone

from deepseek_cli.character_cards import empty_card
from deepseek_cli.character_prompt import (
    build_character_prompt,
    roleplay_memory_query,
)
from deepseek_cli.gateway import Message


def test_prompt_injects_role_fields_but_excludes_author_metadata():
    card = empty_card("Alice")
    data = card["data"]
    data.update(
        {
            "description": "{{char}} protects {{user}}.",
            "personality": "Kind",
            "scenario": "A forest",
            "system_prompt": "Stay in character.",
            "post_history_instructions": "Never narrate {{user}}.",
            "creator_notes": "SECRET AUTHOR NOTE",
            "creator": "Author Name",
            "character_version": "9",
            "tags": ["tagged"],
            "mes_example": "<START>\n{{user}}: Hello\n{{char}}: Welcome",
        }
    )

    prompt = build_character_prompt(card, [], "Hi", user_name="Bob")

    assert "Alice protects Bob" in prompt.system
    assert "Stay in character" in prompt.system
    assert "Never narrate Bob" not in prompt.system
    assert "Never narrate Bob" in prompt.post_history
    assert "SECRET AUTHOR NOTE" not in prompt.system
    assert "Author Name" not in prompt.system
    assert "tagged" not in prompt.system
    assert prompt.examples == (
        Message("user", "Hello"),
        Message("assistant", "Welcome"),
    )


def test_prompt_matches_constant_and_keyword_lore():
    card = empty_card("Alice")
    card["data"]["character_book"] = {
        "extensions": {},
        "entries": [
            {
                "keys": [],
                "content": "Always active",
                "extensions": {},
                "enabled": True,
                "constant": True,
                "insertion_order": 2,
                "position": "before_char",
            },
            {
                "keys": ["moon"],
                "content": "Moon active",
                "extensions": {},
                "enabled": True,
                "insertion_order": 1,
                "position": "after_char",
            },
        ],
    }

    prompt = build_character_prompt(card, [], "Look at the moon")

    assert "Always active" in prompt.system
    assert "Moon active" in prompt.system


def test_prompt_injects_user_persona_and_continuity_state():
    card = empty_card("Alice")

    prompt = build_character_prompt(
        card,
        [],
        "继续",
        user_name="小岚",
        user_persona="喜欢安静，不喜欢被连续追问",
        role_state={
            "scene": {"location": "天台"},
            "open_threads": ["等雨停"],
        },
    )

    assert "角色演绎原则" in prompt.system
    assert "隐藏导演节拍" in prompt.system
    assert "用户称呼：小岚" in prompt.system
    assert "喜欢安静" in prompt.system
    assert "当前连续性状态" in prompt.system
    assert "等雨停" in prompt.system


def test_prompt_formats_emotion_relationship_and_recalled_memory_for_director():
    card = empty_card("Alice")

    prompt = build_character_prompt(
        card,
        [],
        "还记得吗",
        role_state={
            "emotion": {
                "primary": "担心",
                "cause": "用户突然失联",
                "intensity": 68,
            },
            "character_state": {"current_goal": "确认用户平安"},
            "relationship": {
                "stage": "熟悉",
                "trust": 55,
                "recent_change": "刚修复一次误会",
            },
        },
        recalled_memories=("用户曾在蓝鲸书店与角色共撑一把伞",),
    )

    assert "主要=担心" in prompt.system
    assert "原因=用户突然失联" in prompt.system
    assert "信任=55" in prompt.system
    assert "与当前话题相关的较早共同经历" in prompt.system
    assert "蓝鲸书店" in prompt.system


def test_roleplay_memory_query_combines_current_topic_and_open_state():
    query = roleplay_memory_query(
        "那把伞还在吗",
        {
            "emotion": {"cause": "想起雨夜"},
            "character_state": {"concern": "用户可能着凉"},
            "relationship": {"recent_change": "开始愿意互相照顾"},
            "open_threads": ["下次去蓝鲸书店"],
        },
    )

    assert "那把伞还在吗" in query
    assert "想起雨夜" in query
    assert "蓝鲸书店" in query
    assert "互相照顾" in query


def test_prompt_injects_real_local_time_for_role_awareness():
    card = empty_card("Alice")
    moment = datetime(
        2026, 7, 31, 23, 20, tzinfo=timezone(timedelta(hours=8))
    )

    prompt = build_character_prompt(
        card,
        [],
        "还没睡",
        current_time=moment,
    )

    assert "当前本地时间" in prompt.system
    assert "2026年7月31日" in prompt.system
    assert "23:20" in prompt.system
    assert "深夜" in prompt.system
    assert "失眠" in prompt.system


def test_worldbook_keywords_only_scan_recent_history():
    card = empty_card("Alice")
    card["data"]["character_book"] = {
        "extensions": {},
        "entries": [
            {
                "keys": ["ancient-key"],
                "content": "Old lore",
                "extensions": {},
                "enabled": True,
                "insertion_order": 1,
                "position": "after_char",
            }
        ],
    }
    history = [Message("user", "ancient-key")]
    history.extend(Message("user", f"recent-{index}") for index in range(13))

    prompt = build_character_prompt(card, history, "continue")

    assert "Old lore" not in prompt.system
