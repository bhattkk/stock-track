"""Results of the latest finished run: the passing list, and any one stock's breakdown."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from config import settings
from criteria.schema import load as load_criteria
from db import get_db
from models import Result, Run
from routers.runs import run_out

router = APIRouter(tags=["results"])

# Metrics shown as columns in the passing list (the full set is in /stocks/{id}).
LIST_METRICS = ("close", "pct_below_resistance", "resistance", "w_volume_ratio_10w", "rs_6m_vs_benchmark_pct",
                "rs_percentile", "pct_below_52w_high", "w_rsi14", "setups_fired", "warns", "median_traded_value_cr_20d")


def _latest_done(db: Session) -> Run | None:
    """The most recent run that finished successfully."""
    return db.scalar(select(Run).where(Run.status == "done").order_by(Run.id.desc()).limit(1))


@router.get("/results")
def results(db: Session = Depends(get_db)) -> dict:
    """The latest finished run's passing stocks, best score first, plus the run summary."""
    run = _latest_done(db)
    if run is None:
        return {"run": None, "stocks": []}
    rows = db.scalars(select(Result).where(Result.run_id == run.id, Result.passed.is_(True))
                      .order_by(Result.score.desc().nulls_last(), Result.symbol)).all()
    return {
        "run": run_out(run),
        "stocks": [{"instrument_id": r.instrument_id, "symbol": r.symbol, "name": r.name, "score": r.score,
                    "as_of": r.as_of.isoformat() if r.as_of else None,
                    **{k: (r.metrics or {}).get(k) for k in LIST_METRICS}} for r in rows],
    }


@router.get("/search")
def search(q: str, limit: int = 10, db: Session = Depends(get_db)) -> list[dict]:
    """Find stocks in the latest run by symbol or name (prefix matches first)."""
    run = _latest_done(db)
    needle = q.strip().upper()
    if run is None or not needle:
        return []
    rows = db.execute(
        select(Result.instrument_id, Result.symbol, Result.name, Result.passed, Result.failed_at)
        .where(Result.run_id == run.id,
               or_(func.upper(Result.symbol).contains(needle), func.upper(Result.name).contains(needle)))
        .order_by((~func.upper(Result.symbol).startswith(needle)), Result.symbol).limit(limit)
    ).all()
    return [dict(r._mapping) for r in rows]


@router.get("/stocks/{instrument_id}")
def stock(instrument_id: str, db: Session = Depends(get_db)) -> dict:
    """Everything stage 1 decided about one stock in the latest run: every rule's
    outcome (with its `why`), all metrics, and its score."""
    run = _latest_done(db)
    r = db.get(Result, (run.id, instrument_id)) if run else None
    if r is None:
        raise HTTPException(404, "Stock not in the latest run")
    return {"run": run_out(run), "instrument_id": r.instrument_id, "symbol": r.symbol, "name": r.name,
            "passed": r.passed, "failed_at": r.failed_at, "score": r.score,
            "as_of": r.as_of.isoformat() if r.as_of else None, "rules": r.rules, "metrics": r.metrics}


@router.get("/criteria")
def criteria() -> dict:
    """The current rules file, parsed (read-only — edit criteria/technicals.yaml to change it)."""
    try:
        crit, digest = load_criteria(settings.criteria_file)
    except ValueError as exc:
        raise HTTPException(422, f"criteria/technicals.yaml is invalid: {exc}")
    return {"hash": digest, **crit.model_dump()}
