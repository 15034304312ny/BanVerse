from __future__ import annotations

from datetime import datetime

from deepseek_cli.character_cards import empty_card
from deepseek_cli.desktop.ai_features import (
    ReplyPlan,
    ReplySegment,
    assign_image_events,
    enrich_role_image_prompt,
)
from deepseek_cli.multimodal import (
    VisualIdentity,
    build_scene_context,
    has_current_image_share_intent,
    image_provider_capabilities,
    parse_vision_observation,
    read_visual_identity,
    scene_context_prompt,
    user_opted_out_of_images,
    vision_context_text,
    write_visual_identity,
)


def test_visual_identity_round_trip_preserves_other_extensions() -> None:
    card = empty_card("林小满")
    card["data"]["extensions"] = {"other": {"kept": True}}
    identity = VisualIdentity(
        description="黑色短发，棕眼，圆脸",
        default_outfit="浅蓝连帽卫衣",
        negative_prompt="不要改变发型和眼睛颜色",
        use_avatar_reference=False,
    )

    updated = write_visual_identity(card, identity)

    assert updated["data"]["extensions"]["other"] == {"kept": True}
    assert read_visual_identity(updated) == identity
    assert "visual_identity" not in card["data"]["extensions"]


def test_provider_advanced_image_capabilities_require_declaration() -> None:
    default = image_provider_capabilities("grsai")
    declared = image_provider_capabilities(
        "grsai",
        declared_capabilities=("reference_image", "identity_consistency"),
    )

    assert default.text_to_image and default.vision
    assert not default.reference_image and not default.image_to_image
    assert declared.reference_image and declared.identity_consistency
    assert not declared.image_to_image


def test_scene_context_uses_only_known_facts_and_local_time() -> None:
    moment = datetime(2026, 8, 31, 12, 20).astimezone()
    context = build_scene_context(
        {
            "scene": {
                "location": "公司楼下的咖啡店",
                "ongoing_action": "等午餐",
                "outfit": "白衬衫和深蓝长裤",
            }
        },
        recent_event="刚聊到午饭吃什么",
        current_time=moment,
    )

    prompt = scene_context_prompt(context)

    assert "12:20" in prompt
    assert "公司楼下的咖啡店" in prompt
    assert "等午餐" in prompt
    assert "白衬衫和深蓝长裤" in prompt
    assert "刚聊到午饭吃什么" in prompt
    assert "未提供的天气" in prompt


def test_vision_context_hides_low_confidence_ocr_and_sensitive_inference() -> None:
    raw = (
        '{"summary":"画面中的人物属于某民族","people":["实名张三"],'
        '"objects":["咖啡杯"],"scene":"室内","actions":[],'
        '"visible_text":[{"text":"私人号码 1234","confidence":0.31},'
        '{"text":"OPEN","confidence":0.95}],"confidence":0.98,'
        '"uncertainties":[]}'
    )

    observation = parse_vision_observation(raw)
    context = vision_context_text(raw)

    assert observation.confidence < 0.65
    assert "某民族" not in context
    assert "张三" not in context
    assert "私人号码" not in context
    assert "OPEN" in context
    assert "不得作肯定陈述" in context


def test_legacy_vision_text_is_explicitly_low_confidence() -> None:
    observation = parse_vision_observation("桌上似乎有一本书。")
    context = vision_context_text("桌上似乎有一本书。")

    assert observation.confidence == 0.45
    assert "低置信度画面概览" in context


def test_current_image_intent_excludes_imagined_history_and_opt_out() -> None:
    assert has_current_image_share_intent("我这就发一张照片给你看。")
    assert has_current_image_share_intent("我刚拍下窗边的暮色了。")
    assert not has_current_image_share_intent("想象一张我们在海边的照片。")
    assert not has_current_image_share_intent("上次发的照片还记得吗？")
    assert not has_current_image_share_intent("我不发照片了。")
    assert user_opted_out_of_images("今天别发图，聊天就好。")


def test_image_event_is_unique_and_prompt_uses_shared_scene() -> None:
    plan = ReplyPlan(
        (
            ReplySegment("dialogue", text="给你看看。"),
            ReplySegment("image", prompt="窗边的一张生活照"),
            ReplySegment("image", prompt="不应再发第二张"),
        )
    )
    assigned = assign_image_events(plan, "turn-42")
    image_segments = [item for item in assigned.segments if item.kind == "image"]

    assert len(image_segments) == 1
    assert image_segments[0].event_id
    assert image_segments[0].status == "pending"

    card = write_visual_identity(
        empty_card("林小满"),
        VisualIdentity(
            description="黑色短发，棕色眼睛",
            default_outfit="米白针织衫",
            negative_prompt="不要改变发色",
        ),
    )
    prompt = enrich_role_image_prompt(
        "林小满",
        card,
        image_segments[0].prompt,
        current_time=datetime(2026, 8, 31, 19, 30).astimezone(),
        role_state={
            "scene": {
                "location": "家中客厅",
                "ongoing_action": "收拾晚餐",
                "outfit": "浅灰家居服",
            }
        },
        recent_event="刚结束视频通话",
    )

    assert "稳定视觉身份：黑色短发，棕色眼睛" in prompt
    assert "本次已知服装：浅灰家居服" in prompt
    assert "家中客厅" in prompt
    assert "19:30" in prompt
    assert "刚结束视频通话" in prompt
    assert "负面约束：不要改变发色" in prompt
