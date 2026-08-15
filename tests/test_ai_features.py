from datetime import datetime, timedelta, timezone

from deepseek_cli.desktop.ai_features import (
    ProactiveMessageScheduler,
    autonomous_image_request,
    classify_role_reply,
    clean_ai_summary,
    deserialize_reply_segments,
    enrich_role_image_prompt,
    explicit_image_request_prompt,
    opening_request,
    parse_autonomous_image_decision,
    parse_role_postprocess,
    proactive_request,
    role_memory_request,
    serialize_reply_segments,
    summary_request,
)
from deepseek_cli.desktop.data.database import Database
from deepseek_cli.desktop.data.repositories import SettingsRepository
from deepseek_cli.gateway import Message
from deepseek_cli.time_context import local_time_context


def test_summary_prompt_and_cleaner_produce_compact_plain_text():
    assert "完整回复" in summary_request("完整回复")
    assert clean_ai_summary("## AI 摘要：\n“双方决定继续调查关键线索。”") == (
        "双方决定继续调查关键线索。"
    )
    assert clean_ai_summary("很长" * 40).endswith("…")


def test_proactive_request_names_character_without_exposing_timer():
    moment = datetime(
        2026, 7, 31, 12, 5, tzinfo=timezone(timedelta(hours=8))
    )
    text = proactive_request("谢昭宁", current_time=moment)

    assert "谢昭宁" in text
    assert "定时器" not in text
    assert "2026年7月31日" in text
    assert "12:05" in text
    assert "午饭吃什么" in text


def test_opening_request_is_explicitly_first_contact():
    moment = datetime(2026, 8, 15, 20, 30).astimezone()

    text = opening_request("林小满", current_time=moment)

    assert "林小满" in text
    assert "全新的会话" in text
    assert "没有可延续的聊天历史" in text


def test_local_time_context_distinguishes_lunch_and_late_night():
    timezone_hk = timezone(timedelta(hours=8), "Asia/Hong_Kong")
    lunch = local_time_context(
        datetime(2026, 7, 31, 12, 30, tzinfo=timezone_hk)
    )
    late_night = local_time_context(
        datetime(2026, 7, 31, 23, 40, tzinfo=timezone_hk)
    )

    assert lunch.period == "午间"
    assert "午饭吃什么" in lunch.guidance
    assert "星期五" in lunch.display
    assert late_night.period == "深夜"
    assert "失眠" in late_night.guidance


def test_role_memory_request_and_parser_keep_bounded_structured_state():
    request = role_memory_request(
        "林小满",
        '{"scene":{"location":"地铁"}}',
        "今天想安静一下",
        "好，我先去上班，不追问你。",
    )

    assert "上一版连续性状态" in request
    assert "今天想安静一下" in request
    result = parse_role_postprocess(
        '```json\n{"summary":"小满尊重用户想安静的边界",'
        '"role_state":{"scene":{"location":"地铁","time":"早晨",'
        '"ongoing_action":"去上班"},"character_state":{"mood":"关心",'
        '"current_desire":"给用户空间"},"relationship":{"stage":"熟悉",'
        '"preferred_address":"你","boundaries":["不追问"]},'
        '"user_facts":["今天想安静"],"shared_memories":[],'
        '"open_threads":["下班后再聊"],'
        '"recent_patterns":["轻声收尾","A","B","C","D","E","应被截断"]}}\n```'
    )

    assert result.summary == "小满尊重用户想安静的边界"
    assert result.role_state["scene"]["location"] == "地铁"
    assert result.role_state["relationship"]["boundaries"] == ["不追问"]
    assert len(result.role_state["recent_patterns"]) == 6
    assert not parse_role_postprocess("不是 JSON").summary


def test_autonomous_image_decision_is_strict_and_uses_character_context():
    card = {
        "data": {
            "description": "现代都市中的成年服装设计师，短发，喜欢拍生活片段。",
            "personality": "活泼亲近",
            "scenario": "下班后走在亮灯的街区",
        }
    }
    request = autonomous_image_request(
        "林小满",
        card,
        [
            Message("user", "今天过得怎么样？"),
            Message("assistant", "刚下班，路边的花店特别好看。"),
        ],
        "等一下，我想把这片晚霞和花店一起分享给你。",
    )

    assert "林小满" in request
    assert "成年服装设计师" in request
    assert "晚霞和花店" in request
    decision = parse_autonomous_image_decision(
        '```json\n{"send_image":true,"prompt":"现代都市傍晚的花店门口，'
        '一位短发成年女设计师举起手机记录晚霞，暖色平面插画"}\n```'
    )
    assert decision.send_image
    assert "花店门口" in decision.prompt
    assert not parse_autonomous_image_decision(
        '{"send_image":false,"prompt":"不应使用"}'
    ).send_image
    assert not parse_autonomous_image_decision("我觉得可以发图").send_image
    assert not parse_autonomous_image_decision(
        '{"send_image":true,"prompt":"太短"}'
    ).send_image


def test_explicit_image_request_recognizes_direct_and_implicit_phrasing():
    direct = explicit_image_request_prompt(
        "给我发一张你下班路上的照片吧"
    )
    implicit = explicit_image_request_prompt(
        "能不能让我看看你今天的穿搭是什么样子？"
    )

    assert "用户明确请求" in direct
    assert "下班路上" in direct
    assert "今天的穿搭" in implicit
    assert not explicit_image_request_prompt("我给你发了一张图片")
    assert not explicit_image_request_prompt("这张图片拍得很好看")


def test_role_reply_is_split_and_classified_without_showing_image_action():
    answer = (
        "我刚刚下班，今天路上遇到一件特别有意思的事。"
        "先让我喝口水，再慢慢讲给你听，好不好？"
        "（她靠在沙发上轻轻笑了一下）"
        "其实是花店老板把最后一束向日葵送给我了。"
        "（发送图片：雨后亮灯的花店门口，短发女孩抱着向日葵）"
        "你看，是不是很适合今天？"
    )

    plan = classify_role_reply(answer, max_dialogue_chars=36)

    assert [segment.kind for segment in plan.segments] == [
        "dialogue",
        "dialogue",
        "narration",
        "dialogue",
        "image",
        "dialogue",
    ]
    assert "发送图片" not in plan.visible_text
    assert "花店门口" in next(
        segment.prompt
        for segment in plan.segments
        if segment.kind == "image"
    )
    assert "靠在沙发" in plan.visible_text
    restored = deserialize_reply_segments(
        serialize_reply_segments(plan.segments)
    )
    assert restored == plan.segments


def test_role_image_prompt_includes_stable_character_context():
    card = {
        "data": {
            "description": "现代都市里的成年短发女设计师",
            "scenario": "雨后下班路上",
        }
    }

    prompt = enrich_role_image_prompt(
        "林小满", card, "抱着一束向日葵站在花店门口"
    )

    assert "林小满" in prompt
    assert "成年短发女设计师" in prompt
    assert "向日葵" in prompt
    assert "无界面" in prompt


def test_random_scheduler_respects_enabled_interval_settings(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    settings = SettingsRepository(database)
    settings.set("proactive_enabled", "true")
    settings.set("proactive_min_minutes", "30")
    settings.set("proactive_max_minutes", "120")
    calls = []

    def choose(minimum, maximum):
        calls.append((minimum, maximum))
        return 47

    scheduler = ProactiveMessageScheduler(settings, randint=choose)
    scheduler.start()

    assert calls == [(30, 120)]
    assert scheduler.next_delay_ms == 47 * 60_000
    scheduler.stop()
    assert scheduler.next_delay_ms is None
    database.close()


def test_random_scheduler_is_opt_in(tmp_path, qapp):
    database = Database(tmp_path / "chat.db")
    scheduler = ProactiveMessageScheduler(
        SettingsRepository(database),
        randint=lambda _minimum, _maximum: 5,
    )

    scheduler.start()

    assert scheduler.next_delay_ms is None
    database.close()
