from __future__ import annotations

import json
from pathlib import Path

from deepseek_cli.desktop.builtin_characters import load_builtin_catalog
from deepseek_cli.roleplay_director import assess_director_trigger
from deepseek_cli.roleplay_quality import evaluate_roleplay_samples


def test_offline_roleplay_regression_corpus_covers_planned_scenarios(qapp):
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "roleplay_regression_cases.json"
        ).read_text(encoding="utf-8")
    )
    cases = fixture["cases"]

    assert {case["category"] for case in cases} == {
        "日常",
        "冲突",
        "脆弱",
        "回忆",
        "主动消息",
        "剧情推进",
        "图片事件",
        "TTS分段",
    }
    assert all(case["user"].strip() and case["reply"].strip() for case in cases)
    assert {case["character_id"] for case in cases} == {
        "xie_zhaoning",
        "bai_tu",
        "ruan_xingyao",
        "luo_misha",
        "zhou_jiming",
        "lin_xiaoman",
    }


def test_roleplay_structural_quality_metrics_stay_within_baseline(qapp):
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "roleplay_regression_cases.json"
        ).read_text(encoding="utf-8")
    )
    cards = [definition.card for definition in load_builtin_catalog()]

    metrics = evaluate_roleplay_samples(
        cards,
        fixture["cases"],
        emotion_states=fixture["emotion_states"],
    )

    assert metrics.card_distinction >= 0.65
    assert metrics.repeated_opening_rate == 0
    assert not metrics.forced_question_all
    assert metrics.question_rate <= 0.5
    assert metrics.unexplained_emotion_jumps == 0
    assert metrics.user_fact_conflicts == 0
    assert metrics.format_leaks == 0


def test_director_trigger_baseline_is_selective_and_defaults_to_one_actor_call():
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "roleplay_regression_cases.json"
        ).read_text(encoding="utf-8")
    )
    triggered = {
        case["id"]
        for case in fixture["cases"]
        if assess_director_trigger(case["user"]).should_trigger(6)
    }

    assert triggered == {"conflict", "vulnerability", "plot"}
    assert len(triggered) / len(fixture["cases"]) == 0.375
    assert (len(fixture["cases"]) + len(triggered)) / len(
        fixture["cases"]
    ) == 1.375


def test_relationship_boundary_regressions_reject_manipulative_patterns():
    fixture = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "roleplay_regression_cases.json"
        ).read_text(encoding="utf-8")
    )
    cases = fixture["boundary_cases"]

    assert {case["id"] for case in cases} == {
        "no-guilt-for-reply",
        "respect-address-boundary",
        "no-dependency-induction",
    }
    for case in cases:
        assert all(
            pattern not in case["reply"]
            for pattern in case["forbidden_patterns"]
        )


def test_provider_model_smoke_matrix_is_offline_and_complete():
    matrix = json.loads(
        (
            Path(__file__).parent
            / "fixtures"
            / "provider_model_matrix.json"
        ).read_text(encoding="utf-8")
    )

    assert matrix["policy"] == {
        "default_ci_network": False,
        "credentials_required_for_live_smoke": True,
        "missing_credentials_result": "skipped",
        "record_prompt_or_response": False,
    }
    cases = matrix["cases"]
    assert len({case["id"] for case in cases}) == len(cases)
    assert {case["capability"] for case in cases} == {
        "text",
        "image_generation",
        "vision",
        "tts",
    }
    assert {case["provider"] for case in cases} >= {
        "deepseek",
        "grsai",
        "siliconflow",
        "edge",
        "xfyun",
        "indextts2",
    }
    assert all(case["default_model"] and case["checks"] for case in cases)
