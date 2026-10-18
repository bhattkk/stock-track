"""ORM models for the screener. Every table is prefixed `screener_`.

Three layers (see README.md):

  screener_stocks / screener_filings / screener_facts   raw data from NSE
  screener_indicator_values                              computed per stock, one row per indicator
  screener_screens                                       saved filter sets

plus screener_refresh_runs, the progress log of on-demand refreshes.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import Boolean, Date, DateTime, Float, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Stock(Base):
    """One NSE-listed equity (from NSE's EQUITY_L.csv) plus its latest close."""

    __tablename__ = "screener_stocks"

    # "NSE_EQ|<ISIN>" — the same key the portfolio and market-data use.
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))
    isin: Mapped[str] = mapped_column(String(16))
    series: Mapped[str] = mapped_column(String(4))
    listed: Mapped[bool] = mapped_column(Boolean, default=True)  # false once it drops off EQUITY_L
    # Banks / NBFCs / insurers file a different results format; most
    # indicators don't apply to them and come out null.
    is_financial: Mapped[bool] = mapped_column(Boolean, default=False)
    # Which set of results the indicators use: "consolidated" when the
    # company files them, else "standalone".
    basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    close: Mapped[float | None] = mapped_column(Float, nullable=True)
    close_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Last time this stock's filing list was fetched from NSE, and why it failed if it did.
    results_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class Filing(Base):
    """One XBRL results filing (a quarter's results, standalone or consolidated)."""

    __tablename__ = "screener_filings"

    id: Mapped[int] = mapped_column(primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(64), index=True)
    url: Mapped[str] = mapped_column(String(300), unique=True)
    period_end: Mapped[date] = mapped_column(Date)
    basis: Mapped[str] = mapped_column(String(16))
    filed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending | parsed | failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    parsed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Fact(Base):
    """One number from a filing, in long format so new line items need no migration.

    period_type: Q (quarter), H (half year), M9 (nine months), FY (full
    year) for P&L / cash-flow durations; I (instant) for balance-sheet
    values as of period_end. Values are in rupees (per-share items as filed).
    """

    __tablename__ = "screener_facts"

    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    basis: Mapped[str] = mapped_column(String(16), primary_key=True)
    period_type: Mapped[str] = mapped_column(String(4), primary_key=True)
    period_end: Mapped[date] = mapped_column(Date, primary_key=True)
    metric: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[float] = mapped_column(Float)
    filing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)


class IndicatorValue(Base):
    """A computed indicator for one stock (see indicators/). Screens filter on these."""

    __tablename__ = "screener_indicator_values"

    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    key: Mapped[str] = mapped_column(String(64), primary_key=True, index=True)
    value_num: Mapped[float | None] = mapped_column(Float, nullable=True)
    value_text: Mapped[str | None] = mapped_column(String(120), nullable=True)
    detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # inputs behind the value, shown in the UI
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)


class Screen(Base):
    """A saved screen: a rule tree plus the columns / sort to show."""

    __tablename__ = "screener_screens"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    rules: Mapped[dict] = mapped_column(JSONB)
    columns: Mapped[list] = mapped_column(JSONB, default=list)
    sort: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow, onupdate=_utcnow)


class RefreshRun(Base):
    """One on-demand refresh (UI button or CLI) and its progress."""

    __tablename__ = "screener_refresh_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed | interrupted
    stage: Mapped[str] = mapped_column(String(32), default="starting")
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, default=dict)
