"""Balance-sheet strength: debt/equity and friends (criterion 4).

Uses the most recent balance sheet on file — the half-year one when it's
newer than the last year-end.
"""
from __future__ import annotations

from indicators.base import StockContext, Value, crores, div, indicator, rnd

# Interest coverage is capped so debt-free companies (≈0 interest) still
# pass a "coverage > N" filter instead of dropping out as null.
MAX_COVERAGE = 100.0


def _balance_detail(bs) -> dict:
    return {
        "as_of": bs.end.isoformat(),
        "debt_cr": crores(bs.get("debt")),
        "equity_cr": crores(bs.get("equity")),
        "cash_and_investments_cr": crores(bs.get("cash_like")),
    }


@indicator("debt_to_equity", "Debt / Equity", "leverage", unit="x", higher_is_better=False,
           description="Total borrowings / total equity, from the latest balance sheet.")
def debt_to_equity(ctx: StockContext):
    bs = ctx.fin.latest_balance()
    if not bs or not bs.get("equity") or bs.get("equity") <= 0:
        return None
    return Value(rnd(div(bs.get("debt") or 0, bs.get("equity"))), _balance_detail(bs))


@indicator("net_debt_to_equity", "Net debt / Equity", "leverage", unit="x", higher_is_better=False,
           description="(Borrowings − cash, bank balances & current investments) / equity. Negative = net cash.")
def net_debt_to_equity(ctx: StockContext):
    bs = ctx.fin.latest_balance()
    if not bs or not bs.get("equity") or bs.get("equity") <= 0:
        return None
    net = (bs.get("debt") or 0) - (bs.get("cash_like") or 0)
    return Value(rnd(net / bs.get("equity")), _balance_detail(bs))


@indicator("interest_coverage", "Interest coverage", "leverage", unit="x", higher_is_better=True,
           description=f"EBIT / finance costs for the latest FY (capped at {MAX_COVERAGE:g}x for near debt-free companies).")
def interest_coverage(ctx: StockContext):
    years = ctx.fin.fy(1)
    if not years:
        return None
    ebit, interest = years[0].get("ebit"), years[0].get("finance_costs") or 0
    if ebit is None:
        return None
    cov = MAX_COVERAGE if interest <= 0 else min(ebit / interest, MAX_COVERAGE)
    return Value(rnd(cov), {"ebit_cr": crores(ebit), "finance_costs_cr": crores(interest)})


@indicator("current_ratio", "Current ratio", "leverage", unit="x", higher_is_better=True,
           description="Current assets / current liabilities, latest balance sheet.")
def current_ratio(ctx: StockContext):
    bs = ctx.fin.latest_balance()
    return rnd(div(bs.get("current_assets"), bs.get("current_liabilities"))) if bs else None
