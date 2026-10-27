"""Per-stock technical context and the named metrics the gates compare.

`build_context` prepares everything once per stock (daily and weekly bars,
moving averages, RSI, ATR, support/resistance levels). `compute_metrics`
then reduces it to a flat dict of numbers. The tradability and trend gates in
criteria/technicals.yaml refer to these by name (`metric: d_close_vs_sma50_pct`),
and the Analysis tab shows them.

Conventions: "_pct" values are percentages (5.2 means 5.2%); "d_" is from
daily bars and "w_" from weekly bars; None means not enough data.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from technicals import indicators as ind

# Trading days in roughly a year / half a year.
DAYS_52W = 250
DAYS_6M = 126
# Swing-point span (bars on each side) and how far back to look for levels.
DAILY_PIVOT_SPAN, DAILY_LEVEL_LOOKBACK = 3, 250
WEEKLY_PIVOT_SPAN, WEEKLY_LEVEL_LOOKBACK = 2, 52
LEVEL_TOLERANCE_PCT = 1.5


@dataclass
class Context:
    """Everything the gates, setups and flags need for one stock.

    daily / weekly   OHLCV DataFrames, oldest first
    bench            daily OHLCV of the benchmark (NIFTY 50), for relative strength
    d_* / w_*        indicator Series aligned to daily / weekly
    d_levels / w_levels   support/resistance levels from daily / weekly swing points
    """
    today: date
    daily: pd.DataFrame
    weekly: pd.DataFrame
    bench: pd.DataFrame
    d_sma50: pd.Series
    d_sma200: pd.Series
    d_rsi14: pd.Series
    d_atr14: pd.Series
    w_sma30: pd.Series
    w_rsi14: pd.Series
    d_levels: list
    w_levels: list

    @property
    def close(self) -> float:
        """Latest daily close — the price every comparison is made against."""
        return float(self.daily["close"].iloc[-1])


def build_context(daily: pd.DataFrame, bench: pd.DataFrame, today: date) -> Context:
    """Compute indicators and levels for one stock (call once, reuse everywhere)."""
    weekly = ind.resample_weekly(daily, today)
    return Context(
        today=today, daily=daily, weekly=weekly, bench=bench,
        d_sma50=ind.sma(daily["close"], 50), d_sma200=ind.sma(daily["close"], 200),
        d_rsi14=ind.rsi(daily["close"], 14), d_atr14=ind.atr(daily, 14),
        w_sma30=ind.sma(weekly["close"], 30), w_rsi14=ind.rsi(weekly["close"], 14),
        d_levels=ind.levels(daily, DAILY_PIVOT_SPAN, LEVEL_TOLERANCE_PCT, lookback=DAILY_LEVEL_LOOKBACK),
        w_levels=ind.levels(weekly, WEEKLY_PIVOT_SPAN, LEVEL_TOLERANCE_PCT, lookback=WEEKLY_LEVEL_LOOKBACK),
    )


def _last(series: pd.Series, back: int = 0) -> float | None:
    """Value `back` bars before the latest (0 = latest), or None if missing/NaN."""
    if len(series) <= back:
        return None
    v = series.iloc[-1 - back]
    return None if pd.isna(v) else float(v)


def _pct(a: float | None, b: float | None) -> float | None:
    """How far a is above b, in percent: (a / b − 1) × 100. None if either is missing."""
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1) * 100


def _r(v: float | None, nd: int = 2) -> float | None:
    """Round for storage/display; keeps None."""
    return None if v is None else round(float(v), nd)


def _benchmark_return(bench: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float | None:
    """Benchmark % return between two dates, using its last close on or before each date
    (the benchmark and the stock don't always trade on exactly the same days)."""
    if bench.empty:
        return None
    s = bench["close"].loc[:start]
    e = bench["close"].loc[:end]
    if s.empty or e.empty:
        return None
    return _pct(float(e.iloc[-1]), float(s.iloc[-1]))


def compute_metrics(ctx: Context) -> dict:
    """Flat dict of named metrics for one stock. See README "Metrics" for each one."""
    d, w = ctx.daily, ctx.weekly
    close = ctx.close
    year = d.iloc[-DAYS_52W:]

    # Liquidity: median (not mean, so one block deal doesn't inflate it) of
    # close × volume over 20 sessions, in ₹ crore (1 crore = 1e7).
    traded = (d["close"] * d["volume"]).iloc[-20:]
    median_value_cr = float(traded.median()) / 1e7 if len(traded) else None

    # Relative strength: the stock's 6-month return minus the benchmark's over the same dates.
    ret_6m = bench_6m = None
    if len(d) > DAYS_6M:
        ret_6m = _pct(close, float(d["close"].iloc[-1 - DAYS_6M]))
        bench_6m = _benchmark_return(ctx.bench, d.index[-1 - DAYS_6M], d.index[-1])
    rs_6m = None if ret_6m is None or bench_6m is None else ret_6m - bench_6m

    # Weekly volume vs the 10 weeks before it.
    w_vol_ratio = None
    if len(w) >= 11:
        avg10 = float(w["volume"].iloc[-11:-1].mean())
        w_vol_ratio = float(w["volume"].iloc[-1]) / avg10 if avg10 > 0 else None

    res = ind.nearest_above(ctx.w_levels, close)
    sup = ind.nearest_below(ctx.w_levels, close)
    atr = _last(ctx.d_atr14)

    return {
        "as_of": d.index[-1].date().isoformat(),
        "daily_bars": len(d),
        "weekly_bars": len(w),
        "close": _r(close),
        "median_traded_value_cr_20d": _r(median_value_cr),
        "d_sma50": _r(_last(ctx.d_sma50)),
        "d_sma200": _r(_last(ctx.d_sma200)),
        "w_sma30": _r(_last(ctx.w_sma30)),
        "w_close_vs_sma30_pct": _r(_pct(_last(w["close"]) if len(w) else None, _last(ctx.w_sma30))),
        "w_sma30_slope_4w_pct": _r(_pct(_last(ctx.w_sma30), _last(ctx.w_sma30, 4))),
        "d_close_vs_sma50_pct": _r(_pct(close, _last(ctx.d_sma50))),
        "d_sma50_vs_sma200_pct": _r(_pct(_last(ctx.d_sma50), _last(ctx.d_sma200))),
        "d_sma200_slope_20d_pct": _r(_pct(_last(ctx.d_sma200), _last(ctx.d_sma200, 20))),
        "high_52w": _r(float(year["high"].max())) if len(d) >= DAYS_52W else None,
        "low_52w": _r(float(year["low"].min())) if len(d) >= DAYS_52W else None,
        "pct_below_52w_high": _r((1 - close / float(year["high"].max())) * 100) if len(d) >= DAYS_52W else None,
        "pct_above_52w_low": _r(_pct(close, float(year["low"].min()))) if len(d) >= DAYS_52W else None,
        "return_6m_pct": _r(ret_6m),
        "benchmark_return_6m_pct": _r(bench_6m),
        "rs_6m_vs_benchmark_pct": _r(rs_6m),
        "d_rsi14": _r(_last(ctx.d_rsi14), 1),
        "w_rsi14": _r(_last(ctx.w_rsi14), 1),
        "atr_pct": _r(atr / close * 100 if atr else None),
        "w_volume_ratio_10w": _r(w_vol_ratio),
        "resistance": _r(res.price) if res else None,
        "resistance_touches": res.touches if res else None,
        "pct_below_resistance": _r((res.price / close - 1) * 100) if res else None,
        "support": _r(sup.price) if sup else None,
        "support_touches": sup.touches if sup else None,
        "pct_above_support": _r(_pct(close, sup.price)) if sup else None,
    }
