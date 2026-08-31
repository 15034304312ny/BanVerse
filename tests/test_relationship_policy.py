from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from deepseek_cli.relationship_policy import (
    RelationshipPolicy,
    evaluate_proactive_message,
    is_repetitive_proactive_message,
    relationship_policy_for,
    relationship_policy_prompt,
    serialize_character_policy,
    stabilize_role_state,
)


class FakeSettings:
    def __init__(self, **values) -> None:
        self.values = values

    def get(self, key: str, default: str = "") -> str:
        return str(self.values.get(key, default))

    def get_bool(self, key: str, default: bool = False) -> bool:
        value = self.values.get(key, default)
        return str(value).lower() in {"1", "true", "yes", "on"}


@dataclass
class FakeTurn:
    created_at: str
    origin: str = "user"
    status: str = "completed"
    user_content: str = ""
    assistant_content: str = ""


def test_character_policy_inherits_global_and_supports_full_override() -> None:
    settings = FakeSettings(
        relationship_pace="slow",
        relationship_preferred_address="阿澄",
        relationship_allowed_topics="电影，做饭",
        relationship_blocked_topics="收入、住址",
        proactive_frequency="low",
        proactive_daily_limit="1",
        proactive_quiet_start="23:00",
        proactive_quiet_end="09:00",
    )
    inherited = relationship_policy_for(settings, "character-a")
    assert inherited.inherited is True
    assert inherited.pace == "slow"
    assert inherited.allowed_topics == ("电影", "做饭")
    assert inherited.blocked_topics == ("收入", "住址")

    override = RelationshipPolicy(
        pace="fast",
        preferred_address="小树",
        allowed_topics=("旅行",),
        blocked_topics=("家庭",),
        proactive_frequency="high",
        daily_limit=3,
        quiet_start="00:30",
        quiet_end="07:30",
        muted=True,
        inherited=False,
    )
    settings.values["relationship_policy_character_character-a"] = (
        serialize_character_policy(override)
    )
    loaded = relationship_policy_for(settings, "character-a")
    assert loaded == override


def test_policy_prompt_exposes_rules_not_hidden_relationship_scores() -> None:
    prompt = relationship_policy_prompt(
        RelationshipPolicy(
            pace="slow",
            preferred_address="老师",
            blocked_topics=("收入",),
        )
    )
    assert "关系发展速度：慢热" in prompt
    assert "用户偏好的称呼：老师" in prompt
    assert "禁止主动展开的话题：收入" in prompt
    assert "亲密度" not in prompt
    assert "威胁离开" in prompt


def test_proactive_quiet_hours_cover_cross_midnight_and_dst() -> None:
    policy = RelationshipPolicy(quiet_start="22:30", quiet_end="08:00")
    before_dst = datetime(
        2026, 3, 8, 1, 30, tzinfo=timezone(timedelta(hours=-5))
    )
    after_dst = datetime(
        2026, 3, 8, 8, 30, tzinfo=timezone(timedelta(hours=-4))
    )

    denied = evaluate_proactive_message(
        policy,
        (),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=before_dst,
    )
    allowed = evaluate_proactive_message(
        policy,
        (),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=after_dst,
    )
    assert denied.code == "quiet_hours"
    assert allowed.allowed is True


def test_proactive_disabled_pause_and_user_boundary_never_call_model() -> None:
    now = datetime(
        2026, 8, 30, 12, 0, tzinfo=timezone(timedelta(hours=8))
    )
    disabled = evaluate_proactive_message(
        RelationshipPolicy(),
        (),
        {},
        globally_enabled=False,
        conversation_id="conversation-a",
        current_time=now,
    )
    paused = evaluate_proactive_message(
        RelationshipPolicy(paused_until="2026-08-31T12:00:00+08:00"),
        (),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    boundary = evaluate_proactive_message(
        RelationshipPolicy(),
        (
            FakeTurn(
                "2026-08-29T10:00:00+08:00",
                user_content="这几天别主动找我，让我安静一下",
            ),
        ),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    assert disabled.code == "disabled"
    assert paused.code == "paused"
    assert boundary.code == "user_boundary"


def test_proactive_daily_limit_resets_at_local_midnight_and_event_is_idempotent() -> None:
    local_timezone = timezone(timedelta(hours=8))
    policy = RelationshipPolicy(
        daily_limit=1,
        proactive_frequency="high",
        quiet_start="03:00",
        quiet_end="04:00",
    )
    old = FakeTurn(
        "2026-08-29T23:40:00+08:00",
        origin="proactive",
        assistant_content="昨晚的消息",
    )
    now = datetime(2026, 8, 30, 9, 0, tzinfo=local_timezone)
    first = evaluate_proactive_message(
        policy,
        (old,),
        {"open_threads": ["那本读到一半的书"]},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    second_device = evaluate_proactive_message(
        policy,
        (old,),
        {"open_threads": ["那本读到一半的书"]},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    today = FakeTurn(
        "2026-08-30T09:01:00+08:00",
        origin="proactive",
        assistant_content="今天的消息",
    )
    capped = evaluate_proactive_message(
        policy,
        (old, today),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=datetime(2026, 8, 30, 18, 0, tzinfo=local_timezone),
    )
    assert first.allowed is True
    assert first.event_id == second_device.event_id
    assert first.lease_ttl_seconds > 600
    assert "未聊完的话题" in first.explanation
    assert capped.code == "daily_limit"


def test_proactive_recent_interaction_and_frequency_cooldown_are_separate() -> None:
    now = datetime(
        2026, 8, 30, 18, 0, tzinfo=timezone(timedelta(hours=8))
    )
    recent_user = FakeTurn(
        "2026-08-30T17:50:00+08:00", user_content="晚点再聊"
    )
    recent_proactive = FakeTurn(
        "2026-08-30T15:00:00+08:00",
        origin="proactive",
        assistant_content="下午好",
    )
    interaction = evaluate_proactive_message(
        RelationshipPolicy(proactive_frequency="high"),
        (recent_user,),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    cooldown = evaluate_proactive_message(
        RelationshipPolicy(proactive_frequency="normal"),
        (recent_proactive,),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    assert interaction.code == "recent_interaction"
    assert cooldown.code == "cooldown"


def test_sensitive_scene_and_repeated_generated_message_are_suppressed() -> None:
    now = datetime(
        2026, 8, 30, 18, 0, tzinfo=timezone(timedelta(hours=8))
    )
    sensitive = evaluate_proactive_message(
        RelationshipPolicy(quiet_start="00:00", quiet_end="00:00"),
        (
            FakeTurn(
                "2026-08-30T10:00:00+08:00",
                user_content="家人住院了，我现在在急诊",
            ),
        ),
        {},
        globally_enabled=True,
        conversation_id="conversation-a",
        current_time=now,
    )
    assert sensitive.code == "sensitive_scene"
    assert is_repetitive_proactive_message(
        "午休时看到一只神气的橘猫，占着长椅不让人坐。",
        ("午休时看到一只很神气的橘猫，占着长椅不让人坐。",),
    )
    assert not is_repetitive_proactive_message(
        "刚改完海报，突然想问你最近有没有听到好听的歌？",
        ("午休时看到一只很神气的橘猫，占着长椅不让人坐。",),
    )


def test_relationship_state_changes_are_slow_and_event_bound() -> None:
    previous = {
        "relationship": {
            "stage": "认识",
            "preferred_address": "阿澄",
            "trust": 20,
            "intimacy": 10,
            "tension": 5,
            "boundaries": ["不要催回复"],
        }
    }
    casual = stabilize_role_state(
        previous,
        {
            "relationship": {
                "stage": "恋人",
                "trust": 90,
                "intimacy": 95,
                "tension": 0,
                "boundaries": [],
            }
        },
        user_text="你好",
        assistant_text="晚上好。",
        pace="fast",
    )
    repaired = stabilize_role_state(
        previous,
        {
            "relationship": {
                "stage": "熟悉",
                "trust": 80,
                "intimacy": 50,
                "tension": 0,
                "boundaries": ["不要谈工作"],
            }
        },
        user_text="我原谅你了，但不要再催我回复",
        assistant_text="我记住了。",
        pace="slow",
    )
    assert casual["relationship"]["stage"] == "认识"
    assert casual["relationship"]["trust"] == 20
    assert casual["relationship"]["preferred_address"] == "阿澄"
    assert casual["relationship"]["boundaries"] == ["不要催回复"]
    assert repaired["relationship"]["trust"] == 22
    assert repaired["relationship"]["intimacy"] == 12
    assert repaired["relationship"]["tension"] == 3
    assert repaired["relationship"]["boundaries"] == [
        "不要催回复",
        "不要谈工作",
    ]
