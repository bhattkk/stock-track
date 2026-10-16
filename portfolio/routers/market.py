"""Live market index quotes (NIFTY 50) for the front end's header ticker.

Fetched straight from Upstox's LTP API rather than via market-data's Redis
prices, because market-data sweeps only every POLL_INTERVAL_SECONDS and the
header refreshes every 2 s. A short in-process cache means several open tabs
still make at most one Upstox call per CACHE_SECONDS (~40/min at 1.5 s, under
Upstox's 500/min). If Upstox fails, the last good quote is served for up to
STALE_SECONDS before the endpoint returns 502.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, status

from config import settings
from schemas import IndexQuoteOut
from upstox_client import upstox_get

router = APIRouter(prefix="/market", tags=["market"])

INDICES = [("NIFTY 50", "NSE_INDEX|Nifty 50")]
CACHE_SECONDS = 1.5
STALE_SECONDS = 60

_lock = threading.Lock()
_cache: dict = {"at": 0.0, "quotes": None}


def _fetch() -> list[IndexQuoteOut] | None:
    keys = ",".join(key for _, key in INDICES)
    data = upstox_get(f"/v3/market-quote/ltp?instrument_key={quote(keys, safe=',')}", settings.upstox_access_token)
    if not isinstance(data, dict):
        return None
    # Upstox keys rows by "NSE_INDEX:Nifty 50"; match on instrument_token instead.
    rows = {row.get("instrument_token"): row for row in data.values()}
    now = datetime.now(timezone.utc)
    quotes = []
    for name, key in INDICES:
        row = rows.get(key)
        if not row or not row.get("last_price"):
            continue
        ltp, cp = float(row["last_price"]), float(row.get("cp") or 0) or None
        quotes.append(IndexQuoteOut(
            name=name,
            instrument_id=key,
            ltp=ltp,
            prev_close=cp,
            change=round(ltp - cp, 2) if cp else None,
            change_pct=round((ltp / cp - 1) * 100, 2) if cp else None,
            as_of=now,
        ))
    return quotes or None


@router.get("/indices", response_model=list[IndexQuoteOut])
def indices() -> list[IndexQuoteOut]:
    with _lock:
        age = time.monotonic() - _cache["at"]
        if _cache["quotes"] is not None and age < CACHE_SECONDS:
            return _cache["quotes"]
        fresh = _fetch()
        if fresh is not None:
            _cache.update(at=time.monotonic(), quotes=fresh)
            return fresh
        if _cache["quotes"] is not None and age < STALE_SECONDS:
            return _cache["quotes"]
    raise HTTPException(status.HTTP_502_BAD_GATEWAY, "Index quote unavailable from Upstox")
