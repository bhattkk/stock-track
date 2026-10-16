"""Watchlist CRUD + AddToWatchlist / RemoveFromWatchlist.

Adding an instrument subscribes it in Redis (SUBSCRIBED_INSTRUMENTS) so
market-data starts polling it. Removing unsubscribes it only if nothing else
— another watchlist, or the portfolio — still references it.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from datetime import datetime, timezone

import crud
from db import get_db
from deps import get_redis_store
from fundamentals import as_output, refresh_instrument_fundamentals
from models import Watchlist
from redis_store import RedisStore
from schemas import InstrumentIn, WatchlistItemOut, WatchlistNameIn, WatchlistOut

router = APIRouter(prefix="/watchlists", tags=["watchlists"])


def _item_out(item, instrument: dict, quote: dict | None, fundamentals) -> WatchlistItemOut:
    out = WatchlistItemOut(
        instrument_id=item.instrument_id,
        symbol=instrument.get("symbol", ""),
        name=instrument.get("name", ""),
        added_at=item.added_at,
        fundamentals=as_output(fundamentals),
    )
    if quote and quote.get("ltp"):
        out.ltp = quote["ltp"]
        if quote.get("ts"):
            out.ltp_at = datetime.fromtimestamp(quote["ts"], tz=timezone.utc)
        if quote.get("cp"):
            out.prev_close = quote["cp"]
            out.day_change_pct = round((out.ltp / out.prev_close - 1) * 100, 2)
    return out


def _to_out(watchlist: Watchlist, store: RedisStore, db: Session) -> WatchlistOut:
    instrument_ids = [item.instrument_id for item in watchlist.items]
    resolved = store.get_instruments(instrument_ids)
    quotes = store.get_prices(instrument_ids)
    fundamentals = crud.get_fundamentals(db, instrument_ids)
    items = [
        _item_out(item, resolved.get(item.instrument_id, {}), quotes.get(item.instrument_id), fundamentals.get(item.instrument_id))
        for item in watchlist.items
    ]
    return WatchlistOut(
        id=watchlist.id,
        name=watchlist.name,
        created_at=watchlist.created_at,
        updated_at=watchlist.updated_at,
        items=items,
    )


def _get_watchlist_or_404(db: Session, name: str) -> Watchlist:
    watchlist = crud.get_watchlist_by_name(db, name)
    if watchlist is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Watchlist '{name}' not found")
    return watchlist


@router.get("", response_model=list[WatchlistOut])
def list_watchlists(
    db: Session = Depends(get_db), store: RedisStore = Depends(get_redis_store)
) -> list[WatchlistOut]:
    return [_to_out(w, store, db) for w in crud.list_watchlists(db)]


@router.post("", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def create_watchlist(
    body: WatchlistNameIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> WatchlistOut:
    """Create an empty watchlist. 409 if the name is taken."""
    if crud.get_watchlist_by_name(db, body.name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Watchlist '{body.name}' already exists")
    watchlist = crud.get_or_create_watchlist(db, body.name)
    db.commit()
    return _to_out(watchlist, store, db)


@router.get("/{name}", response_model=WatchlistOut)
def get_watchlist(
    name: str, db: Session = Depends(get_db), store: RedisStore = Depends(get_redis_store)
) -> WatchlistOut:
    return _to_out(_get_watchlist_or_404(db, name), store, db)


@router.patch("/{name}", response_model=WatchlistOut)
def edit_watchlist(
    name: str,
    body: WatchlistNameIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> WatchlistOut:
    """Rename a watchlist."""
    watchlist = _get_watchlist_or_404(db, name)
    if crud.get_watchlist_by_name(db, body.name) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Watchlist '{body.name}' already exists")
    crud.rename_watchlist(db, watchlist, body.name)
    db.commit()
    return _to_out(watchlist, store, db)


@router.post("/{name}/items", response_model=WatchlistOut, status_code=status.HTTP_201_CREATED)
def add_to_watchlist(
    name: str,
    body: InstrumentIn,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> WatchlistOut:
    """Add an instrument to the watchlist named ``name``, creating it first if needed."""
    if store.get_instrument(body.instrument_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Unknown instrument_id '{body.instrument_id}'")

    watchlist = crud.get_or_create_watchlist(db, name)
    crud.add_watchlist_item(db, watchlist, body.instrument_id)
    if not crud.get_fundamentals(db, [body.instrument_id]):
        refresh_instrument_fundamentals(db, body.instrument_id, store)
    db.commit()

    store.subscribe(body.instrument_id)
    return _to_out(watchlist, store, db)


@router.delete("/{name}/items/{instrument_id}", status_code=status.HTTP_204_NO_CONTENT, response_model=None)
def remove_from_watchlist(
    name: str,
    instrument_id: str,
    db: Session = Depends(get_db),
    store: RedisStore = Depends(get_redis_store),
) -> None:
    watchlist = _get_watchlist_or_404(db, name)
    removed = crud.remove_watchlist_item(db, watchlist, instrument_id)
    if not removed:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Instrument '{instrument_id}' not in watchlist '{name}'",
        )
    db.commit()

    if not crud.is_instrument_referenced(db, instrument_id):
        store.unsubscribe(instrument_id)
