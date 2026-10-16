"""Pydantic request/response models for the portfolio API."""
from __future__ import annotations

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict, Field, model_validator


class SearchResult(BaseModel):
    instrument_id: str
    symbol: str
    name: str


class InstrumentIn(BaseModel):
    """Body for add-to-watchlist / add-to-portfolio requests."""

    instrument_id: str = Field(min_length=1)


class WatchlistItemOut(BaseModel):
    instrument_id: str
    symbol: str = ""
    name: str = ""
    added_at: datetime

    # Latest quote from market-data's INSTRUMENT_PRICES (null until polled);
    # day change is vs the previous close.
    ltp: float | None = None
    ltp_at: datetime | None = None
    prev_close: float | None = None
    day_change_pct: float | None = None

    # Same shape as PortfolioItemOut.fundamentals (see fundamentals.py).
    fundamentals: dict | None = None


class WatchlistOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    updated_at: datetime
    items: list[WatchlistItemOut] = []


class WatchlistNameIn(BaseModel):
    """Body for creating or renaming a watchlist; surrounding spaces are trimmed."""

    model_config = ConfigDict(str_strip_whitespace=True)

    name: str = Field(min_length=1, max_length=120)


# -- portfolio ----------------------------------------------------------------
class TrancheBase(BaseModel):
    buy_date: date
    quantity: int = Field(gt=0, description="Number of shares bought")
    price: float = Field(gt=0, description="Buy price per share")


class TrancheIn(TrancheBase):
    @model_validator(mode="after")
    def _not_in_future(self) -> "TrancheIn":
        if self.buy_date > date.today():
            raise ValueError("buy_date cannot be in the future")
        return self


class TrancheOut(TrancheBase):
    id: int
    label: str  # T1, T2, ... by buy_date


class PortfolioAddIn(InstrumentIn):
    """Body for add-to-portfolio. Tranches/notes are optional so the old
    `{instrument_id}`-only body keeps working."""

    tranches: list[TrancheIn] = []
    notes: str | None = None


class PortfolioUpdateIn(BaseModel):
    """Partial update: only fields present in the body are changed. Send
    `sell_date: null, sell_price: null` to un-sell a position."""

    notes: str | None = None
    sell_date: date | None = None
    sell_price: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _sell_fields_together(self) -> "PortfolioUpdateIn":
        if (self.sell_date is None) != (self.sell_price is None):
            raise ValueError("sell_date and sell_price must both be set or both be null")
        if self.sell_date is not None and self.sell_date > date.today():
            raise ValueError("sell_date cannot be in the future")
        return self


class PortfolioItemOut(BaseModel):
    instrument_id: str
    symbol: str = ""
    name: str = ""
    added_at: datetime
    notes: str | None = None
    tranches: list[TrancheOut] = []

    # Position (null until at least one tranche exists)
    quantity: int = 0
    invested: float | None = None
    avg_cost: float | None = None
    first_buy_date: date | None = None
    max_holding_date: date | None = None  # T1 + 9 months

    # Market price from market-data's INSTRUMENT_PRICES (null if not polled yet)
    ltp: float | None = None
    ltp_at: datetime | None = None

    # Exit + P&L. `status` is "sold" once sell_date/sell_price are set, else
    # "open". Exactly one of realized/unrealized is populated.
    status: str = "open"
    sell_date: date | None = None
    sell_price: float | None = None
    realized_pnl: float | None = None
    unrealized_pnl: float | None = None
    pnl_pct: float | None = None

    # Trailing SL (open positions only). peak_price = max(highest daily close
    # since T1, current LTP); trailing_sl = peak × (1 − trailing_sl_pct/100).
    # profit_exit fires when ≥ profit_exit_min_tranches tranches, the SL is
    # above avg cost (exit books a profit) and LTP ≤ SL.
    peak_price: float | None = None
    peak_date: date | None = None
    peak_refreshed_on: date | None = None
    trailing_sl: float | None = None
    sl_locks_profit: bool = False
    profit_exit: bool = False
    trailing_sl_pct: float
    profit_exit_min_tranches: int

    # Cached Upstox fundamentals: sector, profile, ratios, shareholding (see fundamentals.py).
    fundamentals: dict | None = None


class IndexQuoteOut(BaseModel):
    """A market index quote, e.g. NIFTY 50 (see routers/market.py)."""

    name: str
    instrument_id: str
    ltp: float
    prev_close: float | None = None
    change: float | None = None
    change_pct: float | None = None
    as_of: datetime
