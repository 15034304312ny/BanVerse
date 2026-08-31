"""Generate a deterministic, offline BanVerse roleplay baseline report."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = REPOSITORY_ROOT / "src"
if str(SOURCE_ROOT) not in sys.path:
    sys.path.insert(0, str(SOURCE_ROOT))

from deepseek_cli.roleplay_director import assess_director_trigger  # noqa: E402
from deepseek_cli.roleplay_quality import evaluate_roleplay_samples  # noqa: E402


def _load_cards() -> list[dict]:
    root = SOURCE_ROOT / "deepseek_cli" / "desktop" / "resources" / "builtin_characters"
    return [
        json.loads(path.read_text(encoding="utf-8"))
        for path in sorted(root.glob("*.json"))
    ]


def generate_report() -> dict:
    regression = json.loads(
        (REPOSITORY_ROOT / "tests" / "fixtures" / "roleplay_regression_cases.json").read_text(
            encoding="utf-8"
        )
    )
    matrix = json.loads(
        (REPOSITORY_ROOT / "tests" / "fixtures" / "provider_model_matrix.json").read_text(
            encoding="utf-8"
        )
    )
    cards = _load_cards()
    metrics = evaluate_roleplay_samples(
        cards,
        regression["cases"],
        emotion_states=regression["emotion_states"],
    )
    director_triggered = [
        str(case["id"])
        for case in regression["cases"]
        if assess_director_trigger(str(case["user"])).should_trigger(6)
    ]
    scenario_count = len(regression["cases"])
    return {
        "schema": 2,
        "network_used": False,
        "contains_user_data": False,
        "character_count": len(cards),
        "scenario_count": scenario_count,
        "scenario_categories": sorted(
            {str(case["category"]) for case in regression["cases"]}
        ),
        "provider_matrix_case_count": len(matrix["cases"]),
        "metrics": asdict(metrics),
        "director_experiment": {
            "enabled_by_default": False,
            "threshold": 6,
            "triggered_case_ids": director_triggered,
            "trigger_rate": (
                len(director_triggered) / scenario_count
                if scenario_count
                else 0.0
            ),
            "average_role_model_calls_if_enabled": (
                (scenario_count + len(director_triggered)) / scenario_count
                if scenario_count
                else 0.0
            ),
            "maximum_extra_calls_per_turn": 1,
            "timeout_budget_seconds": 8,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="生成不访问网络、不包含用户数据的角色扮演结构基线。"
    )
    parser.add_argument(
        "--output",
        type=Path,
        help="可选 JSON 输出路径；未提供时打印到标准输出。",
    )
    args = parser.parse_args()
    text = json.dumps(generate_report(), ensure_ascii=False, indent=2) + "\n"
    if args.output is None:
        print(text, end="")
    else:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
