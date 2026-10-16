"""AddToPortfolio / RemoveFromPortfolio, plus tranches, exit and notes.

Same subscribe/unsubscribe behavior as watchlists: adding an instrument to
the portfolio subscribes it in Redis; removing it unsubscribes it only if no
watchlist still references it either.

A portfolio item is bought in any number of tranches (T1, T2, ... ordered
by buy date) and optionally sold in full via sell_date/sell_price. Position
and P&L figures are derived on read, never stored:

  avg_cost         = sum(qty * price) / sum(qty)
  unrealized_pnl   = (ltp - avg_cost) * qty            while open
  realized_pnl     = (sell_price - avg_cost) * qty     once sold
  pnl_pct          = (exit_or_ltp / avg_cost - 1) * 100
  max_holding_date = T1 buy date + MAX_HOLDING_MONTHS

Trailing stop-loss for open positions (peak close cached by peaks.py):

  peak_price  = max(highest daily close since T1, ltp)
  trailing_sl = peak_price * (1 - TRAILING_SL_PCT / 100)
  profit_exit = >= PROFIT_EXIT_MIN_TRANCHES tranches
                and trailing_sl > avg_cost and ltp <= trailing_sl
"""
from __future__ import annotations

import calendar
from datetime import date, datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

import crud
from db import get_db
from deps import get_redis_store
from models import InstrumentFundamentals, PortfolioItem
from fundamentals import as_output, refresh_all_fundamentals, refresh_instrument_fundamentals
from peaks import refresh_all_peaks, refresh_item_peak
from redis_store import RedisStore
from schemas import (
    PortfolioAddIn,
    PortfolioItemOut,
    PortfolioUpdateIn,
    TrancheIn,
    TrancheOut,
)

router = APIRouter(prefix="/portfolio", tags=["portfolio"])

MAX_HOLDING_MONTHS = 9
TRAILING_SL_PCT = 11
PROFIT_EXIT_MIN_TRANCHES = 3


def _add_months(d: date, months: int) -> date:
    """Calendar-month add, clamping the day (e.g. 31 May + 1 month -> 30 Jun)."""
    month_index = d.month - 1 + months
    year, month = d.year + month_index // 12, month_index % 12 + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def _round(value: float | None, ndigits: int = 2) -> float | None:
    return None if value is None else round(value, ndigits)


def _to_out(
    item: PortfolioItem, instrument: dict, quote: dict | None, fundamentals: InstrumentFundamentals | None = None
) -> PortfolioItemOut:
    tranches = sorted(item.tranches, key=lambda t: (t.buy_date, t.id))
    out = PortfolioItemOut(
        instrument_id=item.instrument_id,
        symbol=instrument.get("symbol", ""),
        name=instrument.get("name", ""),
        added_at=item.added_at,
        notes=item.notes,
        tranches=[
            TrancheOut(id=t.id, label=f"T{i}", buy_date=t.buy_date, quantity=t.quantity, price=t.price)
            for i, t in enumerate(tranches, start=1)
        ],
        sell_date=item.sell_date,
        sell_price=item.sell_price,
        status="sold" if item.sell_date is not None else "open",
        trailing_sl_pct=TRAILING_SL_PCT,
        profit_exit_min_tranches=PROFIT_EXIT_MIN_TRANCHES,
        fundamentals=as_output(fundamentals),
    )

    if quote and quote.get("ltp"):
        out.ltp = quote["ltp"]
        if quote.get("ts"):
            out.ltp_at = datetime.fromtimestamp(quote["ts"], tz=timezone.utc)

    if not tranches:
        return out

    qty = sum(t.quantity for t in tranches)
    invested = sum(t.quantity * t.price for t in tranches)
    avg_cost = invested / qty
    out.quantity = qty
    out.invested = _round(invested)
    out.avg_cost = _round(avg_cost, 4)
    out.first_buy_date = tranches[0].buy_date
    out.max_holding_date = _add_months(tranches[0].buy_date, MAX_HOLDING_MONTHS)

    if out.status == "sold":
        out.realized_pnl = _round((item.sell_price - avg_cost) * qty)
        out.pnl_pct = _round((item.sell_price / avg_cost - 1) * 100)
    else:
        if out.ltp is not None:
            out.unrealized_pnl = _round((out.ltp - avg_cost) * qty)
            out.pnl_pct = _round((out.ltp / avg_cost - 1) * 100)
        _apply_trailing_sl(out, item, avg_cost, len(tranches))
    return out


def _apply_trailing_sl(out: PortfolioItemOut, item: PortfolioItem, avg_cost: float, n_tranches: int) -> None:
    """Fill peak/SL/profit_exit on an open position's output."""
    out.peak_refreshed_on = item.peak_refreshed_on
    candidates = []
    if item.peak_close is not None:
        candidates.append((item.peak_close, item.peak_close_date))
    if out.ltp is not None:
        candidates.append((out.ltp, date.today()))
    if not candidates:
        return

    out.peak_price, out.peak_date = max(candidates, key=lambda c: c[0])
    out.trailing_sl = _round(out.peak_price * (1 - TRAILING_SL_PCT / 100))
    out.sl_locks_profit = out.trailing_sl > avg_cost
    out.profit_exit = (
        n_tranches >= PROFIT_EXIT_MIN_TRANCHES
        and out.sl_locks_profit
        and out.ltp is not None
        and out.ltp <= out.trailing_sl
    )


def _render(items: list[PortfolioItem], store: RedisStore, db: Session) -> list[PortfolioItemOut]:
    ids = [item.instrument_id for item in items]
    instruments = store.get_instruments(ids)
    quotes = store.get_prices(ids)
    fundamentals = crud.get_fundamentals(db, ids)
    return [
        _to_out(i, instruments.get(i.instrument_id, {}), quotes.get(i.instrument_id), fundamentals.get(i.instrument_id))
        for i in items
    ]


def _get_item_or_404(db: Session, instrument_id: str) -> PortfolioItem:
    item = crud.get_portfolio_item(db, instrument_id)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Instrument '{instrument_id}' not in portfolio")
    return item


def _check_sell_after_buys(item: PortfolioItem) -> None:
    if item.sell_date is None:
        return
    if not item.tranches:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "Cannot sell a position with no tranches")
    last_buy = max(t.buy_date for t in item.tranches)
    if item.sell_date < last_buy:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"sell_date {item.sell_date} is before the last buy date {last_buy}",
        )


@router.get("", response_model=list[PortfolioItemOut])
def list_portfolio(
    db: Session = Depends(get_db), store: RedisStore = Depends(get_redis_store)
) -> list[PortfolioItemOut]:
    return _render(crud.list_portfolio_items(db), store, db)


@router.post("/peaks/refresh", response_model=list[PortfolioItemOut])
def refresh_peaks(
    db: Session = Depends(get_db), store: RedisStore = Depends(get_redis_store)
) -> list[PortfolioItemOut]:
    """Re-fetch every open position's peak close from Upstox now."""
    refresh_all_peaks()
    return _render(crud.list_portfolio_items(db), store, db)


@router.post("/fundamentals/refresh", response_model=list[PortfolioItemOut])
def refresh_fundamentals(
    db: Session = Depends(get_db), store: RedisStore = Depends(get_redis_store)
) -> list[PortfolioItemOut]:
    """Re-fetch every portfolio and watchlist instrument's fundamentals from Upstox now."""
    refresh_all_fundamentals(force=True)
    return _render(crud.list_portfolio_items(db), store, db)


@router.get("/{instrument_id}", response_model=PortfolioItemOut)
def get_portfolio_item(
    instrument_id: str, db: Session = Depends(get_db), store: RedisStore = Depends(get_redis_store)
) -> PortfolioItemOut:
    return _render([_get_item_or_404(db, instrument_id)], store, db)[0]


@router.post("", response_model=PortfolioItemOut, status_code=status.HTTP_201_CREATED)
def add_to_portfolio(
    body: PortfolioAddIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> PortfolioItemOut:
    """Add an instrument, optionally with its first tranche(s) and notes.

    409 if it's already in the portfolio and tranches were sent (add further
    tranches via POST /portfolio/{instrument_id}/tranches instead); a bare
    re-add stays an idempotent no-op.
    """
    if store.get_instrument(body.instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown instrument_id '{body.instrument_id}'")

    existing = crud.get_portfolio_item(db, body.instrument_id)
    if existing is not None and body.tranches:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"'{body.instrument_id}' is already in the portfolio; add tranches to it instead",
        )

    item = crud.add_portfolio_item(db, body.instrument_id)
    if body.notes is not None:
        item.notes = body.notes
    for t in body.tranches:
        crud.add_tranche(db, item, t.buy_date, t.quantity, t.price)
    if body.tranches:
        refresh_item_peak(db, item)
    if crud.get_fundamentals(db, [body.instrument_id]) == {}:
        refresh_instrument_fundamentals(db, body.instrument_id, store)
    db.commit()

    store.subscribe(body.instrument_id)
    return _render([item], store, db)[0]


@router.patch("/{instrument_id}", response_model=PortfolioItemOut)
def update_portfolio_item(
    instrument_id: str,
    body: PortfolioUpdateIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> PortfolioItemOut:
    """Edit notes and/or record (or clear) the exit. Only fields sent are changed."""
    item = _get_item_or_404(db, instrument_id)
    if "notes" in body.model_fields_set:
        item.notes = body.notes
    if "sell_date" in body.model_fields_set:
        item.sell_date = body.sell_date
        item.sell_price = body.sell_price
        _check_sell_after_buys(item)
        refresh_item_peak(db, item)  # no-op when sold; refetches on un-sell
    db.commit()
    return _render([item], store, db)[0]


@router.post("/{instrument_id}/tranches", response_model=PortfolioItemOut, status_code=status.HTTP_201_CREATED)
def add_tranche(
    instrument_id: str,
    body: TrancheIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> PortfolioItemOut:
    item = _get_item_or_404(db, instrument_id)
    crud.add_tranche(db, item, body.buy_date, body.quantity, body.price)
    _check_sell_after_buys(item)
    refresh_item_peak(db, item)
    db.commit()
    return _render([item], store, db)[0]


@router.put("/{instrument_id}/tranches/{tranche_id}", response_model=PortfolioItemOut)
def edit_tranche(
    instrument_id: str,
    tranche_id: int,
    body: TrancheIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> PortfolioItemOut:
    item = _get_item_or_404(db, instrument_id)
    tranche = crud.get_tranche(db, item, tranche_id)
    if tranche is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tranche {tranche_id} not found for '{instrument_id}'")
    tranche.buy_date, tranche.quantity, tranche.price = body.buy_date, body.quantity, body.price
    _check_sell_after_buys(item)
    refresh_item_peak(db, item)
    db.commit()
    return _render([item], store, db)[0]


@router.delete("/{instrument_id}/tranches/{tranche_id}", response_model=PortfolioItemOut)
def remove_tranche(
    instrument_id: str,
    tranche_id: int,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> PortfolioItemOut:
    item = _get_item_or_404(db, instrument_id)
    tranche = crud.get_tranche(db, item, tranche_id)
    if tranche is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Tranche {tranche_id} not found for '{instrument_id}'")
    item.tranches.remove(tranche)
    _check_sell_after_buys(item)
    refresh_item_peak(db, item)
    db.commit()
    return _render([item], store, db)[0]


@router.delete("/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def remove_from_portfolio(
    instrument_id: str,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> None:
    removed = crud.remove_portfolio_item(db, instrument_id)
    if not removed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Instrument '{instrument_id}' not in portfolio")
    db.commit()

    if not crud.is_instrument_referenced(db, instrument_id):
        store.unsubscribe(instrument_id)
