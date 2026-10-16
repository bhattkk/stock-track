"""Fundamentals cache for portfolio and watchlist instruments.

Fetched from Upstox (upstox_fundamentals.py) into instrument_fundamentals,
one row per instrument. Refreshed at startup (skipping anything already
fetched today), daily at 06:00 IST after Upstox's 05:00 data refresh, and
right after an instrument is added to the portfolio or a watchlist. A failed
fetch keeps the previous row.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

import crud
from config import settings
from db import SessionLocal
from deps import get_redis_store
from models import InstrumentFundamentals
from redis_store import RedisStore
from upstox_fundamentals import fetch_fundamentals

log = logging.getLogger("portfolio.fundamentals")


def as_output(row: InstrumentFundamentals | None) -> dict | None:
    """API shape for a cached row: the normalized data plus `fetched_on`."""
    return {**row.data, "fetched_on": row.fetched_on.isoformat()} if row else None


def _isin_for(instrument_id: str, store: RedisStore) -> str:
    """ISIN from the instrument master; for NSE_EQ keys it's also the part after '|'."""
    instrument = store.get_instrument(instrument_id) or {}
    return instrument.get("isin") or instrument_id.split("|")[-1]


def refresh_instrument_fundamentals(db: Session, instrument_id: str, store: RedisStore) -> bool:
    """Fetch and upsert one instrument's fundamentals. Returns True on success.

    Flushes but doesn't commit — the caller owns the transaction.
    """
    data = fetch_fundamentals(_isin_for(instrument_id, store), settings.upstox_access_token)
    if data is None:
        return False
    crud.upsert_fundamentals(db, instrument_id, data, date.today())
    return True


def refresh_all_fundamentals(force: bool = False) -> int:
    """Refresh every portfolio (active and sold) and watchlist instrument. Returns how many updated.

    Unless ``force``, skips instruments already fetched today so restarts
    don't re-hit the API.
    """
    store = get_redis_store()
    with SessionLocal() as db:
        ids = crud.list_tracked_instrument_ids(db)
        cached = crud.get_fundamentals(db, ids)
        todo = [i for i in ids if force or i not in cached or cached[i].fetched_on != date.today()]
        updated = sum(refresh_instrument_fundamentals(db, i, store) for i in todo)
        db.commit()
    log.info("Fundamentals refresh done: %d/%d instrument(s) updated (%d already fresh)", updated, len(todo), len(ids) - len(todo))
    return updated
