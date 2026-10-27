"""Basic technical indicators on daily / weekly OHLCV DataFrames.

Every function takes pandas objects (a DataFrame with columns open, high,
low, close, volume indexed by date, or one of its columns as a Series) and
returns pandas objects aligned to the same index, so values for any bar can
be read with `.iloc[i]`. Only pandas + numpy are used — no TA-Lib.

Contents
--------
sma              simple moving average
wilder_rma       Wilder's smoothing (the average RSI and ATR are built on)
rsi              Relative Strength Index
atr              Average True Range
resample_weekly  daily bars -> Friday-ending weekly bars
pivots           swing highs / lows ("fractals")
levels           support / resistance price levels from clustered pivots
nearest_above / nearest_below   the closest level to a price on each side
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd


# -- averages ---------------------------------------------------------------

def sma(series: pd.Series, n: int) -> pd.Series:
    """Simple moving average: the plain mean of the last `n` values.

    The first n-1 values are NaN (not enough data yet). Used for the 50/200-day
    and 30-week trend averages — the price "smoothed" over that period. Price
    above a rising average = buyers in control over that horizon.
    """
    return series.rolling(n, min_periods=n).mean()


def wilder_rma(series: pd.Series, n: int) -> pd.Series:
    """Wilder's running moving average (an EMA with alpha = 1/n).

    J. Welles Wilder defined RSI and ATR with this smoothing, so using it makes
    our numbers match what charting sites show. It reacts more slowly than a
    normal EMA of the same length.
    """
    return series.ewm(alpha=1 / n, adjust=False, min_periods=n).mean()


def rsi(close: pd.Series, n: int = 14) -> pd.Series:
    """Relative Strength Index (0–100) over `n` bars, Wilder's method.

    RSI = 100 − 100 / (1 + average gain / average loss). It measures momentum:
    above 50 means up-moves have outweighed down-moves lately; above 70–80 is
    "overbought" (stretched, prone to a pause); below 30 is "oversold".
    """
    delta = close.diff()
    gain = wilder_rma(delta.clip(lower=0), n)
    loss = wilder_rma(-delta.clip(upper=0), n)
    rs = gain / loss.replace(0, np.nan)
    out = 100 - 100 / (1 + rs)
    # No losses at all in the window -> RSI is 100 by definition.
    return out.where(loss != 0, 100.0).where(gain.notna())


def atr(df: pd.DataFrame, n: int = 14) -> pd.Series:
    """Average True Range over `n` bars, Wilder's method, in rupees.

    True range = the largest of (high − low), |high − previous close| and
    |low − previous close| — i.e. the bar's full move including any gap.
    ATR is the typical daily move; ATR / close is volatility as a percentage,
    which lets us compare a ₹100 stock with a ₹5,000 one. Falling ATR / close
    means the stock is getting quieter ("contracting").
    """
    prev_close = df["close"].shift(1)
    tr = pd.concat([df["high"] - df["low"], (df["high"] - prev_close).abs(),
                    (df["low"] - prev_close).abs()], axis=1).max(axis=1)
    return wilder_rma(tr, n)


# -- weekly bars ------------------------------------------------------------

def resample_weekly(daily: pd.DataFrame, today: date) -> pd.DataFrame:
    """Turn daily bars into weekly bars that end on Friday.

    open = first day's open, high = highest high, low = lowest low,
    close = last day's close, volume = sum. The index is the week's Friday
    (even if the market was shut that Friday).

    A week whose Friday is after `today` is still in progress, so it is
    dropped — a half-finished weekly candle would distort patterns and volume.
    The scheduled run is on Saturday, so normally every week is complete.
    """
    if daily.empty:
        return daily.copy()
    weekly = daily.resample("W-FRI").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna(subset=["close"])
    return weekly[weekly.index.date <= today]


# -- swing points and levels -------------------------------------------------

def pivots(df: pd.DataFrame, span: int) -> tuple[list[int], list[int]]:
    """Positions of swing highs and swing lows ("fractals").

    Bar i is a swing high when its high is the highest of the `span` bars on
    each side (i − span … i + span), and a swing low when its low is the lowest.
    These are the turning points traders draw support/resistance lines through.

    The last `span` bars can't be confirmed yet (we don't know the bars after
    them), so they are never pivots. Ties (equal highs side by side) count once,
    at the first bar. Returns (high_positions, low_positions) as integer
    positions into df.
    """
    highs, lows = df["high"].to_numpy(), df["low"].to_numpy()
    hi_pos, lo_pos = [], []
    for i in range(span, len(df) - span):
        window_h = highs[i - span:i + span + 1]
        window_l = lows[i - span:i + span + 1]
        # `argmax == span` means bar i is the first occurrence of the window max.
        if highs[i] == window_h.max() and int(window_h.argmax()) == span:
            hi_pos.append(i)
        if lows[i] == window_l.min() and int(window_l.argmin()) == span:
            lo_pos.append(i)
    return hi_pos, lo_pos


@dataclass
class Level:
    """A support/resistance zone built from one or more nearby swing points.

    price       average price of the swing points in the zone
    touches     how many swing points formed it — more touches = more traders
                watching that price = more significant
    last_pos    position of the most recent touch (recent levels matter more)
    strength    touches weighted by recency (see `levels`)
    """
    price: float
    touches: int
    last_pos: int
    strength: float


def levels(df: pd.DataFrame, span: int, tolerance_pct: float = 1.5,
           before: int | None = None, lookback: int | None = None) -> list[Level]:
    """Support/resistance levels from clustered swing highs AND swing lows.

    Steps:
      1. Find swing highs (their high) and swing lows (their low) with `pivots`.
      2. Keep only pivots before position `before` (to ask "what were the
         levels before this bar?" — needed to detect a breakout without
         look-ahead) and within the last `lookback` bars before it.
      3. Sort the prices and merge any within `tolerance_pct` of the running
         zone average into one zone — prices rarely turn at exactly the same
         rupee, so nearby turns form one level.
      4. strength = Σ over touches of (0.5 + 0.5 × recency), recency 0…1 from
         oldest to newest bar — a level touched three times recently beats one
         touched once a year ago.

    Highs and lows are pooled on purpose: a broken resistance often becomes
    support ("polarity"), so which side a level is on is decided only by
    comparing it with the current price (see nearest_above / nearest_below).
    Returns levels sorted by price.
    """
    end = len(df) if before is None else before
    start = 0 if lookback is None else max(0, end - lookback)
    hi_pos, lo_pos = pivots(df, span)
    points = [(float(df["high"].iloc[i]), i) for i in hi_pos if start <= i < end]
    points += [(float(df["low"].iloc[i]), i) for i in lo_pos if start <= i < end]
    if not points:
        return []
    points.sort()
    span_len = max(1, end - start)

    clusters: list[list[tuple[float, int]]] = [[points[0]]]
    for price, pos in points[1:]:
        current = clusters[-1]
        avg = sum(p for p, _ in current) / len(current)
        if abs(price - avg) / avg * 100 <= tolerance_pct:
            current.append((price, pos))
        else:
            clusters.append([(price, pos)])

    out = []
    for c in clusters:
        recency = [0.5 + 0.5 * (pos - start) / span_len for _, pos in c]
        out.append(Level(price=sum(p for p, _ in c) / len(c), touches=len(c),
                         last_pos=max(pos for _, pos in c), strength=round(sum(recency), 3)))
    return out


def nearest_above(lvls: list[Level], price: float) -> Level | None:
    """The closest level strictly above `price` — the next resistance overhead.

    Nearest (not strongest) because that is the first ceiling price must get
    through; a breakout means clearing it.
    """
    above = [lv for lv in lvls if lv.price > price]
    return min(above, key=lambda lv: lv.price) if above else None


def nearest_below(lvls: list[Level], price: float) -> Level | None:
    """The closest level at or below `price` — the nearest support underneath."""
    below = [lv for lv in lvls if lv.price <= price]
    return max(below, key=lambda lv: lv.price) if below else None


def pct_rank_last(series: pd.Series, lookback: int) -> float | None:
    """Percentile (0–100) of the latest value within the last `lookback` values.

    0 = the lowest reading of the period, 100 = the highest. Used to ask
    "is volatility near its lowest of the last 3 months?".
    """
    window = series.dropna().iloc[-lookback:]
    if len(window) < lookback // 2:
        return None
    last = window.iloc[-1]
    return float((window < last).sum() / (len(window) - 1) * 100) if len(window) > 1 else None
