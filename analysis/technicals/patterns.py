"""Candlestick patterns on a single bar (daily or weekly).

Each detector takes an OHLC DataFrame and a bar position `i` (negative
positions work, e.g. -1 = latest bar) and returns True/False. They look only
at bar i and the bars before it, never after, so they're safe to use on the
latest bar.

Terms
-----
body          |close − open| — how far price moved from open to close
range         high − low — the bar's full extent
upper shadow  high − max(open, close) — the wick above the body
lower shadow  min(open, close) − low — the wick below the body
bullish bar   close > open;  bearish bar  close < open

A pattern on its own is weak evidence. Stage 1 only uses patterns where
price is already at a support or resistance level (see setups.py), which is
where they carry meaning: buyers or sellers showing up at a price that
matters.

Thresholds are module constants so they're easy to find and tune.
"""
from __future__ import annotations

import pandas as pd

# A "small" body is at most this fraction of the bar's range (doji).
DOJI_BODY_MAX = 0.10
# Hammer / shooting star: the long shadow is at least this many bodies long...
LONG_SHADOW_MIN_BODIES = 2.0
# ...and the opposite shadow is at most this fraction of the bar's range.
SHORT_SHADOW_MAX_RANGE = 0.15
# The body must still be visible (not a doji) for a hammer / star.
MIN_BODY_RANGE = 0.05
# "Strong" candle: body is at least this fraction of the range.
STRONG_BODY_MIN = 0.5
# Bars that define the prior move a reversal pattern needs (e.g. a hammer
# must come after a decline over the previous TREND_BARS bars).
TREND_BARS = 3


def _bar(df: pd.DataFrame, i: int) -> tuple[float, float, float, float]:
    """(open, high, low, close) of bar i."""
    row = df.iloc[i]
    return float(row["open"]), float(row["high"]), float(row["low"]), float(row["close"])


def _parts(o: float, h: float, low: float, c: float) -> tuple[float, float, float, float]:
    """(body, range, upper shadow, lower shadow) of one bar."""
    return abs(c - o), h - low, h - max(o, c), min(o, c) - low


def _pos(df: pd.DataFrame, i: int) -> int:
    """Normalise a possibly-negative position to 0…len-1."""
    return i if i >= 0 else len(df) + i


def _prior_decline(df: pd.DataFrame, i: int) -> bool:
    """True if the close before bar i is lower than the close TREND_BARS bars earlier.

    Reversal-up patterns (hammer, morning star) need something to reverse.
    """
    p = _pos(df, i)
    if p - 1 - TREND_BARS < 0:
        return False
    return df["close"].iloc[p - 1] < df["close"].iloc[p - 1 - TREND_BARS]


def _prior_advance(df: pd.DataFrame, i: int) -> bool:
    """Mirror of _prior_decline: the move into bar i was up."""
    p = _pos(df, i)
    if p - 1 - TREND_BARS < 0:
        return False
    return df["close"].iloc[p - 1] > df["close"].iloc[p - 1 - TREND_BARS]


def doji(df: pd.DataFrame, i: int = -1) -> bool:
    """Open ≈ close: buyers and sellers fought to a draw — indecision.

    Body ≤ 10% of the bar's range.
    """
    body, rng, _, _ = _parts(*_bar(df, i))
    return rng > 0 and body <= DOJI_BODY_MAX * rng


def hammer(df: pd.DataFrame, i: int = -1) -> bool:
    """Bullish reversal: after a decline, sellers pushed price well down but
    buyers drove it back up to close near the high.

    Long lower shadow (≥ 2× the body), tiny upper shadow (≤ 15% of range),
    real body (≥ 5% of range), and a decline over the prior 3 bars.
    The colour of the body doesn't matter.
    """
    body, rng, upper, lower = _parts(*_bar(df, i))
    return (rng > 0 and body >= MIN_BODY_RANGE * rng and lower >= LONG_SHADOW_MIN_BODIES * body
            and upper <= SHORT_SHADOW_MAX_RANGE * rng and _prior_decline(df, i))


def shooting_star(df: pd.DataFrame, i: int = -1) -> bool:
    """Bearish reversal (mirror of the hammer): after an advance, buyers pushed
    price well up but sellers knocked it back to close near the low.

    Long upper shadow (≥ 2× body), tiny lower shadow, real body, prior advance.
    """
    body, rng, upper, lower = _parts(*_bar(df, i))
    return (rng > 0 and body >= MIN_BODY_RANGE * rng and upper >= LONG_SHADOW_MIN_BODIES * body
            and lower <= SHORT_SHADOW_MAX_RANGE * rng and _prior_advance(df, i))


def bullish_engulfing(df: pd.DataFrame, i: int = -1) -> bool:
    """Bullish reversal: a bearish bar followed by a bullish bar whose body
    completely covers the previous body — buyers overwhelmed the prior selling.

    Previous bar bearish, this bar bullish, this open ≤ previous close,
    this close ≥ previous open, and this body bigger than the previous one.
    """
    p = _pos(df, i)
    if p < 1:
        return False
    po, _, _, pc = _bar(df, p - 1)
    o, _, _, c = _bar(df, p)
    return pc < po and c > o and o <= pc and c >= po and (c - o) > (po - pc)


def bearish_engulfing(df: pd.DataFrame, i: int = -1) -> bool:
    """Bearish reversal (mirror): a bullish bar swallowed by a bigger bearish body
    — sellers overwhelmed the prior buying."""
    p = _pos(df, i)
    if p < 1:
        return False
    po, _, _, pc = _bar(df, p - 1)
    o, _, _, c = _bar(df, p)
    return pc > po and c < o and o >= pc and c <= po and (o - c) > (pc - po)


def piercing(df: pd.DataFrame, i: int = -1) -> bool:
    """Bullish reversal: after a strong bearish bar, the next bar opens at or
    below its close and rallies to close above the midpoint of its body (but
    not above its open — that would be an engulfing).

    Previous body ≥ 50% of its range; previous bar bearish; this bar bullish.
    """
    p = _pos(df, i)
    if p < 1:
        return False
    po, ph, pl, pc = _bar(df, p - 1)
    o, _, _, c = _bar(df, p)
    pbody, prng, _, _ = _parts(po, ph, pl, pc)
    midpoint = (po + pc) / 2
    return (pc < po and prng > 0 and pbody >= STRONG_BODY_MIN * prng and c > o
            and o <= pc and midpoint < c < po)


def morning_star(df: pd.DataFrame, i: int = -1) -> bool:
    """Three-bar bullish reversal: a strong bearish bar, then a small-bodied
    bar (selling exhausts), then a bullish bar closing above the midpoint of
    the first bar's body (buyers take over).

    Bar 1 bearish with body ≥ 50% of range; bar 2 body ≤ 30% of bar 1's body;
    bar 3 bullish and closes above bar 1's body midpoint. Bar i is bar 3.
    """
    p = _pos(df, i)
    if p < 2:
        return False
    o1, h1, l1, c1 = _bar(df, p - 2)
    o2, _, _, c2 = _bar(df, p - 1)
    o3, _, _, c3 = _bar(df, p)
    body1, rng1, _, _ = _parts(o1, h1, l1, c1)
    return (c1 < o1 and rng1 > 0 and body1 >= STRONG_BODY_MIN * rng1
            and abs(c2 - o2) <= 0.3 * body1 and c3 > o3 and c3 > (o1 + c1) / 2)


def evening_star(df: pd.DataFrame, i: int = -1) -> bool:
    """Three-bar bearish reversal (mirror of the morning star)."""
    p = _pos(df, i)
    if p < 2:
        return False
    o1, h1, l1, c1 = _bar(df, p - 2)
    o2, _, _, c2 = _bar(df, p - 1)
    o3, _, _, c3 = _bar(df, p)
    body1, rng1, _, _ = _parts(o1, h1, l1, c1)
    return (c1 > o1 and rng1 > 0 and body1 >= STRONG_BODY_MIN * rng1
            and abs(c2 - o2) <= 0.3 * body1 and c3 < o3 and c3 < (o1 + c1) / 2)


def inside_bar(df: pd.DataFrame, i: int = -1) -> bool:
    """The bar's whole range sits inside the previous bar's range — a pause /
    consolidation. Several in a row near resistance often precede a breakout."""
    p = _pos(df, i)
    if p < 1:
        return False
    _, ph, pl, _ = _bar(df, p - 1)
    _, h, low, _ = _bar(df, p)
    return h <= ph and low >= pl


# Name -> detector, so the YAML can refer to patterns by name.
PATTERNS = {
    "doji": doji,
    "hammer": hammer,
    "shooting_star": shooting_star,
    "bullish_engulfing": bullish_engulfing,
    "bearish_engulfing": bearish_engulfing,
    "piercing": piercing,
    "morning_star": morning_star,
    "evening_star": evening_star,
    "inside_bar": inside_bar,
}


def detect(df: pd.DataFrame, i: int = -1, names: list[str] | None = None) -> list[str]:
    """Names of the patterns (from `names`, default all) present at bar i."""
    out = []
    for name in names or PATTERNS:
        if name not in PATTERNS:
            raise ValueError(f"Unknown candlestick pattern {name!r}; known: {sorted(PATTERNS)}")
        if PATTERNS[name](df, i):
            out.append(name)
    return out
