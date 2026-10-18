"""Start an on-demand refresh and watch its progress."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

import refresh as refresh_job
from db import get_db
from models import Filing, RefreshRun, Stock

router = APIRouter(tags=["refresh"])


class RefreshRequest(BaseModel):
    symbols: list[str] | None = None  # default: every listed stock that may have new results
    full: bool = False  # re-list every stock's filings and re-parse everything
    compute_only: bool = False  # skip NSE entirely; just recompute indicators


def _run_out(run: RefreshRun | None) -> dict | None:
    if run is None:
        return None
    return {
        "id": run.id, "status": run.status, "stage": run.stage, "total": run.total, "done": run.done,
        "failed": run.failed, "message": run.message, "params": run.params,
        "started_at": run.started_at.isoformat(),
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/refresh")
def refresh_status(db: Session = Depends(get_db)) -> dict:
    last = db.scalar(select(RefreshRun).order_by(RefreshRun.id.desc()).limit(1))
    last_ok = db.scalar(select(RefreshRun).where(RefreshRun.status == "done").order_by(RefreshRun.id.desc()).limit(1))
    return {
        "run": _run_out(last),
        "last_completed_at": last_ok.finished_at.isoformat() if last_ok and last_ok.finished_at else None,
        "coverage": {
            "listed": db.scalar(select(func.count()).select_from(Stock).where(Stock.listed.is_(True))),
            "with_results": db.scalar(select(func.count(func.distinct(Filing.instrument_id))).where(Filing.status == "parsed")),
            "close_date": (d.isoformat() if (d := db.scalar(select(func.max(Stock.close_date)))) else None),
        },
    }


@router.post("/refresh", status_code=202)
def start_refresh(req: RefreshRequest) -> dict:
    try:
        run_id = refresh_job.create_run(req.model_dump())
    except refresh_job.RefreshBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    threading.Thread(
        target=refresh_job.run_refresh, args=(run_id, req.symbols, req.full, req.compute_only),
        name=f"refresh-{run_id}", daemon=True,
    ).start()
    return {"id": run_id, "status": "running"}
