"""One stage-1 run over the whole NSE equity universe.

    1. load + validate criteria/technicals.yaml (fail fast on a typo)
    2. sync daily candles for the NIFTY 50 benchmark and every stock
       (incremental; ~1 Upstox request per stock, paced for the rate limit)
    3. evaluate every stock: metrics → gates → setups → flags
    4. rank: relative-strength percentile across the universe, then score
    5. store one analysis_results row per stock + the funnel counts on the run

Progress (stage, done/total) is written to analysis_runs as it goes, so the
Analysis tab can show a progress bar. Only one run can be active at a time.
"""
from __future__ import annotations

import logging
from bisect import bisect_left
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select, update

import candles
from config import ist_today, settings
from criteria.schema import Criteria, load as load_criteria
from db import SessionLocal
from models import Result, Run
from sources.upstox import Upstox, UpstoxError
from stores.redis_store import RedisStore
from technicals.metrics import build_context, compute_metrics
from technicals.rules import evaluate, score

log = logging.getLogger("analysis.pipeline")

PROGRESS_EVERY = 25           # commit progress every N stocks
STALE_AFTER = timedelta(minutes=10)  # no heartbeat for this long = the run's process is gone
MAX_CONSECUTIVE_FAILURES = 20  # e.g. an expired token: stop instead of failing 2,700 times


class RunInProgress(Exception):
    pass


def latest_session(today: date) -> date:
    """The most recent weekday on or before `today` — the newest bar we can expect.

    Exchange holidays aren't known here; on a holiday this is one day ahead of
    the real last session, which only costs one extra (cheap) request per stock.
    """
    d = today
    while d.weekday() >= 5:  # Saturday = 5, Sunday = 6
        d -= timedelta(days=1)
    return d


def mark_interrupted_runs() -> None:
    """Mark runs whose process died as `interrupted`.

    A run is presumed dead when it is still `running` but hasn't written
    progress for STALE_AFTER. A CLI run (`python cli.py run`) that is alive
    keeps its heartbeat fresh, so restarting the service doesn't touch it.
    """
    now = datetime.now(timezone.utc)
    with SessionLocal() as db:
        for run in db.scalars(select(Run).where(Run.status == "running")):
            last = run.heartbeat_at or run.started_at
            if last is None or now - last > STALE_AFTER:
                run.status, run.finished_at = "interrupted", now
        db.commit()


def start_run() -> int:
    """Create a run row (refusing if one is active) and return its id.

    Stale "running" rows are cleared first, so a crashed run can't block new ones.

    Validates the criteria file first, so a broken YAML fails the request
    immediately instead of 50 minutes into a run.
    """
    _, digest = load_criteria(settings.criteria_file)
    mark_interrupted_runs()
    with SessionLocal() as db:
        if db.scalar(select(Run.id).where(Run.status == "running")):
            raise RunInProgress("A run is already in progress")
        run = Run(criteria_hash=digest, heartbeat_at=datetime.now(timezone.utc))
        db.add(run)
        db.commit()
        return run.id


def _progress(run_id: int, **values) -> None:
    """Update the run row's progress fields (and heartbeat) in their own short transaction."""
    with SessionLocal() as db:
        db.execute(update(Run).where(Run.id == run_id).values(heartbeat_at=datetime.now(timezone.utc), **values))
        db.commit()


def sync_all(run_id: int | None, universe: list[dict], upstox: Upstox, today: date) -> dict[str, str]:
    """Bring candles up to date for the benchmark and every stock.

    Skips a stock whose newest stored bar is already the latest session (so a
    "Run now" right after the scheduled run doesn't re-download anything).
    Returns {instrument_id: error} for the stocks whose sync failed. Their
    stored candles (if any) are still evaluated.
    """
    expected = latest_session(today)
    errors: dict[str, str] = {}
    consecutive = 0
    ids = [settings.benchmark_key] + [ins["instrument_id"] for ins in universe]
    with SessionLocal() as db:
        for n, instrument_id in enumerate(ids, start=1):
            last = candles.last_stored_date(db, instrument_id)
            if last is None or last < expected:
                try:
                    candles.sync(db, upstox, instrument_id, settings.history_days, today)
                    consecutive = 0
                except UpstoxError as exc:
                    db.rollback()
                    errors[instrument_id] = str(exc)
                    consecutive += 1
                    log.warning("Candle sync failed for %s: %s", instrument_id, exc)
                    if consecutive >= MAX_CONSECUTIVE_FAILURES:
                        raise RuntimeError(f"{consecutive} candle fetches failed in a row "
                                           f"(last: {exc}). Check UPSTOX_ACCESS_TOKEN / rate limits.") from exc
            if run_id and n % PROGRESS_EVERY == 0:
                _progress(run_id, done=n)
    if settings.benchmark_key in errors:
        log.warning("Benchmark sync failed; relative-strength gates will be NA if no stored history")
    return errors


def evaluate_all(criteria: Criteria, universe: list[dict], sync_errors: dict[str, str],
                 today: date, run_id: int | None = None) -> list[dict]:
    """Evaluate every stock and return result rows (not yet saved).

    A stock with no stored candles gets `failed_at = "data"` and the sync
    error as its reason. Scores are added after all stocks are evaluated,
    because the relative-strength percentile needs the whole universe.
    """
    rows: list[dict] = []
    with SessionLocal() as db:
        bench = candles.load(db, settings.benchmark_key)
        for n, ins in enumerate(universe, start=1):
            iid = ins["instrument_id"]
            base = {"instrument_id": iid, "symbol": ins.get("symbol") or iid, "name": ins.get("name")}
            daily = candles.load(db, iid)
            if daily.empty:
                reason = sync_errors.get(iid, "No daily candles from Upstox (new listing or suspended?)")
                rows.append({**base, "passed": False, "failed_at": "data", "score": None, "metrics": {},
                             "rules": [{"layer": "data", "id": "candles", "title": "Price data",
                                        "status": "NA", "note": reason, "why": "Needs daily candles."}],
                             "as_of": None, "_ev": None})
                continue
            ctx = build_context(daily, bench, today)
            metrics = compute_metrics(ctx)
            ev = evaluate(criteria, ctx, metrics)
            if iid in sync_errors:
                metrics["sync_error"] = sync_errors[iid]  # evaluated on older candles
            rows.append({**base, "passed": ev.passed, "failed_at": ev.failed_at, "metrics": metrics,
                         "rules": ev.rules, "as_of": daily.index[-1].date(), "_ev": ev})
            if run_id and n % PROGRESS_EVERY == 0:
                _progress(run_id, done=n)

    _rank(criteria, rows)
    return rows


def _rank(criteria: Criteria, rows: list[dict]) -> None:
    """Add the RS percentile (vs every stock with an RS value) and the score to each row."""
    rs_values = sorted(r["metrics"]["rs_6m_vs_benchmark_pct"] for r in rows
                       if r["_ev"] and r["metrics"].get("rs_6m_vs_benchmark_pct") is not None)
    n = len(rs_values)
    for r in rows:
        ev = r.pop("_ev")
        if ev is None:
            continue
        rs = r["metrics"].get("rs_6m_vs_benchmark_pct")
        # Percentile = share of the universe with a lower RS (0 = weakest, 100 = strongest).
        pct = None if rs is None or n < 2 else round(_count_below(rs_values, rs) / (n - 1) * 100, 1)
        r["metrics"]["rs_percentile"] = pct
        r["metrics"]["setups_fired"] = ev.setups_fired
        r["metrics"]["warns"] = ev.warns
        r["score"] = score(criteria, ev, pct, r["metrics"].get("w_rsi14"))


def _count_below(sorted_values: list[float], x: float) -> int:
    """How many values in the sorted list are strictly below x (binary search)."""
    return bisect_left(sorted_values, x)


def funnel(rows: list[dict]) -> dict:
    """Counts of stocks clearing each layer in order, for the run summary.

    e.g. {"universe": 2708, "with_data": 2650, "tradability": 1500, "trend": 420,
          "setup": 95, "passed": 90}
    """
    order = ["data", "tradability", "trend", "setup", "flag"]
    counts = {"universe": len(rows)}
    remaining = len(rows)
    for layer, label in zip(order, ["with_data", "tradability", "trend", "setup", "passed"]):
        remaining -= sum(1 for r in rows if r["failed_at"] == layer)
        counts[label] = remaining
    return counts


def execute(run_id: int, today: date | None = None) -> None:
    """Run the whole pipeline for an existing run row. Never raises: errors mark the run failed."""
    today = today or ist_today()
    try:
        criteria, _ = load_criteria(settings.criteria_file)
        universe = RedisStore(settings.redis_url).equity_universe()
        if not universe:
            raise RuntimeError("INSTRUMENTS in Redis is empty: is market-data running?")
        upstox = Upstox(settings.upstox_access_token, settings.upstox_request_delay)
        log.info("Run %d: %d stocks, syncing candles", run_id, len(universe))

        _progress(run_id, stage="syncing", total=len(universe) + 1, done=0)
        errors = sync_all(run_id, universe, upstox, today)

        log.info("Run %d: evaluating (%d sync errors)", run_id, len(errors))
        _progress(run_id, stage="evaluating", total=len(universe), done=0)
        rows = evaluate_all(criteria, universe, errors, today, run_id)

        with SessionLocal() as db:
            db.bulk_insert_mappings(Result, [{**r, "run_id": run_id} for r in rows])
            counts = funnel(rows)
            db.execute(update(Run).where(Run.id == run_id).values(
                status="done", stage="done", done=len(universe), counts=counts,
                finished_at=datetime.now(timezone.utc)))
            db.commit()
        log.info("Run %d done: %s", run_id, counts)
    except Exception as exc:  # the run must end in a terminal state whatever happens
        log.exception("Run %d failed", run_id)
        _progress(run_id, status="failed", error=str(exc)[:2000], finished_at=datetime.now(timezone.utc))


def run_now() -> int:
    """Start a run and execute it in this thread (CLI / scheduler). Returns the run id."""
    run_id = start_run()
    execute(run_id)
    return run_id
