from __future__ import annotations

from deepseek_cli.desktop.main import (
    _finish_smoke_test,
    _schedule_smoke_exit,
    _smoke_test_enabled,
)


class _CloseTarget:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def close(self) -> None:
        self.events.append("close")


class _QuitTarget:
    def __init__(self, events: list[str]) -> None:
        self.events = events

    def quit(self) -> None:
        self.events.append("quit")


def test_smoke_test_enabled_accepts_current_and_legacy_flags(monkeypatch):
    monkeypatch.delenv("BANVERSE_SMOKE_TEST", raising=False)
    monkeypatch.delenv("DEEPSEEK_CHAT_SMOKE_TEST", raising=False)
    assert not _smoke_test_enabled()

    monkeypatch.setenv("BANVERSE_SMOKE_TEST", "1")
    assert _smoke_test_enabled()

    monkeypatch.delenv("BANVERSE_SMOKE_TEST")
    monkeypatch.setenv("DEEPSEEK_CHAT_SMOKE_TEST", "1")
    assert _smoke_test_enabled()


def test_finish_smoke_closes_window_before_quitting_application():
    events: list[str] = []

    _finish_smoke_test(_QuitTarget(events), _CloseTarget(events))

    assert events == ["close", "quit"]


def test_smoke_exit_timer_is_owned_single_shot(qapp):
    events: list[str] = []

    timer = _schedule_smoke_exit(
        qapp,
        _CloseTarget(events),
        delay_ms=60_000,
    )

    assert timer.parent() is qapp
    assert timer.isSingleShot()
    assert timer.isActive()
    timer.stop()
    assert events == []
