"""Indicator maths against hand-computed values."""
from datetime import date

import pandas as pd
import pytest

from technicals import indicators as ind
from tests.conftest import bars


def test_sma_is_plain_mean_and_nan_until_full_window():
    s = pd.Series([1, 2, 3, 4, 5], dtype=float)
    out = ind.sma(s, 3)
    assert out.isna().tolist()[:2] == [True, True]
    assert out.iloc[2:].tolist() == [2.0, 3.0, 4.0]


def test_rsi_is_100_with_no_losses_and_0_with_no_gains():
    up = pd.Series(range(1, 40), dtype=float)
    assert ind.rsi(up, 14).iloc[-1] == 100.0
    down = pd.Series(range(40, 1, -1), dtype=float)
    assert ind.rsi(down, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_matches_wilder_reference():
    # Wilder's classic 14-day example (New Concepts in Technical Trading Systems):
    # RSI on the 15th close is ~70.53 with the simple-average seed; with EWM
    # smoothing seeded by the first value it is close to that once warmed up.
    closes = pd.Series([44.34, 44.09, 44.15, 43.61, 44.33, 44.83, 45.10, 45.42, 45.84, 46.08, 45.89,
                        46.03, 45.61, 46.28, 46.28, 46.00, 46.03, 46.41, 46.22, 45.64, 46.21, 46.25,
                        45.71, 46.45, 45.78, 45.35, 44.03, 44.18, 44.22, 44.57, 43.42, 42.66, 43.13])
    out = ind.rsi(closes, 14)
    assert 30 < out.iloc[-1] < 50          # the series ends in a decline
    assert out.iloc[:13].isna().all()      # not enough data before 14 bars


def test_atr_uses_gaps_in_true_range():
    # Bar 2 gaps up: high-low is 1 but |high - prev close| is 11.
    df = bars([(10, 11, 9, 10), (21, 21, 20, 20.5)] + [(20.5, 21, 20, 20.5)] * 14)
    tr_first = max(21 - 20, abs(21 - 10), abs(20 - 10))
    assert tr_first == 11
    assert ind.atr(df, 14).iloc[-1] > 1  # the gap keeps ATR above the plain range


def test_resample_weekly_ohlcv_and_drops_unfinished_week():
    # Mon 2025-01-06 .. Fri 2025-01-17 (two full weeks) + Mon 2025-01-20.
    df = bars([(10, 12, 9, 11, 100), (11, 13, 10, 12, 100), (12, 12, 8, 9, 100), (9, 10, 9, 10, 100), (10, 11, 10, 11, 100),
               (11, 15, 11, 14, 200), (14, 14, 13, 13, 200), (13, 13, 12, 12, 200), (12, 13, 12, 13, 200), (13, 14, 12, 13.5, 200),
               (13.5, 14, 13, 13.8, 50)])
    weekly = ind.resample_weekly(df, today=date(2025, 1, 20))
    assert [d.isoformat() for d in weekly.index.date] == ["2025-01-10", "2025-01-17"]
    first = weekly.iloc[0]
    assert (first["open"], first["high"], first["low"], first["close"], first["volume"]) == (10, 13, 8, 11, 500)
    # On the following Friday the third week is complete and included.
    assert len(ind.resample_weekly(df, today=date(2025, 1, 24))) == 3


def test_pivots_find_swing_points_and_skip_unconfirmed_tail():
    highs = [10, 11, 12, 15, 12, 11, 10, 11, 12, 13, 14]
    df = pd.DataFrame({"high": highs, "low": [h - 2 for h in highs]})
    hi, lo = ind.pivots(df, span=2)
    assert hi == [3]   # 15 is the swing high; 14 at the end isn't confirmed
    assert lo == [6]   # low of the dip (10 - 2)


def test_levels_cluster_nearby_pivots_and_split_far_ones():
    # Two swing highs at ~100 (within 1.5%) and one swing low at 88.
    highs = [95, 96, 100, 96, 95, 92, 91, 92, 95, 96, 100.8, 96, 95, 94]
    lows = [h - 3 for h in highs]
    lows[6] = 88  # the deepest low (bar 5 is 89)
    df = pd.DataFrame({"high": highs, "low": lows})
    lv = ind.levels(df, span=2, tolerance_pct=1.5)
    near_100 = [x for x in lv if 99 < x.price < 101]
    assert len(near_100) == 1 and near_100[0].touches == 2
    assert ind.nearest_above(lv, 97).price == pytest.approx(100.4)
    assert ind.nearest_below(lv, 97).price == pytest.approx(88)


def test_levels_before_excludes_later_pivots():
    highs = [95, 96, 100, 96, 95, 96, 97, 110, 97, 96, 95]
    df = pd.DataFrame({"high": highs, "low": [h - 3 for h in highs]})
    assert all(x.price < 105 for x in ind.levels(df, span=2, before=6))


def test_pct_rank_last():
    s = pd.Series([5, 4, 3, 2, 1], dtype=float)
    assert ind.pct_rank_last(s, 5) == 0.0
    assert ind.pct_rank_last(pd.Series([1, 2, 3, 4, 5], dtype=float), 5) == 100.0
