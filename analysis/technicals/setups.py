"""Entry setups (layer C) and red flags (layer D).

A setup answers "is something happening right now that makes this a good
moment to enter?". A red flag answers "is there a warning sign?". Each is a
function `(ctx, **params) -> Signal`. Its parameters come from
criteria/technicals.yaml, so thresholds are tuned there and the logic lives
here. The SETUPS and FLAGS registries map the YAML `id`s to these functions.

Every detector returns a human-readable `note` that explains the outcome
with numbers. The Analysis tab shows that note when you search a stock.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from technicals import indicators as ind
from technicals import patterns
from technicals.metrics import Context

WEEKLY_PIVOT_SPAN = 2
WEEKLY_LEVEL_LOOKBACK = 52


@dataclass
class Signal:
    """Outcome of one setup / flag.

    fired    True / False, or None when there isn't enough data to judge
    note     one-line explanation with the numbers behind it
    details  the numbers themselves, for the UI / debugging
    """
    fired: bool | None
    note: str
    details: dict = field(default_factory=dict)


def _na(reason: str) -> Signal:
    """Shorthand for "can't evaluate" (counts as not fired)."""
    return Signal(None, f"Not enough data: {reason}")


def _avg_volume_before(df: pd.DataFrame, pos: int, n: int) -> float | None:
    """Mean volume of the `n` bars before position `pos` (not including it)."""
    if pos - n < 0:
        return None
    v = float(df["volume"].iloc[pos - n:pos].mean())
    return v if v > 0 else None


# -- C. entry setups ---------------------------------------------------------

def breakout(ctx: Context, within_weeks: int = 2, volume_multiple: float = 1.5,
             volume_avg_weeks: int = 10, fallback_high_weeks: int = 26) -> Signal:
    """Weekly close above resistance on heavy volume, within the last `within_weeks` weeks.

    For each recent week j, resistance is the nearest swing-point level above
    the previous week's close, using only swing points from before week j (no
    look-ahead). If there is none (the stock is above every old swing), the
    highest weekly close of the prior `fallback_high_weeks` weeks is used.
    The breakout counts when week j closed above that level on volume at least
    `volume_multiple` times the average of the `volume_avg_weeks` weeks before
    it, and the latest close is still above the level (the breakout held).

    Why it matters: resistance is where sellers kept showing up. Closing
    above it on big volume means demand has absorbed that supply, and such
    breakouts often start the next leg up. Without the volume, many
    "breakouts" fail and fall back below the level.
    """
    w = ctx.weekly
    need = max(volume_avg_weeks, fallback_high_weeks) + within_weeks + 1
    if len(w) < need:
        return _na(f"{len(w)} weekly bars, need {need}")
    for back in range(within_weeks):
        j = len(w) - 1 - back
        prev_close = float(w["close"].iloc[j - 1])
        lvls = ind.levels(w, WEEKLY_PIVOT_SPAN, before=j, lookback=WEEKLY_LEVEL_LOOKBACK)
        lv = ind.nearest_above(lvls, prev_close)
        level = lv.price if lv else float(w["close"].iloc[j - fallback_high_weeks:j].max())
        source = f"swing level ({lv.touches} touch{'es' if lv.touches > 1 else ''})" if lv \
            else f"{fallback_high_weeks}-week closing high"
        close_j = float(w["close"].iloc[j])
        avg_vol = _avg_volume_before(w, j, volume_avg_weeks)
        vol_x = float(w["volume"].iloc[j]) / avg_vol if avg_vol else 0.0
        if close_j > level and vol_x >= volume_multiple and ctx.close > level:
            week = w.index[j].date().isoformat()
            return Signal(True, f"Week of {week} closed {close_j:.2f} above {source} {level:.2f} "
                                f"on {vol_x:.1f}× average volume; still above it",
                          {"week": week, "level": round(level, 2), "volume_x": round(vol_x, 2)})
    return Signal(False, f"No weekly close above resistance on ≥{volume_multiple}× volume "
                         f"in the last {within_weeks} weeks")


def accumulation(ctx: Context, volume_multiple: float = 2.0, volume_avg_weeks: int = 10,
                 min_close_position: float = 0.5) -> Signal:
    """Last week was an up week on unusually high volume that closed in the upper part of its range.

    Close above the prior week's close; volume ≥ `volume_multiple` × the
    average of the previous `volume_avg_weeks` weeks; close at least
    `min_close_position` of the way from the week's low to its high (0.5 = upper half).

    Why it matters: big buyers (funds) can't hide their size. A week of very
    heavy volume where price rose and held near the highs suggests
    institutions are building positions. That often happens before price breaks out.
    """
    w = ctx.weekly
    if len(w) < volume_avg_weeks + 2:
        return _na(f"{len(w)} weekly bars")
    j = len(w) - 1
    o, h, low, c = (float(w[k].iloc[j]) for k in ("open", "high", "low", "close"))
    prev_c = float(w["close"].iloc[j - 1])
    avg_vol = _avg_volume_before(w, j, volume_avg_weeks)
    vol_x = float(w["volume"].iloc[j]) / avg_vol if avg_vol else 0.0
    pos = (c - low) / (h - low) if h > low else 0.0
    details = {"volume_x": round(vol_x, 2), "close_position": round(pos, 2), "week_change_pct": round((c / prev_c - 1) * 100, 2)}
    fired = c > prev_c and vol_x >= volume_multiple and pos >= min_close_position
    return Signal(fired, f"Last week {details['week_change_pct']:+.1f}% on {vol_x:.1f}× volume, "
                         f"closed {pos:.0%} up its range", details)


def tight_base(ctx: Context, weeks: int = 3, max_range_pct: float = 8.0, atr_lookback: int = 60,
               max_atr_percentile: float = 20.0, max_pct_below_resistance: float = 5.0) -> Signal:
    """Price has gone quiet just under resistance — a coiled spring.

    All three must hold:
      - the last `weeks` weekly closes are within `max_range_pct` of each other;
      - daily ATR/close (volatility) is in the lowest `max_atr_percentile`% of
        its last `atr_lookback` sessions;
      - price is within `max_pct_below_resistance`% below the nearest weekly
        resistance. If there is no resistance overhead, the stock is above all
        its old swing points (blue sky), which also counts.

    Why it matters: when a stock in an uptrend moves less and less near its
    highs, sellers are drying up (often called a volatility contraction
    pattern, VCP). Breakouts from tight bases tend to be cleaner, and the stop
    can sit just under the base, so risk is small.
    """
    w, d = ctx.weekly, ctx.daily
    if len(w) < weeks or len(d) < atr_lookback:
        return _na(f"{len(w)} weekly / {len(d)} daily bars")
    closes = w["close"].iloc[-weeks:]
    range_pct = (float(closes.max()) / float(closes.min()) - 1) * 100
    atr_pct_series = ctx.d_atr14 / d["close"]
    atr_rank = ind.pct_rank_last(atr_pct_series, atr_lookback)
    res = ind.nearest_above(ctx.w_levels, ctx.close)
    below_res = (res.price / ctx.close - 1) * 100 if res else 0.0
    details = {"range_pct": round(range_pct, 2), "atr_percentile": None if atr_rank is None else round(atr_rank, 1),
               "pct_below_resistance": round(below_res, 2), "resistance": round(res.price, 2) if res else None}
    if atr_rank is None:
        return Signal(None, "Not enough ATR history", details)
    fired = range_pct <= max_range_pct and atr_rank <= max_atr_percentile and below_res <= max_pct_below_resistance
    res_txt = f"{below_res:.1f}% below resistance {res.price:.2f}" if res else "no resistance overhead"
    return Signal(fired, f"Last {weeks} weekly closes within {range_pct:.1f}%, volatility at "
                         f"{atr_rank:.0f}th percentile of {atr_lookback} days, {res_txt}", details)


def pullback_support(ctx: Context, ma_days: int = 50, tolerance_pct: float = 2.0, within_days: int = 3,
                     reversal_patterns: list[str] | None = None, volume_short_days: int = 5,
                     volume_long_days: int = 20) -> Signal:
    """An uptrending stock dipped to support and printed a bullish reversal candle on light volume.

    All three must hold, within the last `within_days` daily bars:
      - support touched: some bar's low came within `tolerance_pct`% of the
        `ma_days`-day average, or of a daily support level (a former
        resistance that was broken counts, since levels are pooled), and the
        reversal bar closed above that support (a dip from above, not a rally into it);
      - a reversal candle formed (`reversal_patterns`, default hammer,
        bullish engulfing, piercing, morning star);
      - the pullback was quiet: average volume of the `volume_short_days`
        bars before the reversal was below the `volume_long_days`-bar average.

    Why it matters: buying a dip to support inside an uptrend is the classic
    low-risk entry, because the stop goes just under support. Light volume on the dip
    says holders aren't rushing out, and the reversal candle shows buyers stepping back in.
    """
    names = reversal_patterns or ["hammer", "bullish_engulfing", "piercing", "morning_star"]
    d = ctx.daily
    sma = ind.sma(d["close"], ma_days)
    if len(d) < max(ma_days, volume_long_days) + within_days + 1 or pd.isna(sma.iloc[-1]):
        return _na(f"{len(d)} daily bars")
    for back in range(within_days):
        k = len(d) - 1 - back
        found = patterns.detect(d, k, names)
        if not found:
            continue
        # Support touch: the lowest low of the bars up to and including the reversal.
        window = d.iloc[max(0, k - within_days + 1):k + 1]
        low = float(window["low"].min())
        ma = float(sma.iloc[k])
        near_ma = abs(low - ma) / ma * 100 <= tolerance_pct
        lvls = ind.levels(d, 3, before=k - within_days + 1, lookback=250)
        sup = ind.nearest_below(lvls, low * (1 + tolerance_pct / 100))
        near_level = sup is not None and abs(low - sup.price) / sup.price * 100 <= tolerance_pct
        # It must be a dip *to* support from above: the reversal bar closes above
        # the level it touched (a rally up to an average from below is resistance).
        close_k = float(d["close"].iloc[k])
        near_ma = near_ma and close_k > ma
        near_level = near_level and close_k > sup.price
        short = _avg_volume_before(d, k, volume_short_days)
        long_ = _avg_volume_before(d, k, volume_long_days)
        quiet = short is not None and long_ is not None and short < long_
        if (near_ma or near_level) and quiet:
            where = f"{ma_days}-day average {ma:.2f}" if near_ma else f"support {sup.price:.2f}"
            day = d.index[k].date().isoformat()
            return Signal(True, f"{', '.join(found)} on {day} after a dip to {where} on light volume "
                                f"({short / long_:.2f}× normal)",
                          {"day": day, "patterns": found, "low": round(low, 2), "support": where,
                           "volume_x": round(short / long_, 2)})
    return Signal(False, f"No bullish reversal at support on light volume in the last {within_days} days")


def weekly_reversal(ctx: Context, reversal_patterns: list[str] | None = None,
                    tolerance_pct: float = 3.0) -> Signal:
    """The latest weekly candle is a bullish reversal and its low sits on support.

    Pattern from `reversal_patterns` (default bullish engulfing, hammer,
    morning star) on the latest week, with that week's low within
    `tolerance_pct`% of the 30-week average or of a weekly support level.

    Why it matters: a weekly reversal at support is the same idea as the daily
    pullback, but on a bigger timeframe, so it carries more weight. A whole
    week of buyers defending a level that matters.
    """
    names = reversal_patterns or ["bullish_engulfing", "hammer", "morning_star"]
    w = ctx.weekly
    if len(w) < 31:
        return _na(f"{len(w)} weekly bars")
    found = patterns.detect(w, -1, names)
    low = float(w["low"].iloc[-1])
    sma30 = float(ctx.w_sma30.iloc[-1])
    near_ma = abs(low - sma30) / sma30 * 100 <= tolerance_pct
    lvls = ind.levels(w, WEEKLY_PIVOT_SPAN, before=len(w) - 1, lookback=WEEKLY_LEVEL_LOOKBACK)
    sup = ind.nearest_below(lvls, low * (1 + tolerance_pct / 100))
    near_level = sup is not None and abs(low - sup.price) / sup.price * 100 <= tolerance_pct
    if not found:
        return Signal(False, "No bullish reversal candle on the latest week")
    if not (near_ma or near_level):
        return Signal(False, f"{', '.join(found)} on the latest week, but its low {low:.2f} isn't near "
                             f"the 30-week average {sma30:.2f} or a support level", {"patterns": found})
    where = f"30-week average {sma30:.2f}" if near_ma else f"support {sup.price:.2f}"
    return Signal(True, f"{', '.join(found)} on the latest week with its low {low:.2f} at {where}",
                  {"patterns": found, "low": round(low, 2), "support": where})


# -- D. red flags ------------------------------------------------------------

def overextended(ctx: Context, max_pct_above_sma50: float = 25.0, max_rsi: float = 80.0) -> Signal:
    """Price has run too far, too fast: more than `max_pct_above_sma50`% above the
    50-day average, or daily RSI above `max_rsi`.

    Why it matters: stretched stocks tend to pull back or move sideways before
    going higher. Buying here puts the stop far away (big risk) or right in the
    path of a normal pullback.
    """
    sma50, rsi = ctx.d_sma50.iloc[-1], ctx.d_rsi14.iloc[-1]
    if pd.isna(sma50) or pd.isna(rsi):
        return _na("50-day average / RSI")
    above = (ctx.close / float(sma50) - 1) * 100
    fired = above > max_pct_above_sma50 or float(rsi) > max_rsi
    return Signal(fired, f"{above:.1f}% above the 50-day average, daily RSI {float(rsi):.0f}",
                  {"pct_above_sma50": round(above, 2), "rsi": round(float(rsi), 1)})


def distribution(ctx: Context, weeks: int = 8, min_count: int = 3, volume_avg_weeks: int = 10) -> Signal:
    """At least `min_count` of the last `weeks` weeks were down weeks on above-average volume.

    "Above average" means more than the mean of the `volume_avg_weeks` weeks
    before that week.

    Why it matters: heavy volume on down weeks is the mirror of accumulation.
    Large holders are selling into strength. Several such weeks close together
    often come before a trend breaks.
    """
    w = ctx.weekly
    if len(w) < weeks + volume_avg_weeks + 1:
        return _na(f"{len(w)} weekly bars")
    count, days = 0, []
    for j in range(len(w) - weeks, len(w)):
        avg = _avg_volume_before(w, j, volume_avg_weeks)
        if w["close"].iloc[j] < w["close"].iloc[j - 1] and avg and w["volume"].iloc[j] > avg:
            count += 1
            days.append(w.index[j].date().isoformat())
    return Signal(count >= min_count, f"{count} high-volume down weeks in the last {weeks}",
                  {"count": count, "weeks": days})


def bearish_at_resistance(ctx: Context, reversal_patterns: list[str] | None = None,
                          tolerance_pct: float = 3.0) -> Signal:
    """The latest weekly candle is a bearish reversal with its high at resistance.

    Pattern from `reversal_patterns` (default bearish engulfing, shooting star,
    evening star) on the latest week, with its high within `tolerance_pct`% of
    a level built from the swing points before that week.

    Why it matters: sellers defended the ceiling on a weekly timeframe. Until
    that level is cleared, upside looks capped.
    """
    names = reversal_patterns or ["bearish_engulfing", "shooting_star", "evening_star"]
    w = ctx.weekly
    if len(w) < 10:
        return _na(f"{len(w)} weekly bars")
    found = patterns.detect(w, -1, names)
    if not found:
        return Signal(False, "No bearish reversal candle on the latest week")
    high = float(w["high"].iloc[-1])
    lvls = ind.levels(w, WEEKLY_PIVOT_SPAN, before=len(w) - 1, lookback=WEEKLY_LEVEL_LOOKBACK)
    near = [lv for lv in lvls if abs(high - lv.price) / lv.price * 100 <= tolerance_pct]
    if not near:
        return Signal(False, f"{', '.join(found)} on the latest week, but not at a resistance level",
                      {"patterns": found})
    lv = min(near, key=lambda x: abs(high - x.price))
    return Signal(True, f"{', '.join(found)} on the latest week with its high {high:.2f} at resistance {lv.price:.2f}",
                  {"patterns": found, "level": round(lv.price, 2)})


def gap_down(ctx: Context, min_gap_pct: float = 5.0, within_days: int = 10) -> Signal:
    """A daily gap down of at least `min_gap_pct`% in the last `within_days`
    sessions that hasn't been filled.

    The gap is open vs the previous close. "Not filled" means no bar since then
    has traded back up to the previous day's low.

    Why it matters: a large unfilled gap down usually means bad news the
    market is still digesting (results, governance, a downgrade). Price
    often struggles to get back through the gap.
    """
    d = ctx.daily
    if len(d) < within_days + 1:
        return _na(f"{len(d)} daily bars")
    for t in range(len(d) - within_days, len(d)):
        prev_close, prev_low = float(d["close"].iloc[t - 1]), float(d["low"].iloc[t - 1])
        gap = (1 - float(d["open"].iloc[t]) / prev_close) * 100
        if gap >= min_gap_pct and float(d["high"].iloc[t:].max()) < prev_low:
            day = d.index[t].date().isoformat()
            return Signal(True, f"Gapped down {gap:.1f}% on {day}; not filled (high since {float(d['high'].iloc[t:].max()):.2f} "
                                f"< prior low {prev_low:.2f})", {"day": day, "gap_pct": round(gap, 2)})
    return Signal(False, f"No unfilled gap down ≥{min_gap_pct}% in the last {within_days} days")


# YAML id -> detector.
SETUPS = {
    "breakout": breakout,
    "accumulation": accumulation,
    "tight_base": tight_base,
    "pullback_support": pullback_support,
    "weekly_reversal": weekly_reversal,
}
FLAGS = {
    "overextended": overextended,
    "distribution": distribution,
    "bearish_at_resistance": bearish_at_resistance,
    "gap_down": gap_down,
}
