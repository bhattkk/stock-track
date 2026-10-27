"""Runs: list them, see one's progress, start one ("Run now")."""
from __future__ import annotations

import threading

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import pipeline
from db import get_db
from models import Run

router = APIRouter(tags=["runs"])


def run_out(run: Run) -> dict:
    """API shape of a run row."""
    return {
        "id": run.id, "status": run.status, "stage": run.stage, "total": run.total, "done": run.done,
        "counts": run.counts, "criteria_hash": run.criteria_hash, "error": run.error,
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "finished_at": run.finished_at.isoformat() if run.finished_at else None,
    }


@router.get("/runs")
def list_runs(limit: int = 10, db: Session = Depends(get_db)) -> list[dict]:
    """Most recent runs first."""
    return [run_out(r) for r in db.scalars(select(Run).order_by(Run.id.desc()).limit(limit))]


@router.get("/runs/{run_id}")
def get_run(run_id: int, db: Session = Depends(get_db)) -> dict:
    """One run, including live progress while it's running."""
    run = db.get(Run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run_out(run)


@router.post("/runs", status_code=202)
def start(db: Session = Depends(get_db)) -> dict:
    """Start a run in a background thread; 409 if one is already running,
    422 if criteria/technicals.yaml is invalid (the message says what's wrong)."""
    try:
        run_id = pipeline.start_run()
    except pipeline.RunInProgress as exc:
        raise HTTPException(409, str(exc))
    except ValueError as exc:  # pydantic ValidationError is a ValueError
        raise HTTPException(422, f"criteria/technicals.yaml is invalid: {exc}")
    threading.Thread(target=pipeline.execute, args=(run_id,), name=f"run-{run_id}", daemon=True).start()
    return run_out(db.get(Run, run_id))
