"""Margins: OPM improvement (criterion 1) and gross-margin proxies for
backward integration / pricing power (criteria 2-3)."""
from __future__ import annotations

from indicators.base import StockContext, Value, indicator, rnd, stdev


def _fy_series(ctx: StockContext, metric: str, n: int) -> dict[str, float | None]:
    return {f"FY{p.end.year % 100:02d}": rnd(p.get(metric)) for p in reversed(ctx.fin.fy(n))}


@indicator("opm_latest", "OPM (latest FY)", "profitability", unit="%", higher_is_better=True,
           description="Operating profit margin: (revenue − expenses excl. interest & depreciation) / revenue, latest full year.")
def opm_latest(ctx: StockContext):
    years = ctx.fin.fy(1)
    return Value(rnd(years[0].get("opm")), {"opm": _fy_series(ctx, "opm", 5)}) if years else None


@indicator("opm_ttm", "OPM (TTM)", "profitability", unit="%", higher_is_better=True,
           description="Operating margin over the last four reported quarters.")
def opm_ttm(ctx: StockContext):
    ebitda, qs = ctx.fin.ttm("ebitda")
    revenue, _ = ctx.fin.ttm("revenue")
    if ebitda is None or not revenue or revenue <= 0:
        return None
    return Value(rnd(ebitda / revenue * 100), {"quarters": {q.end.isoformat(): rnd(q.get("opm")) for q in reversed(qs)}})


@indicator("opm_ttm_change", "OPM change (TTM vs prior TTM)", "profitability", unit="pp", higher_is_better=True,
           description="Last-four-quarters OPM minus the four quarters before that, in percentage points.")
def opm_ttm_change(ctx: StockContext):
    cur_e, _ = ctx.fin.ttm("ebitda")
    cur_r, _ = ctx.fin.ttm("revenue")
    prev_e, _ = ctx.fin.ttm("ebitda", offset=4)
    prev_r, _ = ctx.fin.ttm("revenue", offset=4)
    if None in (cur_e, cur_r, prev_e, prev_r) or cur_r <= 0 or prev_r <= 0:
        return None
    cur, prev = cur_e / cur_r * 100, prev_e / prev_r * 100
    return Value(rnd(cur - prev), {"opm_ttm": rnd(cur), "opm_prior_ttm": rnd(prev)})


@indicator("opm_change_5y", "OPM change (5Y)", "profitability", unit="pp", params={"years": 5}, higher_is_better=True,
           description="Latest-FY OPM minus OPM five years earlier, in percentage points.")
@indicator("opm_change_3y", "OPM change (3Y)", "profitability", unit="pp", params={"years": 3}, higher_is_better=True,
           description="Latest-FY OPM minus OPM three years earlier, in percentage points.")
def opm_change(ctx: StockContext, years: int):
    return _margin_change(ctx, "opm", years)


@indicator("opm_rising_years", "OPM rising streak", "profitability", unit="yrs", higher_is_better=True,
           description="Consecutive years (counting back from the latest FY) in which OPM went up.")
def opm_rising_years(ctx: StockContext):
    years = ctx.fin.fy(6)
    if len(years) < 2:
        return None
    streak = 0
    for newer, older in zip(years, years[1:]):
        a, b = newer.get("opm"), older.get("opm")
        if a is None or b is None or a <= b or newer.end.year - older.end.year != 1:
            break
        streak += 1
    return Value(streak, {"opm": _fy_series(ctx, "opm", 6)})


@indicator("opm_stability_5y", "OPM volatility (5Y stdev)", "profitability", unit="sd", higher_is_better=False,
           description="Standard deviation of yearly OPM over five years. Low = steady margins, a sign of pricing power.")
def opm_stability(ctx: StockContext):
    return _margin_stdev(ctx, "opm")


@indicator("gross_margin_latest", "Gross margin (latest FY)", "profitability", unit="%", higher_is_better=True,
           description="(Revenue − materials − traded goods ± inventory change) / revenue.")
def gross_margin_latest(ctx: StockContext):
    years = ctx.fin.fy(1)
    return Value(rnd(years[0].get("gross_margin")), {"gross_margin": _fy_series(ctx, "gross_margin", 5)}) if years else None


@indicator("gross_margin_change_3y", "Gross margin change (3Y)", "profitability", unit="pp", params={"years": 3}, higher_is_better=True,
           description="Gross margin now vs three years ago. A rising gross margin is a proxy for backward integration or better product mix.")
def gross_margin_change(ctx: StockContext, years: int):
    return _margin_change(ctx, "gross_margin", years)


@indicator("gross_margin_stability_5y", "Gross margin volatility (5Y stdev)", "profitability", unit="sd", higher_is_better=False,
           description="Standard deviation of yearly gross margin. Steady through raw-material cycles suggests pricing power.")
def gross_margin_stability(ctx: StockContext):
    return _margin_stdev(ctx, "gross_margin")


def _margin_change(ctx: StockContext, metric: str, years: int):
    latest = ctx.fin.fy(1)
    if not latest:
        return None
    base = ctx.fin.fy_ending(latest[0].end.year - years)
    now, then = latest[0].get(metric), base.get(metric) if base else None
    if now is None or then is None:
        return None
    return Value(rnd(now - then), {metric: _fy_series(ctx, metric, years + 1)})


def _margin_stdev(ctx: StockContext, metric: str):
    years = ctx.fin.fy(5)
    sd = stdev([p.get(metric) for p in years], min_points=4)
    return Value(rnd(sd), {metric: _fy_series(ctx, metric, 5)}) if sd is not None else None
