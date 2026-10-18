"""Growth: revenue and profit CAGRs."""
from __future__ import annotations

from indicators.base import StockContext, Value, cagr, crores, indicator, rnd


def _cagr(ctx: StockContext, metric: str, years: int):
    latest = ctx.fin.fy(1)
    if not latest:
        return None
    base = ctx.fin.fy_ending(latest[0].end.year - years)
    if not base:
        return None
    start, end = base.get(metric), latest[0].get(metric)
    g = cagr(start, end, years)
    if g is None:
        return None
    label = lambda p: f"FY{p.end.year % 100:02d}"  # noqa: E731
    return Value(rnd(g), {f"{metric}_cr": {label(base): crores(start), label(latest[0]): crores(end)}})


@indicator("sales_cagr_5y", "Sales CAGR (5Y)", "growth", unit="%", params={"years": 5}, higher_is_better=True)
@indicator("sales_cagr_3y", "Sales CAGR (3Y)", "growth", unit="%", params={"years": 3}, higher_is_better=True)
def sales_cagr(ctx: StockContext, years: int):
    return _cagr(ctx, "revenue", years)


@indicator("profit_cagr_5y", "Profit CAGR (5Y)", "growth", unit="%", params={"years": 5}, higher_is_better=True)
@indicator("profit_cagr_3y", "Profit CAGR (3Y)", "growth", unit="%", params={"years": 3}, higher_is_better=True)
def profit_cagr(ctx: StockContext, years: int):
    return _cagr(ctx, "profit", years)


@indicator("sales_growth_ttm", "Sales growth (TTM YoY)", "growth", unit="%", higher_is_better=True,
           description="Last four quarters' revenue vs the four before.")
def sales_growth_ttm(ctx: StockContext):
    cur, _ = ctx.fin.ttm("revenue")
    prev, _ = ctx.fin.ttm("revenue", offset=4)
    if cur is None or not prev or prev <= 0:
        return None
    return Value(rnd((cur / prev - 1) * 100), {"revenue_ttm_cr": crores(cur), "revenue_prior_ttm_cr": crores(prev)})
