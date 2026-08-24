from __future__ import annotations

import json

import pytest

from deepseek_cli.sync_protocol import (
    DEFAULT_SYNC_URL,
    normalize_sync_username,
    parse_sync_pairing,
    sync_username_key,
    validate_sync_password,
)


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


def test_sync_username_and_password_validation():
    assert normalize_sync_username("  小满_2026  ") == "小满_2026"
    assert normalize_sync_username("BanVerse.User") == "BanVerse.User"
    assert sync_username_key("BanVerse.User") == "banverse.user"
    assert validate_sync_password("正确的密码-2026") == "正确的密码-2026"

    for username in ("ab", "-username", "username-", "user name", "用户🙂"):
        with pytest.raises(ValueError):
            normalize_sync_username(username)
    for password in ("short", "        ", "x" * 129):
        with pytest.raises(ValueError):
            validate_sync_password(password)
