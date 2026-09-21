"""Offline tests for combined timeline read (library + CLI)."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

import pytest

from tr_api import activity_log, timeline, transactions
from tr_api.cli import _build_parser, main
from tr_api.profiles import Profile


class FakeTrWebSocket:
    """Records one WS lifetime and every fetch_one payload on that instance."""

    constructed: list["FakeTrWebSocket"] = []

    def __init__(self, cookie_jar: object) -> None:
        self.cookie_jar = cookie_jar
        self.fetch_calls: list[dict[str, Any]] = []
        FakeTrWebSocket.constructed.append(self)

    async def __aenter__(self) -> FakeTrWebSocket:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None

    async def fetch_one(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        self.fetch_calls.append(dict(payload))
        topic = payload["type"]
        after = payload.get("after")

        if topic == transactions.TOPIC:
            if after == "tx-page-2":
                return {
                    "items": [{"id": "tx-2", "timestamp": "2026-02-02T00:00:00.000Z"}],
                    "cursors": {"after": None},
                }
            return {
                "items": [{"id": "tx-1", "timestamp": "2026-02-01T00:00:00.000Z"}],
                "cursors": {"after": "tx-page-2"},
            }

        if topic == activity_log.TOPIC:
            if after == "al-page-2":
                return {
                    "items": [{"id": "al-2", "timestamp": "2026-02-02T00:00:00.000Z"}],
                    "cursors": {"after": None},
                }
            return {
                "items": [{"id": "al-1", "timestamp": "2026-02-01T00:00:00.000Z"}],
                "cursors": {"after": "al-page-2"},
            }

        raise AssertionError(f"unexpected topic {topic!r}")


class FakeTrWebSocketWithSince(FakeTrWebSocket):
    async def fetch_one(self, payload: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        self.fetch_calls.append(dict(payload))
        topic = payload["type"]
        if topic == transactions.TOPIC:
            return {
                "items": [
                    {"id": "tx-new", "timestamp": "2026-02-01T00:00:00.000Z"},
                    {"id": "tx-old", "timestamp": "2025-12-01T00:00:00.000Z"},
                ],
                "cursors": {"after": None},
            }
        if topic == activity_log.TOPIC:
            return {
                "items": [
                    {"id": "al-new", "timestamp": "2026-02-01T00:00:00.000Z"},
                    {"id": "al-old", "timestamp": "2025-11-01T00:00:00.000Z"},
                ],
                "cursors": {"after": None},
            }
        raise AssertionError(f"unexpected topic {topic!r}")


class FakeClient:
    class session:
        cookies = object()


@pytest.fixture(autouse=True)
def reset_fake_ws() -> None:
    FakeTrWebSocket.constructed.clear()
    yield
    FakeTrWebSocket.constructed.clear()


@pytest.fixture
def fake_profile() -> Profile:
    return Profile(phone="+490000000000")


@pytest.fixture
def cli_mocks(monkeypatch: pytest.MonkeyPatch, fake_profile: Profile) -> dict[str, Any]:
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


def test_parser_recognizes_timeline() -> None:
    args = _build_parser().parse_args(["timeline"])
    assert args.cmd == "timeline"
    assert args.func.__name__ == "cmd_timeline"


def test_regression_must_not_open_two_websockets(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fails if fetch_combined opens separate WS connections per topic."""
    monkeypatch.setattr("tr_api.timeline.TrWebSocket", FakeTrWebSocket)

    timeline.fetch_combined(FakeClient(), max_pages=1)

    assert len(FakeTrWebSocket.constructed) == 1, (
        "Combined timeline must use exactly one TrWebSocket; "
        "two separate connections can return incomplete activity_log data."
    )


def test_both_topics_use_same_ws_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_api.timeline.TrWebSocket", FakeTrWebSocket)

    timeline.fetch_combined(FakeClient(), max_pages=1)
    ws = FakeTrWebSocket.constructed[0]

    topics = [call["type"] for call in ws.fetch_calls]
    assert topics == [transactions.TOPIC, activity_log.TOPIC]


def test_topic_order_transactions_then_activity_log(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_api.timeline.TrWebSocket", FakeTrWebSocket)

    timeline.fetch_combined(FakeClient(), max_pages=1)
    ws = FakeTrWebSocket.constructed[0]

    first_tx = next(i for i, call in enumerate(ws.fetch_calls) if call["type"] == transactions.TOPIC)
    first_al = next(i for i, call in enumerate(ws.fetch_calls) if call["type"] == activity_log.TOPIC)
    assert first_tx < first_al


def test_max_pages_applies_to_both_topics(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_api.timeline.TrWebSocket", FakeTrWebSocket)

    result = timeline.fetch_combined(FakeClient(), max_pages=1)
    ws = FakeTrWebSocket.constructed[0]

    tx_calls = [c for c in ws.fetch_calls if c["type"] == transactions.TOPIC]
    al_calls = [c for c in ws.fetch_calls if c["type"] == activity_log.TOPIC]
    assert len(tx_calls) == 1
    assert len(al_calls) == 1
    assert result["transactions"] == {"count": 1, "items": [{"id": "tx-1", "timestamp": "2026-02-01T00:00:00.000Z"}]}
    assert result["activity_log"] == {"count": 1, "items": [{"id": "al-1", "timestamp": "2026-02-01T00:00:00.000Z"}]}
    assert result["combined_count"] == 2


def test_since_applies_to_both_streams(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("tr_api.timeline.TrWebSocket", FakeTrWebSocketWithSince)

    cutoff = datetime(2026, 1, 1, tzinfo=timezone.utc)
    result = timeline.fetch_combined(FakeClient(), since=cutoff, max_pages=5)

    assert result["transactions"]["items"] == [
        {"id": "tx-new", "timestamp": "2026-02-01T00:00:00.000Z"},
    ]
    assert result["activity_log"]["items"] == [
        {"id": "al-new", "timestamp": "2026-02-01T00:00:00.000Z"},
    ]
    assert result["combined_count"] == 2


def test_cli_json_envelope(
    monkeypatch: pytest.MonkeyPatch,
    cli_mocks: dict[str, Any],
    capsys: pytest.CaptureFixture[str],
) -> None:
    sample = {
        "transactions": {"count": 1, "items": [{"id": "tx-cli"}]},
        "activity_log": {"count": 1, "items": [{"id": "al-cli"}]},
        "combined_count": 2,
    }

    def fake_fetch_combined(
        client: object,
        *,
        since: datetime | None = None,
        max_pages: int = 200,
    ) -> dict[str, Any]:
        cli_mocks["fetch_combined"] = (since, max_pages)
        return sample

    monkeypatch.setattr("tr_api.cli.timeline.fetch_combined", fake_fetch_combined)

    rc = main(["--json", "timeline", "--since", "2026-01-01", "--max-pages", "5"])
    out = json.loads(capsys.readouterr().out.strip())

    assert rc == 0
    assert out == {"ok": True, "data": sample}
    since, max_pages = cli_mocks["fetch_combined"]
    assert since.year == 2026 and since.month == 1 and since.day == 1
    assert max_pages == 5
