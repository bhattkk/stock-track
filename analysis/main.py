"""Analysis service entrypoint: stage 1 (technical filter) of the stock pipeline.

Every week (Saturday 07:00 IST by default) it syncs daily candles for every
NSE equity from Upstox and filters them on daily and weekly charts using the
rules in criteria/technicals.yaml (see README.md). The front end's Analysis
tab shows the passes and, for any stock, why it passed or was rejected.

Reads market-data's INSTRUMENTS hash in Redis (the universe); writes only its
own analysis_* tables in Postgres. Never calls another service.
"""
from __future__ import annotations

import logging
import threading
from contextlib import asynccontextmanager

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI

import pipeline
from config import settings
from db import init_schema
from logging_config import setup_logging
from routers import results, runs

setup_logging()
log = logging.getLogger("analysis")


def scheduled_run() -> None:
    """Scheduler job: start a run unless one is already going."""
    try:
        pipeline.run_now()
    except pipeline.RunInProgress:
        log.info("Scheduled run skipped: a run is already in progress")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting analysis service")
    init_schema()
    pipeline.mark_interrupted_runs()
    scheduler = None
    if settings.schedule_enabled:
        scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
        scheduler.add_job(scheduled_run, CronTrigger(day_of_week=settings.schedule_day_of_week,
                                                     hour=settings.schedule_hour, timezone="Asia/Kolkata"),
                          id="weekly-run", max_instances=1, coalesce=True, misfire_grace_time=6 * 3600)
        scheduler.start()
        log.info("Weekly run scheduled: %s %02d:00 IST", settings.schedule_day_of_week, settings.schedule_hour)
    yield
    if scheduler:
        scheduler.shutdown(wait=False)
    log.info("Stopped.")


app = FastAPI(title="Analysis Service", lifespan=lifespan)
app.include_router(runs.router)
app.include_router(results.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok", "threads": str(threading.active_count())}
