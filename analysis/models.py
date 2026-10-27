"""Postgres tables owned by the analysis service (all prefixed `analysis_`).

analysis_candles  daily OHLCV per instrument — a durable, incremental store so
                  each weekly run only downloads the bars added since the last one
analysis_runs     one row per pipeline run: progress, funnel counts, rules hash
analysis_results  one row per (run, stock): pass/fail, every rule's outcome,
                  and the metrics behind them — what the Analysis tab shows
"""
from __future__ import annotations

from datetime import date, datetime, timezone

from sqlalchemy import JSON, BigInteger, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from db import Base

# JSONB on Postgres, plain JSON elsewhere (tests run on SQLite).
Json = JSON().with_variant(JSONB(), "postgresql")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Candle(Base):
    """One daily bar. Index instruments (the NIFTY 50 benchmark) have volume 0."""

    __tablename__ = "analysis_candles"

    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    d: Mapped[date] = mapped_column(Date, primary_key=True)
    o: Mapped[float] = mapped_column(Float)
    h: Mapped[float] = mapped_column(Float)
    l: Mapped[float] = mapped_column(Float)  # noqa: E741  (OHLC naming)
    c: Mapped[float] = mapped_column(Float)
    v: Mapped[int] = mapped_column(BigInteger)


class Run(Base):
    """One pipeline run (scheduled or "Run now")."""

    __tablename__ = "analysis_runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Updated with every progress write. A "running" run whose heartbeat has
    # gone stale belongs to a process that died (see pipeline.mark_interrupted_runs).
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | done | failed | interrupted
    stage: Mapped[str] = mapped_column(String(32), default="starting")  # starting | syncing | evaluating | done
    total: Mapped[int] = mapped_column(Integer, default=0)
    done: Mapped[int] = mapped_column(Integer, default=0)
    # Funnel: how many stocks cleared each layer, e.g. {"universe": 2708, "tradable": 1500, ...}
    counts: Mapped[dict | None] = mapped_column(Json, nullable=True)
    criteria_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


class Result(Base):
    """One stock's stage-1 outcome in one run.

    `failed_at` is the first layer the stock failed — "data" (no/too little
    history), "tradability", "trend", "setup" or "flag" — or None when it passed.
    `rules` holds every rule's outcome (see technicals/rules.py) so a rejected
    stock can always explain itself.
    """

    __tablename__ = "analysis_results"

    run_id: Mapped[int] = mapped_column(ForeignKey("analysis_runs.id", ondelete="CASCADE"), primary_key=True)
    instrument_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32))
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    passed: Mapped[bool] = mapped_column(Boolean, default=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    failed_at: Mapped[str | None] = mapped_column(String(16), nullable=True)
    rules: Mapped[list | None] = mapped_column(Json, nullable=True)
    metrics: Mapped[dict | None] = mapped_column(Json, nullable=True)
    as_of: Mapped[date | None] = mapped_column(Date, nullable=True)  # date of the last candle used

    __table_args__ = (
        Index("ix_analysis_results_run_passed_score", "run_id", "passed", "score"),
        Index("ix_analysis_results_symbol", "symbol"),
    )
