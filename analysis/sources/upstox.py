"""Upstox daily candles (read-only Analytics token).

Same endpoint the portfolio service uses for peak closes, written again here
(not imported) so this service has no code dependency on it:

    GET /v3/historical-candle/{instrument_key}/days/1/{to_date}/{from_date}

Response rows are [timestamp, open, high, low, close, volume, open_interest],
newest first. Works for equities ("NSE_EQ|<ISIN>") and indices
("NSE_INDEX|Nifty 50", whose volume is 0).
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import date, datetime
from urllib.parse import quote

import requests

log = logging.getLogger("analysis.upstox")

BASE_URL = "https://api.upstox.com"
RETRIES = 3


class UpstoxError(Exception):
    pass


class Upstox:
    """Throttled client: consecutive requests are spaced `delay` seconds apart
    (Upstox rate limits are per user, shared with the other services)."""

    def __init__(self, token: str, delay: float = 1.1):
        if not token:
            raise UpstoxError("UPSTOX_ACCESS_TOKEN is empty; set it in the repo .env")
        self.delay = delay
        self._s = requests.Session()
        self._s.headers.update({"Authorization": f"Bearer {token}", "Accept": "application/json"})
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        """Sleep just long enough that requests are at least `delay` apart."""
        with self._lock:
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def daily_candles(self, instrument_id: str, since: date, until: date) -> list[dict]:
        """Daily bars from `since` to `until` (inclusive), oldest first.

        Returns [{"d": date, "o", "h", "l", "c", "v"}]. An empty list means
        Upstox has no bars in the range (e.g. a suspended stock). Raises
        UpstoxError after RETRIES failed attempts; 429 (rate limited) and 5xx
        are retried with backoff, other 4xx are not.
        """
        path = (f"/v3/historical-candle/{quote(instrument_id, safe='')}/days/1/"
                f"{until.isoformat()}/{since.isoformat()}")
        last_exc: Exception | None = None
        for attempt in range(RETRIES):
            self._throttle()
            try:
                resp = self._s.get(BASE_URL + path, timeout=20)
                if resp.status_code == 429 or resp.status_code >= 500:
                    raise UpstoxError(f"HTTP {resp.status_code}")
                if resp.status_code >= 400:
                    raise UpstoxError(f"HTTP {resp.status_code}: {resp.text[:200]}")  # not retryable
                rows = (resp.json().get("data") or {}).get("candles") or []
                return sorted((_bar(r) for r in rows), key=lambda b: b["d"])
            except UpstoxError as exc:
                last_exc = exc
                if not str(exc).startswith(("HTTP 429", "HTTP 5")):
                    break
            except (requests.RequestException, ValueError) as exc:
                last_exc = exc
            time.sleep(5 * 2 ** attempt)  # 5 s, 10 s, 20 s — rate-limit windows are coarse
        raise UpstoxError(f"candles for {instrument_id} failed: {last_exc}")


def _bar(row: list) -> dict:
    """[ts, o, h, l, c, v, oi] -> {"d", "o", "h", "l", "c", "v"} (ts like 2026-10-23T00:00:00+05:30)."""
    return {"d": datetime.fromisoformat(row[0]).date(), "o": float(row[1]), "h": float(row[2]),
            "l": float(row[3]), "c": float(row[4]), "v": int(row[5] or 0)}
