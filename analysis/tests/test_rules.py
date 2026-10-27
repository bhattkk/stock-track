"""Setups, flags, the rules engine and ranking on synthetic charts, using the real YAML."""
from datetime import date

import numpy as np
import pandas as pd
import pytest
import yaml

from config import settings
from criteria.schema import Criteria, load as load_criteria
from pipeline import _rank, funnel, latest_session
from technicals import setups
from technicals.metrics import build_context, compute_metrics
from technicals.rules import evaluate
from tests.conftest import trend_series

CRITERIA, _ = load_criteria(settings.criteria_file)


def _today(df: pd.DataFrame) -> date:
    """The Saturday after the last bar, so the last week counts as complete."""
    last = df.index[-1].date()
    return last + pd.Timedelta(days=(5 - last.weekday()) % 7 or 7)


def _flat_bench(index) -> pd.DataFrame:
    return pd.DataFrame({"open": 100.0, "high": 100.0, "low": 100.0, "close": 100.0, "volume": 0}, index=index)


def _ctx(df):
    return build_context(df, _flat_bench(df.index), _today(df))


def breakout_chart() -> pd.DataFrame:
    """Uptrend for ~210 days, a ~60-day base capped near 150, then a breakout week on 3× volume."""
    up = trend_series(n=210, start=80, daily_pct=0.3, noise=0.3)
    scale = 145 / up["close"].iloc[-1]
    up[["open", "high", "low", "close"]] *= scale
    idx = pd.bdate_range(up.index[-1] + pd.Timedelta(days=1), periods=60)
    t = np.arange(60)
    base_close = 146 + 3 * np.sin(t / 3)          # oscillates 143–149, highs ~150
    base = pd.DataFrame({"open": np.r_[up["close"].iloc[-1], base_close[:-1]], "close": base_close}, index=idx)
    base["high"] = base[["open", "close"]].max(axis=1) + 1
    base["low"] = base[["open", "close"]].min(axis=1) - 1
    base["volume"] = 200_000
    bidx = pd.bdate_range(idx[-1] + pd.Timedelta(days=1), periods=5)
    closes = np.array([151, 154, 156, 157, 158.0])
    brk = pd.DataFrame({"open": np.r_[base_close[-1], closes[:-1]], "close": closes,
                        "high": closes + 0.5, "low": np.r_[base_close[-1], closes[:-1]] - 0.5,
                        "volume": 600_000}, index=bidx)
    return pd.concat([up, base, brk])[["open", "high", "low", "close", "volume"]]


def test_yaml_loads_and_every_rule_explains_itself():
    rules = CRITERIA.tradability + CRITERIA.trend + CRITERIA.setups.rules + CRITERIA.flags
    assert all(len(r.why) > 40 for r in rules)


def test_unknown_setup_or_pattern_is_rejected():
    raw = yaml.safe_load(settings.criteria_file.read_text())
    raw["setups"]["rules"][0]["id"] = "moon_shot"
    with pytest.raises(ValueError, match="Unknown setup"):
        Criteria.model_validate(raw)
    raw = yaml.safe_load(settings.criteria_file.read_text())
    raw["flags"][2]["params"]["reversal_patterns"] = ["dragon"]
    with pytest.raises(ValueError, match="unknown pattern"):
        Criteria.model_validate(raw)


def test_strong_uptrend_passes_tradability_and_trend():
    df = trend_series(n=300, start=100, daily_pct=0.25, noise=0.4, volume=1_000_000)  # ~₹10+ Cr/day
    ctx = _ctx(df)
    ev = evaluate(CRITERIA, ctx, compute_metrics(ctx))
    gates = {r["id"]: r["status"] for r in ev.rules if r["layer"] in ("tradability", "trend")}
    assert set(gates.values()) == {"PASS"}, gates
    assert ev.failed_at in (None, "setup", "flag")


def test_downtrend_is_rejected_at_trend_with_reasons():
    df = trend_series(n=300, start=300, daily_pct=-0.2, noise=0.4, volume=1_000_000)
    ctx = _ctx(df)
    ev = evaluate(CRITERIA, ctx, compute_metrics(ctx))
    assert ev.failed_at == "trend" and not ev.passed
    failed = [r for r in ev.rules if r["status"] == "FAIL"]
    assert any(r["id"] == "w_above_30w" for r in failed)
    assert all("needs" in r["note"] for r in failed)


def test_short_history_is_na_and_never_passes():
    df = trend_series(n=120, start=100, daily_pct=0.3)
    ctx = _ctx(df)
    ev = evaluate(CRITERIA, ctx, compute_metrics(ctx))
    statuses = {r["id"]: r["status"] for r in ev.rules}
    assert statuses["history"] == "FAIL"
    assert statuses["d_50_above_200"] == "NA"
    assert ev.failed_at == "tradability"


def test_breakout_fires_on_volume_and_not_without_it():
    df = breakout_chart()
    sig = setups.breakout(_ctx(df), within_weeks=2, volume_multiple=1.5)
    assert sig.fired, sig.note
    quiet = df.copy()
    quiet["volume"] = 200_000
    assert not setups.breakout(_ctx(quiet), within_weeks=2, volume_multiple=1.5).fired


def test_accumulation_needs_volume_and_strong_close():
    df = breakout_chart()
    assert setups.accumulation(_ctx(df), volume_multiple=2.0).fired
    weak = df.copy()
    week_low = float(weak["low"].iloc[-5:].min())
    # Friday reverses: the week still closes up (above last week's close) but near its low.
    weak.iloc[-1, weak.columns.get_loc("close")] = week_low + 0.5
    weak.iloc[-1, weak.columns.get_loc("low")] = week_low
    assert not setups.accumulation(_ctx(weak), volume_multiple=2.0, min_close_position=0.5).fired


def test_overextended_flag():
    df = trend_series(n=300, start=100, daily_pct=0.8, noise=0.2)  # parabolic
    assert setups.overextended(_ctx(df), max_pct_above_sma50=25, max_rsi=80).fired


def test_gap_down_flag_only_when_unfilled():
    df = trend_series(n=260, start=100, daily_pct=0.1, noise=0.2)
    gapped = df.copy()
    i = len(df) - 5
    gapped.iloc[i:] = gapped.iloc[i:] * [0.9, 0.9, 0.9, 0.9, 1]  # 10% gap down that never recovers
    assert setups.gap_down(_ctx(gapped), min_gap_pct=5, within_days=10).fired
    assert not setups.gap_down(_ctx(df), min_gap_pct=5, within_days=10).fired


def test_rank_percentiles_and_funnel():
    class Ev:
        def __init__(self, fired):
            self.setups_fired, self.warns = fired, []
    rows = [{"failed_at": None, "metrics": {"rs_6m_vs_benchmark_pct": 30, "w_rsi14": 60}, "_ev": Ev(["breakout"])},
            {"failed_at": "trend", "metrics": {"rs_6m_vs_benchmark_pct": -10, "w_rsi14": 40}, "_ev": Ev([])},
            {"failed_at": "data", "metrics": {}, "_ev": None}]
    _rank(CRITERIA, rows)
    assert rows[0]["metrics"]["rs_percentile"] == 100.0 and rows[1]["metrics"]["rs_percentile"] == 0.0
    assert rows[0]["score"] == 2 + 4 + 1   # one setup, top RS, healthy weekly RSI
    assert funnel(rows) == {"universe": 3, "with_data": 2, "tradability": 2, "trend": 1, "setup": 1, "passed": 1}


def test_latest_session_skips_weekend():
    assert latest_session(date(2026, 10, 25)) == date(2026, 10, 23)  # Sunday -> Friday
    assert latest_session(date(2026, 10, 21)) == date(2026, 10, 21)


def _pullback_chart(from_above: bool) -> pd.DataFrame:
    """~250 bars ending in a 50-day-average touch, a quiet 5-day drift and a bullish engulfing bar.

    from_above=True: an uptrend dips down to the 50-day average (support).
    from_above=False: a downtrend rallies up to it and the engulfing bar closes below it.
    """
    df = trend_series(n=250, start=100, daily_pct=0.2 if from_above else -0.2, noise=0.2, volume=1_000_000)
    ma = float(df["close"].iloc[-60:-10].mean())  # roughly where the 50-day average sits
    tail = []
    level = ma * (1.01 if from_above else 0.97)
    for k in range(5):  # quiet drift towards the average
        tail.append((level + 0.5, level + 0.8, level - 0.2, level, 400_000))
    lo = ma * (1.0 if from_above else 0.96)
    tail.append((level, level + 0.1, lo, level - 0.6, 400_000))                 # bearish bar
    tail.append((level - 0.8, level + 1.2 if from_above else level - 0.1, level - 0.9,
                 level + 1.0 if from_above else level - 0.2, 900_000))          # bullish engulfing
    idx = pd.bdate_range(df.index[-1] + pd.Timedelta(days=1), periods=len(tail))
    extra = pd.DataFrame(tail, columns=["open", "high", "low", "close", "volume"], index=idx)
    return pd.concat([df, extra])


def test_pullback_needs_a_dip_from_above_not_a_rally_into_resistance():
    below = _pullback_chart(from_above=False)
    assert not setups.pullback_support(_ctx(below), tolerance_pct=5).fired
