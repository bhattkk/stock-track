"""Cash conversion and capex quality (criteria 5 and 9)."""
from __future__ import annotations

from indicators.base import StockContext, Value, crores, indicator, rnd

# capex_self_funded_3y: debt may rise by at most this share of the capex
# spent over the period for the capex to count as "not debt-funded".
MAX_DEBT_SHARE_OF_CAPEX = 0.25


def _years(ctx: StockContext, n: int):
    """The last n full years — only if all n are on file and consecutive."""
    years = ctx.fin.fy(n)
    if len(years) < n or any(a.end.year - b.end.year != 1 for a, b in zip(years, years[1:])):
        return None
    return years


def _total(years, metric: str) -> float | None:
    vals = [p.get(metric) for p in years]
    return None if any(v is None for v in vals) else sum(vals)


def _by_year(years, metric: str) -> dict[str, float | None]:
    return {f"FY{p.end.year % 100:02d}": crores(p.get(metric)) for p in reversed(years)}


@indicator("cfo_to_pat_5y", "CFO / PAT (5Y)", "cash_flow", unit="x", params={"years": 5}, higher_is_better=True,
           description="Operating cash flow / net profit, summed over five years. ≥ 0.7 means profits turn into cash.")
@indicator("cfo_to_pat_3y", "CFO / PAT (3Y)", "cash_flow", unit="x", params={"years": 3}, higher_is_better=True,
           description="Operating cash flow / net profit, summed over three years.")
def cfo_to_pat(ctx: StockContext, years: int):
    ys = _years(ctx, years)
    if not ys:
        return None
    cfo, pat = _total(ys, "cfo"), _total(ys, "net_profit")
    if cfo is None or pat is None or pat <= 0:
        return None
    return Value(rnd(cfo / pat), {"cfo_cr": _by_year(ys, "cfo"), "pat_cr": _by_year(ys, "net_profit")})


@indicator("fcf_3y", "Free cash flow (3Y)", "cash_flow", unit="₹ Cr", params={"years": 3}, higher_is_better=True,
           description="Operating cash flow − capex, summed over three years.")
def fcf(ctx: StockContext, years: int):
    ys = _years(ctx, years)
    if not ys:
        return None
    cfo, capex = _total(ys, "cfo"), _total(ys, "capex")
    if cfo is None or capex is None:
        return None
    return Value(crores(cfo - capex), {"cfo_cr": _by_year(ys, "cfo"), "capex_cr": _by_year(ys, "capex")})


@indicator("capex_3y", "Capex (3Y)", "capex", unit="₹ Cr", params={"years": 3},
           description="Purchases of property, plant & equipment and intangibles, summed over three years.")
def capex_total(ctx: StockContext, years: int):
    ys = _years(ctx, years)
    total = _total(ys, "capex") if ys else None
    return Value(crores(total), {"capex_cr": _by_year(ys, "capex")}) if total is not None else None


@indicator("capex_to_cfo_3y", "Capex / CFO (3Y)", "capex", unit="x", params={"years": 3}, higher_is_better=False,
           description="Share of operating cash flow spent on capex over three years. Below 1 = capex paid from internal accruals.")
def capex_to_cfo(ctx: StockContext, years: int):
    ys = _years(ctx, years)
    if not ys:
        return None
    capex, cfo = _total(ys, "capex"), _total(ys, "cfo")
    if capex is None or cfo is None or cfo <= 0:
        return None
    return Value(rnd(capex / cfo), {"capex_cr": _by_year(ys, "capex"), "cfo_cr": _by_year(ys, "cfo")})


def _debt_change(ctx: StockContext, years: int):
    latest = ctx.fin.fy(1)
    if not latest:
        return None, None, None
    now_bs = latest[0]
    then_bs = ctx.fin.balance_at(now_bs.end.replace(year=now_bs.end.year - years))
    now, then = now_bs.get("debt"), then_bs.get("debt") if then_bs else None
    if then_bs is None or then_bs.get("equity") is None:
        return None, now, then
    return (now or 0) - (then or 0), now, then


@indicator("debt_change_3y", "Debt change (3Y)", "capex", unit="₹ Cr", params={"years": 3}, higher_is_better=False,
           description="Borrowings at the latest year-end minus borrowings three year-ends earlier.")
def debt_change(ctx: StockContext, years: int):
    change, now, then = _debt_change(ctx, years)
    if change is None:
        return None
    return Value(crores(change), {"debt_now_cr": crores(now), "debt_then_cr": crores(then)})


@indicator("capex_self_funded_3y", "Capex not debt-funded (3Y)", "capex", kind="bool", params={"years": 3}, higher_is_better=True,
           description=f"True when borrowings rose by no more than {int(MAX_DEBT_SHARE_OF_CAPEX * 100)}% of the capex spent over three years (or fell).")
def capex_self_funded(ctx: StockContext, years: int):
    ys = _years(ctx, years)
    capex = _total(ys, "capex") if ys else None
    change, now, then = _debt_change(ctx, years)
    if capex is None or change is None:
        return None
    return Value(change <= MAX_DEBT_SHARE_OF_CAPEX * capex, {
        "capex_3y_cr": crores(capex), "debt_change_cr": crores(change),
        "debt_now_cr": crores(now), "debt_then_cr": crores(then),
    })
