"""Redis persistence layer and key schema for the market-data service.

Key schema
----------
INSTRUMENTS             hash  instrument_id -> json(instrument)  (equity
                               master, refreshed daily; symbol/name/exchange
                               etc. for future search)
SUBSCRIBED_INSTRUMENTS  set   instrument_ids currently subscribed, written
                               by another service
INSTRUMENT_PRICES       hash  instrument_id -> json(quote)
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Iterable

import redis

from providers.base import Instrument, Quote


class Store:
    INSTRUMENTS_KEY = "INSTRUMENTS"
    SUBSCRIBED_KEY = "SUBSCRIBED_INSTRUMENTS"
    PRICES_KEY = "INSTRUMENT_PRICES"

    def __init__(self, url: str):
        self._r = redis.Redis.from_url(url, decode_responses=True)

    # -- connectivity -------------------------------------------------------
    def ping(self) -> bool:
        return bool(self._r.ping())

    # -- instrument master ---------------------------------------------------
    def save_instruments(self, instruments: Iterable[Instrument]) -> int:
        instruments = list(instruments)
        pipe = self._r.pipeline()
        pipe.delete(self.INSTRUMENTS_KEY)
        if instruments:
            mapping = {ins.instrument_id: json.dumps(asdict(ins)) for ins in instruments}
            pipe.hset(self.INSTRUMENTS_KEY, mapping=mapping)
        pipe.execute()
        return len(instruments)

    def instrument_count(self) -> int:
        return self._r.hlen(self.INSTRUMENTS_KEY)

    def get_instrument(self, instrument_id: str) -> dict | None:
        raw = self._r.hget(self.INSTRUMENTS_KEY, instrument_id)
        return json.loads(raw) if raw else None

    # -- subscriptions --------------------------------------------------------
    def get_subscriptions(self) -> list[str]:
        return list(self._r.smembers(self.SUBSCRIBED_KEY))

    # -- prices ---------------------------------------------------------------
    def save_prices(self, quotes: dict[str, Quote]) -> None:
        if not quotes:
            return
        mapping = {
            instrument_id: json.dumps(asdict(q)) for instrument_id, q in quotes.items()
        }
        self._r.hset(self.PRICES_KEY, mapping=mapping)

    def get_price(self, instrument_id: str) -> dict | None:
        raw = self._r.hget(self.PRICES_KEY, instrument_id)
        return json.loads(raw) if raw else None
