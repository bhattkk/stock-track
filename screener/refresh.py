"""On-demand refresh: universe → results filings → indicators.

Started from the UI (POST /refresh, runs in a background thread) or the CLI
(`python cli.py refresh`). Only one refresh runs at a time across both — a
Postgres advisory lock guards it — and progress is written to
screener_refresh_runs so the UI can show it.

Incremental by default:
  * a stock's filing list is only fetched when a new quarter's results
    could be out (see `is_due`) — `full=True` ignores that;
  * a filing already parsed is never downloaded again (and its raw XBRL is
    cached on disk anyway).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import func, select, text, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from compute import compute_all
from config import settings
from db import SessionLocal, engine
from models import Fact, Filing, RefreshRun, Stock
from providers.base import FilingRef
from providers.nse_results import NseResultsProvider
from providers.nse_session import NseError, NseSession
from providers.nse_universe import fetch_equity_list, fetch_latest_closes

log = logging.getLogger("screener.refresh")

ADVISORY_LOCK_KEY = 7_203_401  # arbitrary, unique to this service
# Earliest a company's results can be out after its quarter ends, and how
# often a late filer's list is re-checked after that.
RESULTS_EARLIEST_DAYS = 10
RECHECK_AFTER = timedelta(days=3)
FY_END_MONTH = 3  # Indian companies close their books on 31 March


class RefreshBusy(Exception):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# -- filing selection ---------------------------------------------------------

def _months_back(d: date, months: int) -> tuple[int, int]:
    total = d.year * 12 + d.month - 1 - months
    return total // 12, total % 12 + 1


def select_filings(refs: list[FilingRef], years: int, quarters: int) -> list[FilingRef]:
    """The filings worth parsing: the latest revision per quarter, consolidated
    over standalone, for the last `quarters` quarters plus each FY-end quarter
    (Q4 filings carry the full year) of the last `years` years. Oldest first."""
    latest: dict[tuple[date, str], FilingRef] = {}
    for r in refs:
        key = (r.period_end, r.basis)
        cur = latest.get(key)
        if cur is None or (r.filed_at or datetime.min) > (cur.filed_at or datetime.min):
            latest[key] = r
    by_period: dict[date, FilingRef] = {}
    for (end, basis), r in latest.items():
        if basis == "consolidated" or end not in by_period:
            by_period[end] = r

    ends = sorted(by_period, reverse=True)
    if not ends:
        return []
    keep = set(ends[:quarters])
    fy_ends = [e for e in ends if e.month == FY_END_MONTH]
    if fy_ends:
        keep.update(fy_ends[:years])
    else:  # unusual year-end: keep every quarter in the window instead
        y, m = _months_back(ends[0], 12 * years)
        keep.update(e for e in ends if (e.year, e.month) > (y, m))
    return sorted((by_period[e] for e in keep), key=lambda r: r.period_end)


def is_due(stock: Stock, latest_period: date | None, today: date) -> bool:
    """Could a newer quarter's results have been filed since we last looked?"""
    if latest_period is None or stock.results_checked_at is None:
        return True
    y, m = _months_back(latest_period, -3)
    next_end = date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)  # last day of the next quarter
    if today < next_end + timedelta(days=RESULTS_EARLIEST_DAYS):
        return False
    return _utcnow() - stock.results_checked_at >= RECHECK_AFTER


# -- steps ----------------------------------------------------------------------

def sync_universe(db: Session, session: NseSession) -> int:
    """Upsert screener_stocks from EQUITY_L.csv and set the latest closes."""
    listed = fetch_equity_list(session, settings.series_list)
    closes, close_date = fetch_latest_closes(session)
    existing = {s.instrument_id: s for s in db.scalars(select(Stock))}
    seen = set()
    for ls in listed:
        seen.add(ls.instrument_id)
        stock = existing.get(ls.instrument_id)
        if stock is None:
            stock = Stock(instrument_id=ls.instrument_id)
            db.add(stock)
        stock.symbol, stock.name, stock.isin, stock.series, stock.listed = ls.symbol, ls.name, ls.isin, ls.series, True
        if ls.isin in closes:
            stock.close, stock.close_date = closes[ls.isin], close_date
    for iid, stock in existing.items():
        if iid not in seen:
            stock.listed = False
    db.commit()
    log.info("Universe: %d listed stock(s); closes as of %s", len(listed), close_date)
    return len(listed)


def store_facts(db: Session, instrument_id: str, basis: str, filing_id: int, facts) -> None:
    if not facts:
        return
    rows = [{"instrument_id": instrument_id, "basis": basis, "period_type": f.period_type,
             "period_end": f.period_end, "metric": f.metric, "value": f.value, "filing_id": filing_id} for f in facts]
    stmt = pg_insert(Fact).values(rows)
    db.execute(stmt.on_conflict_do_update(
        index_elements=[Fact.instrument_id, Fact.basis, Fact.period_type, Fact.period_end, Fact.metric],
        set_={"value": stmt.excluded.value, "filing_id": stmt.excluded.filing_id},
    ))


def refresh_stock(db: Session, provider: NseResultsProvider, stock: Stock, full: bool) -> int:
    """Fetch and parse this stock's new filings. Returns how many were parsed."""
    latest = db.scalar(select(func.max(Filing.period_end)).where(
        Filing.instrument_id == stock.instrument_id, Filing.status == "parsed"))
    refs = provider.list_filings(stock.symbol, None if full else latest)
    wanted = select_filings(refs, settings.history_years, settings.history_quarters)
    done_urls = set(db.scalars(select(Filing.url).where(
        Filing.instrument_id == stock.instrument_id, Filing.status == "parsed")))

    parsed = 0
    for ref in wanted:
        if ref.url in done_urls and not full:
            continue
        filing = db.scalar(select(Filing).where(Filing.url == ref.url))
        if filing is None:
            filing = Filing(instrument_id=stock.instrument_id, url=ref.url)
            db.add(filing)
        filing.period_end, filing.basis, filing.filed_at = ref.period_end, ref.basis, ref.filed_at
        db.flush()
        try:
            facts, is_financial = provider.fetch_facts(ref)
        except Exception as exc:  # network, XML or anything else — keep going
            filing.status, filing.error = "failed", str(exc)[:500]
            log.warning("%s: filing %s failed: %s", stock.symbol, ref.url, exc)
            continue
        store_facts(db, stock.instrument_id, ref.basis, filing.id, facts)
        filing.status, filing.error, filing.parsed_at = "parsed", None, _utcnow()
        stock.is_financial = stock.is_financial or is_financial
        parsed += 1
    stock.results_checked_at, stock.last_error = _utcnow(), None
    db.commit()
    return parsed


# -- orchestration ----------------------------------------------------------------

def _progress(db: Session, run: RefreshRun, **fields) -> None:
    for k, v in fields.items():
        setattr(run, k, v)
    db.commit()


def mark_interrupted_runs() -> None:
    """At startup: a 'running' row with nobody holding the lock died with its process."""
    with engine.connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            return  # a CLI refresh is genuinely running
        try:
            conn.execute(update(RefreshRun).where(RefreshRun.status == "running")
                         .values(status="interrupted", finished_at=_utcnow()))
            conn.commit()
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
            conn.commit()


def create_run(params: dict) -> int:
    with SessionLocal() as db:
        if db.scalar(select(RefreshRun.id).where(RefreshRun.status == "running")):
            raise RefreshBusy("A refresh is already running")
        run = RefreshRun(params=params)
        db.add(run)
        db.commit()
        return run.id


def run_refresh(run_id: int, symbols: list[str] | None = None, full: bool = False, compute_only: bool = False) -> None:
    """Execute a refresh created by `create_run`. Blocks until done."""
    lock_conn = engine.connect()
    db = SessionLocal()
    run = db.get(RefreshRun, run_id)
    try:
        if not lock_conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": ADVISORY_LOCK_KEY}).scalar():
            _progress(db, run, status="failed", finished_at=_utcnow(), message="Another refresh is running")
            return
        session = NseSession()
        ids = None
        if not compute_only:
            _progress(db, run, stage="universe", message="Fetching NSE equity list and closing prices")
            sync_universe(db, session)

            q = select(Stock).where(Stock.listed.is_(True)).order_by(Stock.symbol)
            if symbols:
                q = q.where(Stock.symbol.in_([s.upper() for s in symbols]))
            stocks = db.scalars(q).all()
            ids = [s.instrument_id for s in stocks] if symbols else None
            latest = dict(db.execute(select(Filing.instrument_id, func.max(Filing.period_end))
                                     .where(Filing.status == "parsed").group_by(Filing.instrument_id)).all())
            today = date.today()
            todo = [s for s in stocks if full or symbols or is_due(s, latest.get(s.instrument_id), today)]
            _progress(db, run, stage="results", total=len(todo), done=0, failed=0,
                      message=f"{len(todo)} of {len(stocks)} stock(s) may have new results")

            provider = NseResultsProvider(session)
            failed = 0
            for i, stock in enumerate(todo, 1):
                try:
                    refresh_stock(db, provider, stock, full)
                except NseError as exc:
                    db.rollback()
                    failed += 1
                    stock.last_error = str(exc)[:500]
                    db.commit()
                    log.warning("%s: %s", stock.symbol, exc)
                except Exception as exc:
                    db.rollback()
                    failed += 1
                    log.exception("%s: refresh failed", stock.symbol)
                    stock.last_error = str(exc)[:500]
                    db.commit()
                _progress(db, run, done=i, failed=failed, message=stock.symbol)

        _progress(db, run, stage="indicators", done=0, total=0, message="Computing indicators")
        compute_all(db, ids, progress=lambda d, t: _progress(db, run, done=d, total=t))
        _progress(db, run, status="done", stage="done", finished_at=_utcnow(), message=None)
        log.info("Refresh %d done", run_id)
    except Exception as exc:
        log.exception("Refresh %d failed", run_id)
        db.rollback()
        _progress(db, run, status="failed", finished_at=_utcnow(), message=str(exc)[:500])
    finally:
        db.close()
        try:
            lock_conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": ADVISORY_LOCK_KEY})
        finally:
            lock_conn.close()


def reparse_cached(db: Session, provider: NseResultsProvider) -> int:
    """Re-parse every parsed filing from its cached XBRL (after a TAG_MAP change)."""
    count = 0
    for filing in db.scalars(select(Filing).where(Filing.status == "parsed").order_by(Filing.filed_at)):
        if not provider.cache_path(filing.url).exists():
            continue
        ref = FilingRef(url=filing.url, period_end=filing.period_end, basis=filing.basis, filed_at=filing.filed_at)
        facts, _ = provider.fetch_facts(ref)
        store_facts(db, filing.instrument_id, filing.basis, filing.id, facts)
        count += 1
        if count % 500 == 0:
            db.commit()
    db.commit()
    return count
