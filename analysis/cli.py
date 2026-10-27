"""Command line for the analysis service (run from analysis/).

    python cli.py run          full run: sync candles + evaluate + store (≈50 min first time)
    python cli.py sync         only bring candles up to date
    python cli.py check SYMBOL evaluate one stock against the current rules and print the breakdown
                               (uses stored candles, syncing that one stock first; stores nothing)
    python cli.py validate     check criteria/technicals.yaml without running anything
"""
from __future__ import annotations

import json
import sys

import candles
import pipeline
from config import ist_today, settings
from criteria.schema import load as load_criteria
from db import SessionLocal, init_schema
from logging_config import setup_logging
from sources.upstox import Upstox
from stores.redis_store import RedisStore
from technicals.metrics import build_context, compute_metrics
from technicals.rules import evaluate

MARK = {"PASS": "✔", "FIRED": "✔", "CLEAR": "·", "FAIL": "✘", "NOT_FIRED": "·", "NA": "?", "WARN": "!", "REJECT": "✘"}


def check(symbol: str) -> int:
    """Print every rule's outcome for one stock (for tuning the YAML)."""
    criteria, _ = load_criteria(settings.criteria_file)
    ins = next((i for i in RedisStore(settings.redis_url).equity_universe() if i["symbol"] == symbol.upper()), None)
    if ins is None:
        print(f"{symbol} not found in INSTRUMENTS")
        return 1
    upstox = Upstox(settings.upstox_access_token, settings.upstox_request_delay)
    with SessionLocal() as db:
        for iid in (settings.benchmark_key, ins["instrument_id"]):
            candles.sync(db, upstox, iid, settings.history_days)
        daily, bench = candles.load(db, ins["instrument_id"]), candles.load(db, settings.benchmark_key)
    ctx = build_context(daily, bench, ist_today())
    metrics = compute_metrics(ctx)
    ev = evaluate(criteria, ctx, metrics)
    print(f"{ins['symbol']} — {'PASSED' if ev.passed else 'rejected at ' + ev.failed_at}  (as of {metrics['as_of']})\n")
    for r in ev.rules:
        print(f"  {MARK.get(r['status'], ' ')} [{r['layer']:<11}] {r['title']:<48} {r['note']}")
    print("\nmetrics:", json.dumps(metrics, indent=1))
    return 0


def main() -> int:
    setup_logging()
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "validate":
        _, digest = load_criteria(settings.criteria_file)
        print(f"OK — {settings.criteria_file} (sha256 {digest[:12]})")
        return 0
    init_schema()
    if cmd == "run":
        print(f"Run {pipeline.run_now()} finished")
        return 0
    if cmd == "sync":
        universe = RedisStore(settings.redis_url).equity_universe()
        errors = pipeline.sync_all(None, universe, Upstox(settings.upstox_access_token, settings.upstox_request_delay),
                                   ist_today())
        print(f"Synced {len(universe)} stocks, {len(errors)} errors")
        return 0
    if cmd == "check" and len(sys.argv) > 2:
        return check(sys.argv[2])
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
