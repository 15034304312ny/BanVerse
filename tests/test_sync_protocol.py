from __future__ import annotations

import json

import pytest

from deepseek_cli.sync_protocol import DEFAULT_SYNC_URL, parse_sync_pairing


def test_parse_sync_pairing_normalizes_valid_payload():
    pairing = parse_sync_pairing(
        json.dumps(
            {
                "server_url": "https://sync.example.test/",
                "account_id": "account-123456",
                "token": "token-12345678",
                "ignored": "not imported",
            }
        )
    )

    assert DEFAULT_SYNC_URL == "https://47.102.121.29"
    assert pairing == {
        "server_url": "https://sync.example.test",
        "account_id": "account-123456",
        "token": "token-12345678",
    }


@pytest.mark.parametrize(
    "value",
    [
        "",
        "not-json",
        "[]",
        '{"server_url":"file:///tmp/sync","account_id":"account-123456",'
        '"token":"token-12345678"}',
        '{"server_url":"https://sync.example.test","account_id":"short",'
        '"token":"token-12345678"}',
    ],
)
def test_parse_sync_pairing_rejects_invalid_payload(value):
    with pytest.raises(ValueError):
        parse_sync_pairing(value)
