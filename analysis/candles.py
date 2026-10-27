"""Keep `analysis_candles` up to date, incrementally.

The first sync for a stock downloads `history_days` of daily bars; later
syncs only ask for bars after the newest one already stored (≈5 per week).
Bars older than the window are pruned so the table doesn't grow forever.

`load()` turns the stored bars into a pandas DataFrame for the indicators.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from config import ist_today
from models import Candle
from sources.upstox import Upstox

log = logging.getLogger("analysis.candles")


def last_stored_date(db: Session, instrument_id: str) -> date | None:
    """Date of the newest stored bar for this instrument, or None if there are none."""
    return db.scalar(select(func.max(Candle.d)).where(Candle.instrument_id == instrument_id))


def sync(db: Session, upstox: Upstox, instrument_id: str, history_days: int, today: date | None = None) -> int:
    """Fetch any missing recent bars for one instrument and store them.

    Re-fetches from the last stored date (not the day after) so that bar is
    overwritten with its final values if it was stored mid-session.
    Returns the number of bars written. Commits.
    """
    today = today or ist_today()
    window_start = today - timedelta(days=history_days)
    last = last_stored_date(db, instrument_id)
    since = max(last, window_start) if last else window_start
    bars = upstox.daily_candles(instrument_id, since, today)
    if bars:
        rows = [{"instrument_id": instrument_id, **b} for b in bars]
        stmt = pg_insert(Candle).values(rows)
        db.execute(stmt.on_conflict_do_update(
            index_elements=[Candle.instrument_id, Candle.d],
            set_={k: stmt.excluded[k] for k in ("o", "h", "l", "c", "v")},
        ))
    # Prune bars that fell out of the window (keeps the table ~history_days per stock).
    db.execute(delete(Candle).where(Candle.instrument_id == instrument_id, Candle.d < window_start))
    db.commit()
    return len(bars)


def load(db: Session, instrument_id: str) -> pd.DataFrame:
    """All stored bars for one instrument as a DataFrame indexed by date, oldest first.

    Columns: open, high, low, close, volume. Empty DataFrame if none stored.
    """
    rows = db.execute(select(Candle.d, Candle.o, Candle.h, Candle.l, Candle.c, Candle.v)
                      .where(Candle.instrument_id == instrument_id).order_by(Candle.d)).all()
    df = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume"])
    return df.set_index(pd.to_datetime(df["date"])).drop(columns="date")
