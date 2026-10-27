"""Read-only access to the instrument master market-data keeps in Redis.

INSTRUMENTS  hash  instrument_id -> json(instrument)  (owned by market-data,
             refreshed daily; this service only reads it)

The analysis universe is every company share in that hash: instrument_type
"EQ" *and* an ISIN starting with "INE". Upstox also tags ETFs and other fund
units as EQ; their ISINs start with "INF" (mutual-fund units) and they are
not companies, so they're left out. ~2,350 stocks.
"""
from __future__ import annotations

import json

import redis


class RedisStore:
    INSTRUMENTS_KEY = "INSTRUMENTS"

    def __init__(self, url: str):
        self._r = redis.Redis.from_url(url, decode_responses=True)

    def ping(self) -> bool:
        """True when Redis answers; used by /health."""
        return bool(self._r.ping())

    def equity_universe(self) -> list[dict]:
        """Every NSE company share (EQ with an INE… ISIN) in the instrument master, sorted by symbol.

        Each item is the instrument dict market-data stored:
        {instrument_id, symbol, name, exchange, segment, instrument_type, isin}.
        """
        out = []
        for raw in self._r.hvals(self.INSTRUMENTS_KEY):
            ins = json.loads(raw)
            if ins.get("instrument_type") == "EQ" and (ins.get("isin") or "").startswith("INE"):
                out.append(ins)
        return sorted(out, key=lambda i: i.get("symbol") or "")
