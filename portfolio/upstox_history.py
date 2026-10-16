"""Upstox historical daily candles — just enough to find a peak close."""
from __future__ import annotations

import logging
from datetime import date, datetime
from urllib.parse import quote

from upstox_client import upstox_get

log = logging.getLogger("portfolio.upstox_history")

CANDLES_PATH = "/v3/historical-candle/{key}/days/1/{to_date}/{from_date}"
CLOSE = 4  # candle = [timestamp, open, high, low, close, volume, oi]


def highest_close_since(instrument_id: str, since: date, token: str) -> tuple[float, date] | None:
    """Highest daily close (and its date) from ``since`` through today.

    Returns None on any fetch error or when there are no candles, so callers
    can keep whatever peak they had.
    """
    path = CANDLES_PATH.format(
        key=quote(instrument_id, safe=""), to_date=date.today().isoformat(), from_date=since.isoformat()
    )
    data = upstox_get(path, token)
    candles = data.get("candles") if isinstance(data, dict) else None
    if not candles:
        if data is not None:
            log.warning("No daily candles for %s since %s", instrument_id, since)
        return None

    best = max(candles, key=lambda c: c[CLOSE])
    peak_date = datetime.fromisoformat(best[0]).date()
    log.debug("%s: %d candles since %s, peak close %s on %s", instrument_id, len(candles), since, best[CLOSE], peak_date)
    return float(best[CLOSE]), peak_date
