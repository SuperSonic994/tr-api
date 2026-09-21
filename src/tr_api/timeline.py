"""Combined timeline read over a single WebSocket.

Trade Republic splits the account timeline across two WS topics:

  - ``timelineTransactions`` (``tr_api.transactions.TOPIC``)
  - ``timelineActivityLog`` (``tr_api.activity_log.TOPIC``)

Both must be read back-to-back on **one** ``TrWebSocket`` connection.
Opening a second connection for the second topic can return incomplete or
empty results. Downstream apps (dashboard, documents) follow this pattern;
``fetch_combined`` exposes it for CLI and library callers.

No economic interpretation, deduplication, or event rewriting — raw items
from each topic are returned separately.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from . import activity_log, transactions
from .client import TrClient
from .protocol import TrWebSocket

MAX_PAGES_DEFAULT = transactions.MAX_PAGES_DEFAULT


async def _paginate_topic_on_ws(
    ws: TrWebSocket,
    topic: str,
    *,
    since: datetime | None,
    max_pages: int,
) -> list[dict[str, Any]]:
    """Walk one timeline topic on an already-open WS."""
    out: list[dict[str, Any]] = []
    cursor: str | None = None
    pages = 0
    cutoff = (
        since.replace(tzinfo=timezone.utc)
        if (since and since.tzinfo is None)
        else since
    )

    while True:
        payload: dict[str, Any] = {"type": topic}
        if cursor is not None:
            payload["after"] = cursor
        page = await ws.fetch_one(payload)
        items = page.get("items") or []
        stop = False
        for it in items:
            if cutoff is not None:
                ts = _parse_iso(it.get("timestamp") or it.get("eventTime"))
                if ts is not None and ts < cutoff:
                    stop = True
                    break
            out.append(it)
        if stop:
            return out
        cursor = (page.get("cursors") or {}).get("after")
        pages += 1
        if cursor is None or pages >= max_pages:
            return out


def _parse_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


async def _fetch_combined_async(
    cookie_jar: Any,
    *,
    since: datetime | None = None,
    max_pages: int = MAX_PAGES_DEFAULT,
) -> dict[str, Any]:
    async with TrWebSocket(cookie_jar) as ws:
        tx_items = await _paginate_topic_on_ws(
            ws,
            transactions.TOPIC,
            since=since,
            max_pages=max_pages,
        )
        al_items = await _paginate_topic_on_ws(
            ws,
            activity_log.TOPIC,
            since=since,
            max_pages=max_pages,
        )
    return {
        "transactions": {"count": len(tx_items), "items": tx_items},
        "activity_log": {"count": len(al_items), "items": al_items},
        "combined_count": len(tx_items) + len(al_items),
    }


def fetch_combined(
    client: TrClient,
    *,
    since: datetime | None = None,
    max_pages: int = MAX_PAGES_DEFAULT,
) -> dict[str, Any]:
    """Fetch both timeline topics on one WebSocket connection."""
    return asyncio.run(
        _fetch_combined_async(
            client.session.cookies,
            since=since,
            max_pages=max_pages,
        )
    )
