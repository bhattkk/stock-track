"""Return ratios: ROE and ROCE (criterion 8).

Both use the average of opening and closing balance sheets for the year
(closing only when the opening one isn't stored).
"""
from __future__ import annotations

from indicators.base import Period, StockContext, Value, div, indicator, rnd


def _avg_balance(ctx: StockContext, year: Period, metric: str) -> float | None:
    closing = year.get(metric)
    prev = ctx.fin.balance_at(year.end.replace(year=year.end.year - 1))
    opening = prev.get(metric) if prev else None
    if closing is None:
        return None
    return closing if opening is None else (opening + closing) / 2


def _roe(ctx: StockContext, year: Period) -> float | None:
    equity = _avg_balance(ctx, year, "shareholders_equity")
    if equity is None or equity <= 0:
        return None
    r = div(year.get("profit"), equity)
    return None if r is None else r * 100


def _roce(ctx: StockContext, year: Period) -> float | None:
    capital = _avg_balance(ctx, year, "capital_employed")
    if capital is None or capital <= 0:
        return None
    r = div(year.get("ebit"), capital)
    return None if r is None else r * 100


def _yearly(ctx: StockContext, fn, n: int) -> dict[str, float | None]:
    return {f"FY{p.end.year % 100:02d}": rnd(fn(ctx, p)) for p in reversed(ctx.fin.fy(n))}


@indicator("roe_latest", "ROE (latest FY)", "returns", unit="%", higher_is_better=True,
           description="Profit attributable to shareholders / average shareholders' equity.")
def roe_latest(ctx: StockContext):
    years = ctx.fin.fy(1)
    return Value(rnd(_roe(ctx, years[0])), {"roe": _yearly(ctx, _roe, 5)}) if years else None


@indicator("roce_latest", "ROCE (latest FY)", "returns", unit="%", higher_is_better=True,
           description="EBIT (PBT + interest, excl. exceptional items) / average capital employed (equity + borrowings).")
def roce_latest(ctx: StockContext):
    years = ctx.fin.fy(1)
    return Value(rnd(_roce(ctx, years[0])), {"roce": _yearly(ctx, _roce, 5)}) if years else None


@indicator("roe_3y_avg", "ROE (3Y avg)", "returns", unit="%", params={"years": 3}, higher_is_better=True,
           description="Average of yearly ROE over the last three full years.")
def roe_avg(ctx: StockContext, years: int):
    return _average(ctx, _roe, years, "roe")


@indicator("roce_3y_avg", "ROCE (3Y avg)", "returns", unit="%", params={"years": 3}, higher_is_better=True,
           description="Average of yearly ROCE over the last three full years.")
def roce_avg(ctx: StockContext, years: int):
    return _average(ctx, _roce, years, "roce")


def _average(ctx: StockContext, fn, years: int, label: str):
    vals = [fn(ctx, p) for p in ctx.fin.fy(years)]
    if len(vals) < years or any(v is None for v in vals):
        return None
    return Value(rnd(sum(vals) / len(vals)), {label: _yearly(ctx, fn, years)})
