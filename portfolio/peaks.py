"""Peak-close cache for the trailing stop-loss.

Each open position stores the highest daily close since its T1 buy date
(peak_close/peak_close_date), fetched from Upstox history. It's refreshed:

- daily after market close, and once at startup (scheduled in main.py);
- right after a position's tranches change, since the T1 date may move.

At read time routers/portfolio.py combines it with the live LTP, so the
peak also moves during the trading day.
"""
from __future__ import annotations

import logging
from datetime import date

from sqlalchemy.orm import Session

import crud
from config import settings
from db import SessionLocal
from models import PortfolioItem
from upstox_history import highest_close_since

log = logging.getLogger("portfolio.peaks")


def refresh_item_peak(db: Session, item: PortfolioItem) -> bool:
    """Re-fetch the peak close for one position; keeps the old value on failure.

    Sold positions and positions without tranches are skipped. Returns True
    if the peak was updated. Flushes but doesn't commit — the caller owns the
    transaction.
    """
    if item.sell_date is not None or not item.tranches:
        return False
    first_buy = min(t.buy_date for t in item.tranches)
    result = highest_close_since(item.instrument_id, first_buy, settings.upstox_access_token)
    if result is None:
        return False
    item.peak_close, item.peak_close_date = result
    item.peak_refreshed_on = date.today()
    db.flush()
    return True


def refresh_all_peaks() -> int:
    """Scheduler entry point: refresh every open position. Returns how many updated."""
    with SessionLocal() as db:
        updated = sum(refresh_item_peak(db, item) for item in crud.list_portfolio_items(db))
        db.commit()
    log.info("Peak refresh done: %d position(s) updated", updated)
    return updated
