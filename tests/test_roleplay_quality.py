from __future__ import annotations

import json
from pathlib import Path

from deepseek_cli.desktop.builtin_characters import load_builtin_catalog
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
    }
    assert all(case["user"].strip() and case["reply"].strip() for case in cases)


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
