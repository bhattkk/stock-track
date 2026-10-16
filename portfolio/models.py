"""ORM models: watchlists and the portfolio.

A watchlist is a named group of instruments (create-on-first-add, per the
API contract). The portfolio is a single implicit list of instruments the
user holds — no separate "portfolios" table, just portfolio_items. Each
portfolio item is bought in any number of tranches (one portfolio_tranches
row each) and, optionally, sold in full (sell_date/sell_price).
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Date, DateTime, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base

# Prices are stored exactly in Postgres but handed to Python as floats — the
# API serves them as JSON numbers and does only display-level arithmetic.
Price = Numeric(14, 4, asdecimal=False)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Watchlist(Base):
    __tablename__ = "watchlists"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utcnow, onupdate=_utcnow
    )

    items: Mapped[list["WatchlistItem"]] = relationship(
        back_populates="watchlist", cascade="all, delete-orphan"
    )


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "instrument_id", name="uq_watchlist_instrument"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    instrument_id: Mapped[str] = mapped_column(String(64), index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)

    watchlist: Mapped[Watchlist] = relationship(back_populates="items")


class PortfolioItem(Base):
    __tablename__ = "portfolio_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    added_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # why I bought it
    sell_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sell_price: Mapped[float | None] = mapped_column(Price, nullable=True)

    # Highest daily close since T1, cached from Upstox history (see peaks.py).
    peak_close: Mapped[float | None] = mapped_column(Price, nullable=True)
    peak_close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    peak_refreshed_on: Mapped[date | None] = mapped_column(Date, nullable=True)

    tranches: Mapped[list["PortfolioTranche"]] = relationship(
        back_populates="item",
        cascade="all, delete-orphan",
        order_by="(PortfolioTranche.buy_date, PortfolioTranche.id)",
    )


class PortfolioTranche(Base):
    """One buy of a portfolio item. Ordered by buy_date, the first is T1."""

    __tablename__ = "portfolio_tranches"

    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_item_id: Mapped[int] = mapped_column(
        ForeignKey("portfolio_items.id", ondelete="CASCADE"), index=True
    )
    buy_date: Mapped[date] = mapped_column(Date)
    quantity: Mapped[int] = mapped_column(Integer)
    price: Mapped[float] = mapped_column(Price)

    item: Mapped[PortfolioItem] = relationship(back_populates="tranches")


class InstrumentFundamentals(Base):
    """Cached Upstox fundamentals for one instrument (see fundamentals.py).

    Keyed by instrument, not portfolio position, so watchlists can reuse it.
    `data` is the normalized dict from upstox_fundamentals.fetch_fundamentals.
    """

    __tablename__ = "instrument_fundamentals"

    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    data: Mapped[dict] = mapped_column(JSONB)
    fetched_on: Mapped[date] = mapped_column(Date)
