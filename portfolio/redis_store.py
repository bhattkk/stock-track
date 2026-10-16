"""Redis access for the portfolio service.

Shares the key schema owned by market-data (see market-data/store.py):

INSTRUMENTS             hash  instrument_id -> json(instrument)  (read-only
                               here; market-data owns writing it)
SUBSCRIBED_INSTRUMENTS  set   instrument_ids market-data should poll —
                               this service owns writing it: an instrument
                               is subscribed while it's referenced by at
                               least one watchlist or the portfolio.
INSTRUMENT_PRICES       hash  instrument_id -> json(quote) (read-only here;
                               market-data writes LTP quotes every sweep)
"""
from __future__ import annotations

import json

import redis


class RedisStore:
    INSTRUMENTS_KEY = "INSTRUMENTS"
    SUBSCRIBED_KEY = "SUBSCRIBED_INSTRUMENTS"
    PRICES_KEY = "INSTRUMENT_PRICES"

    def __init__(self, url: str):
        self._r = redis.Redis.from_url(url, decode_responses=True)

    def ping(self) -> bool:
        return bool(self._r.ping())

    # -- search -----------------------------------------------------------
    def search_instruments(self, query: str, limit: int) -> list[dict]:
        """Substring-match symbol/name (case-insensitive); symbol matches first.

        Loads the whole INSTRUMENTS hash and filters in Python — fine at the
        scale of one exchange's equity master. Results are ordered so a
        symbol *starting with* the query ranks above one that merely
        contains it, and symbol matches rank above name-only matches.
        """
        needle = query.strip().lower()
        if not needle:
            return []

        symbol_prefix: list[dict] = []
        symbol_contains: list[dict] = []
        name_match: list[dict] = []

        for raw in self._r.hvals(self.INSTRUMENTS_KEY):
            ins = json.loads(raw)
            symbol = (ins.get("symbol") or "").lower()
            name = (ins.get("name") or "").lower()
            if symbol.startswith(needle):
                symbol_prefix.append(ins)
            elif needle in symbol:
                symbol_contains.append(ins)
            elif needle in name:
                name_match.append(ins)

            if len(symbol_prefix) >= limit:
                break

        ordered = symbol_prefix + symbol_contains + name_match
        return ordered[:limit]

    def get_instrument(self, instrument_id: str) -> dict | None:
        raw = self._r.hget(self.INSTRUMENTS_KEY, instrument_id)
        return json.loads(raw) if raw else None

    def get_instruments(self, instrument_ids: list[str]) -> dict[str, dict]:
        """Batch lookup, e.g. to resolve symbol/name for a watchlist's items."""
        if not instrument_ids:
            return {}
        raws = self._r.hmget(self.INSTRUMENTS_KEY, instrument_ids)
        return {
            instrument_id: json.loads(raw)
            for instrument_id, raw in zip(instrument_ids, raws)
            if raw
        }

    # -- prices ---------------------------------------------------------------
    def get_prices(self, instrument_ids: list[str]) -> dict[str, dict]:
        """Batch lookup of the latest quotes ({ltp, cp, ts, ...}) market-data saved."""
        if not instrument_ids:
            return {}
        raws = self._r.hmget(self.PRICES_KEY, instrument_ids)
        return {
            instrument_id: json.loads(raw)
            for instrument_id, raw in zip(instrument_ids, raws)
            if raw
        }

    # -- subscriptions ------------------------------------------------------
    def subscribe(self, instrument_id: str) -> None:
        self._r.sadd(self.SUBSCRIBED_KEY, instrument_id)

    def unsubscribe(self, instrument_id: str) -> None:
        self._r.srem(self.SUBSCRIBED_KEY, instrument_id)
