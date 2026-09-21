"""Offline CLI tests for the activity-log subcommand."""
from __future__ import annotations

import json
from typing import Any

import pytest

from tr_api.cli import _build_parser, main
from tr_api.profiles import Profile


@pytest.fixture
def fake_profile() -> Profile:
    return Profile(phone="+490000000000")


@pytest.fixture
def cli_mocks(monkeypatch: pytest.MonkeyPatch, fake_profile: Profile) -> dict[str, Any]:
    """Patch profile resolution and TrClient so handlers never touch disk/network."""
    calls: dict[str, Any] = {}

    class FakeTrClient:
        def __init__(self, profile: Profile) -> None:
            calls["profile"] = profile

        def __enter__(self) -> FakeTrClient:
            return self

        def __exit__(self, *args: object) -> None:
            return None

    monkeypatch.setattr("tr_api.cli._resolve_profile", lambda phone: fake_profile)
    monkeypatch.setattr("tr_api.cli.TrClient", FakeTrClient)
    return calls


def test_parser_recognizes_activity_log() -> None:
    args = _build_parser().parse_args(["activity-log"])
    assert args.cmd == "activity-log"
    assert args.func.__name__ == "cmd_activity_log"


def test_default_calls_fetch_all(monkeypatch: pytest.MonkeyPatch, cli_mocks: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    sample = [{"id": "evt-1", "eventType": "TRADING_TRADE_EXECUTED"}]

    def fake_fetch_all(client: object, *, max_pages: int = 200) -> list[dict[str, str]]:
        cli_mocks["fetch_all"] = max_pages
        return sample

    monkeypatch.setattr("tr_api.cli.activity_log.fetch_all", fake_fetch_all)

    rc = main(["--json", "activity-log"])
    out = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert out == {"ok": True, "data": {"count": 1, "items": sample}}
    assert cli_mocks["fetch_all"] == activity_log_max_pages_default()


def test_since_calls_fetch_since(monkeypatch: pytest.MonkeyPatch, cli_mocks: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    sample = [{"id": "evt-2", "timestamp": "2026-02-01T12:00:00.000Z"}]

    def fake_fetch_since(client: object, cutoff: object, *, max_pages: int = 200) -> list[dict[str, str]]:
        cli_mocks["fetch_since"] = (cutoff, max_pages)
        return sample

    monkeypatch.setattr("tr_api.cli.activity_log.fetch_since", fake_fetch_since)

    rc = main(["--json", "activity-log", "--since", "2026-01-01"])
    out = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert out["ok"] is True
    assert out["data"]["count"] == 1
    assert out["data"]["items"] == sample
    cutoff, max_pages = cli_mocks["fetch_since"]
    assert cutoff.year == 2026 and cutoff.month == 1 and cutoff.day == 1
    assert max_pages == activity_log_max_pages_default()


def test_since_id_calls_fetch_until_id(monkeypatch: pytest.MonkeyPatch, cli_mocks: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    sample = [{"id": "evt-new"}]

    def fake_fetch_until_id(
        client: object,
        known_ids: object,
        *,
        max_pages: int = 200,
    ) -> list[dict[str, str]]:
        cli_mocks["fetch_until_id"] = (list(known_ids), max_pages)
        return sample

    monkeypatch.setattr("tr_api.cli.activity_log.fetch_until_id", fake_fetch_until_id)

    rc = main(["--json", "activity-log", "--since-id", "known-event-id"])
    out = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert out == {"ok": True, "data": {"count": 1, "items": sample}}
    known_ids, max_pages = cli_mocks["fetch_until_id"]
    assert known_ids == ["known-event-id"]
    assert max_pages == activity_log_max_pages_default()


def test_max_pages_is_forwarded(monkeypatch: pytest.MonkeyPatch, cli_mocks: dict[str, Any], capsys: pytest.CaptureFixture[str]) -> None:
    def fake_fetch_all(client: object, *, max_pages: int = 200) -> list[dict[str, str]]:
        cli_mocks["fetch_all"] = max_pages
        return []

    monkeypatch.setattr("tr_api.cli.activity_log.fetch_all", fake_fetch_all)

    rc = main(["--json", "activity-log", "--max-pages", "5"])
    out = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert out == {"ok": True, "data": {"count": 0, "items": []}}
    assert cli_mocks["fetch_all"] == 5


def activity_log_max_pages_default() -> int:
    from tr_api import activity_log

    return activity_log.MAX_PAGES_DEFAULT
