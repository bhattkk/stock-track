"""Price-based numbers: market cap, P/E, P/B (close from NSE's bhavcopy)."""
from __future__ import annotations

from indicators.base import StockContext, Value, crores, div, indicator, rnd


def _market_cap(ctx: StockContext) -> float | None:
    shares = ctx.fin.latest_shares()
    return None if ctx.close is None or not shares else ctx.close * shares


@indicator("price", "Price", "valuation", unit="₹", inputs={"prices"}, applies_to_financials=True,
           description="Latest NSE closing price at the last refresh.")
def price(ctx: StockContext):
    return ctx.close


@indicator("market_cap", "Market cap", "valuation", unit="₹ Cr", inputs={"prices", "financials"}, applies_to_financials=True,
           description="Close × shares outstanding (paid-up capital / face value from the latest filing).")
def market_cap(ctx: StockContext):
    return crores(_market_cap(ctx))


@indicator("pe", "P/E (TTM)", "valuation", unit="x", inputs={"prices", "financials"}, higher_is_better=False,
           description="Market cap / profit of the last four quarters (latest FY if quarters are missing). Null when loss-making.")
def pe(ctx: StockContext):
    mcap = _market_cap(ctx)
    earnings, _ = ctx.fin.ttm("profit")
    basis = "TTM"
    if earnings is None and ctx.fin.fy(1):
        earnings, basis = ctx.fin.fy(1)[0].get("profit"), "latest FY"
    if mcap is None or earnings is None or earnings <= 0:
        return None
    return Value(rnd(mcap / earnings, 1), {"earnings_cr": crores(earnings), "earnings_basis": basis})


@indicator("pb", "P/B", "valuation", unit="x", inputs={"prices", "financials"}, higher_is_better=False,
           description="Market cap / shareholders' equity from the latest balance sheet.")
def pb(ctx: StockContext):
    bs = ctx.fin.latest_balance()
    equity = bs.get("shareholders_equity") if bs else None
    mcap = _market_cap(ctx)
    if mcap is None or not equity or equity <= 0:
        return None
    return rnd(div(mcap, equity))
