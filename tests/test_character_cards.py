import json

import pytest

from deepseek_cli.character_cards import (
    CharacterCardError,
    dump_card,
    empty_card,
    parse_card_json,
)


def test_v2_round_trip_preserves_unknown_extensions_and_character_book():
    card = empty_card("Alice")
    card["data"]["extensions"] = {"third_party": {"value": 7}}
    card["data"]["future_field"] = {"kept": True}
    card["data"]["character_book"] = {
        "extensions": {"vendor": "x"},
        "entries": [
            {
                "keys": ["moon"],
                "content": "Moon lore",
                "extensions": {},
                "enabled": True,
                "insertion_order": 1,
            }
        ],
    }

    restored = parse_card_json(dump_card(card))

    assert restored["data"]["extensions"]["third_party"]["value"] == 7
    assert restored["data"]["future_field"] == {"kept": True}
    assert restored["data"]["character_book"]["entries"][0]["content"] == "Moon lore"


def test_v2_rejects_missing_name_and_wrong_types():
    card = empty_card()
    card["data"]["name"] = ""
    with pytest.raises(CharacterCardError):
        parse_card_json(json.dumps(card))
    card = empty_card()
    card["data"]["tags"] = "not-a-list"
    with pytest.raises(CharacterCardError):
        parse_card_json(json.dumps(card))
