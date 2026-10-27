"""Shared test helpers. Tests are pure-Python: no Postgres, Redis or Upstox
(the real stack holds live portfolio data and must never be touched)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def bars(rows, start="2025-01-06", freq="B"):
    """DataFrame from [(open, high, low, close[, volume]), ...] on consecutive business days."""
    rows = [r if len(r) == 5 else (*r, 1000) for r in rows]
    idx = pd.date_range(start, periods=len(rows), freq=freq)
    return pd.DataFrame(rows, columns=["open", "high", "low", "close", "volume"], index=idx)


def trend_series(n=300, start=100.0, daily_pct=0.15, noise=0.6, volume=200_000, seed=1):
    """A smooth uptrend (or downtrend for negative daily_pct) with small noise, business days."""
    rng = np.random.default_rng(seed)
    closes = start * np.cumprod(1 + daily_pct / 100 + rng.normal(0, noise / 100, n))
    opens = np.r_[closes[0], closes[:-1]]
    highs = np.maximum(opens, closes) * 1.005
    lows = np.minimum(opens, closes) * 0.995
    idx = pd.date_range("2025-06-02", periods=n, freq="B")
    return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": closes,
                         "volume": np.full(n, volume)}, index=idx)
