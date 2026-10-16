"""Database operations for watchlists and the portfolio.

Kept as plain functions (not a repository/service class hierarchy) since the
whole domain is three small tables — routers call these directly.
"""
from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from models import InstrumentFundamentals, PortfolioItem, PortfolioTranche, Watchlist, WatchlistItem


# -- watchlists ---------------------------------------------------------------
def list_watchlists(db: Session) -> list[Watchlist]:
    return list(db.scalars(select(Watchlist).order_by(Watchlist.name)))


def get_watchlist_by_name(db: Session, name: str) -> Watchlist | None:
    return db.scalar(select(Watchlist).where(Watchlist.name == name))


def get_or_create_watchlist(db: Session, name: str) -> Watchlist:
    watchlist = get_watchlist_by_name(db, name)
    if watchlist is None:
        watchlist = Watchlist(name=name)
        db.add(watchlist)
        db.flush()  # assigns watchlist.id without ending the transaction
    return watchlist


def rename_watchlist(db: Session, watchlist: Watchlist, new_name: str) -> Watchlist:
    watchlist.name = new_name
    db.flush()
    return watchlist


def add_watchlist_item(db: Session, watchlist: Watchlist, instrument_id: str) -> WatchlistItem:
    existing = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.instrument_id == instrument_id,
        )
    )
    if existing is not None:
        return existing
    item = WatchlistItem(watchlist_id=watchlist.id, instrument_id=instrument_id)
    db.add(item)
    db.flush()
    return item


def remove_watchlist_item(db: Session, watchlist: Watchlist, instrument_id: str) -> bool:
    item = db.scalar(
        select(WatchlistItem).where(
            WatchlistItem.watchlist_id == watchlist.id,
            WatchlistItem.instrument_id == instrument_id,
        )
    )
    if item is None:
        return False
    db.delete(item)
    db.flush()
    return True


# -- portfolio ------------------------------------------------------------
def list_portfolio_items(db: Session) -> list[PortfolioItem]:
    return list(db.scalars(select(PortfolioItem).order_by(PortfolioItem.added_at)))


def add_portfolio_item(db: Session, instrument_id: str) -> PortfolioItem:
    existing = db.scalar(select(PortfolioItem).where(PortfolioItem.instrument_id == instrument_id))
    if existing is not None:
        return existing
    item = PortfolioItem(instrument_id=instrument_id)
    db.add(item)
    db.flush()
    return item


def get_portfolio_item(db: Session, instrument_id: str) -> PortfolioItem | None:
    return db.scalar(select(PortfolioItem).where(PortfolioItem.instrument_id == instrument_id))


def add_tranche(db: Session, item: PortfolioItem, buy_date, quantity: int, price: float) -> PortfolioTranche:
    tranche = PortfolioTranche(buy_date=buy_date, quantity=quantity, price=price)
    item.tranches.append(tranche)
    db.flush()
    return tranche


def get_tranche(db: Session, item: PortfolioItem, tranche_id: int) -> PortfolioTranche | None:
    return db.scalar(
        select(PortfolioTranche).where(
            PortfolioTranche.id == tranche_id,
            PortfolioTranche.portfolio_item_id == item.id,
        )
    )


def remove_portfolio_item(db: Session, instrument_id: str) -> bool:
    item = db.scalar(select(PortfolioItem).where(PortfolioItem.instrument_id == instrument_id))
    if item is None:
        return False
    db.delete(item)
    db.flush()
    return True


# -- fundamentals -----------------------------------------------------------
def list_tracked_instrument_ids(db: Session) -> list[str]:
    """Every instrument in the portfolio or any watchlist, de-duplicated, sorted."""
    portfolio = db.scalars(select(PortfolioItem.instrument_id))
    watchlists = db.scalars(select(WatchlistItem.instrument_id))
    return sorted(set(portfolio) | set(watchlists))


def get_fundamentals(db: Session, instrument_ids: list[str]) -> dict[str, InstrumentFundamentals]:
    if not instrument_ids:
        return {}
    rows = db.scalars(select(InstrumentFundamentals).where(InstrumentFundamentals.instrument_id.in_(instrument_ids)))
    return {row.instrument_id: row for row in rows}


def upsert_fundamentals(db: Session, instrument_id: str, data: dict, fetched_on) -> InstrumentFundamentals:
    row = db.get(InstrumentFundamentals, instrument_id)
    if row is None:
        row = InstrumentFundamentals(instrument_id=instrument_id)
        db.add(row)
    row.data, row.fetched_on = data, fetched_on
    db.flush()
    return row


# -- subscriptions ----------------------------------------------------------
def is_instrument_referenced(db: Session, instrument_id: str) -> bool:
    """True if any watchlist or the portfolio still holds this instrument.

    Used after a remove to decide whether market-data should stop polling it
    — an instrument stays subscribed as long as *something* still needs it.
    """
    in_watchlist = db.scalar(
        select(WatchlistItem.id).where(WatchlistItem.instrument_id == instrument_id)
    )
    if in_watchlist is not None:
        return True
    in_portfolio = db.scalar(
        select(PortfolioItem.id).where(PortfolioItem.instrument_id == instrument_id)
    )
    return in_portfolio is not None
