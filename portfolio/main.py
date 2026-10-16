"""Portfolio microservice entrypoint.

Owns watchlists and the portfolio in Postgres, exposes them over a small
FastAPI app, and keeps market-data's Redis SUBSCRIBED_INSTRUMENTS set in
sync (see routers/watchlists.py and routers/portfolio.py). A background
scheduler periodically backs up Postgres to disk (see backup.py) and
refreshes each open position's peak close for the trailing SL (see peaks.py)
and each portfolio instrument's fundamentals (see fundamentals.py).
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

from backup import run_backup
from config import settings
from db import init_schema
from deps import get_redis_store
from logging_config import setup_logging
from fundamentals import refresh_all_fundamentals
from peaks import refresh_all_peaks
from routers import market, portfolio, search, watchlists

setup_logging()
log = logging.getLogger("portfolio")

MAX_LOGGED_BODY = 2000  # truncate defensively; payloads here are small JSON


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """Logs every incoming API request (method, path, body) and its response status."""

    async def dispatch(self, request: Request, call_next):
        body = await request.body()
        data = body.decode("utf-8", errors="replace")[:MAX_LOGGED_BODY] if body else ""
        query = f"?{request.url.query}" if request.url.query else ""
        log.info(
            "--> %s %s%s%s",
            request.method,
            request.url.path,
            query,
            f" data={data}" if data else "",
        )
        response = await call_next(request)
        log.info("<-- %s %s %d", request.method, request.url.path, response.status_code)
        return response


scheduler = BackgroundScheduler(timezone="Asia/Kolkata")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting portfolio service")

    init_schema()
    log.info("Postgres schema ready")

    get_redis_store().ping()
    log.info("Connected to Redis at %s", settings.redis_url)

    if settings.backup_interval_minutes > 0:
        scheduler.add_job(
            run_backup,
            "interval",
            minutes=settings.backup_interval_minutes,
            args=[settings],
            id="pg_backup",
            max_instances=1,
            coalesce=True,
        )
        log.info(
            "Backups scheduled: every %d min -> %s (keep last %d)",
            settings.backup_interval_minutes,
            settings.backup_dir,
            settings.backup_retention,
        )
    else:
        log.info("Backups disabled (backup_interval_minutes <= 0)")

    # Peak closes: once now (in the background, so startup isn't blocked on
    # Upstox) and daily after the 15:30 IST close.
    scheduler.add_job(refresh_all_peaks, "date", run_date=datetime.now(scheduler.timezone), id="peaks_startup")
    scheduler.add_job(
        refresh_all_peaks, "cron", day_of_week="mon-fri", hour=16, minute=15, id="peaks_daily", coalesce=True
    )
    log.info("Peak refresh scheduled: at startup and Mon-Fri 16:15 IST")

    # Fundamentals: at startup (skips anything already fetched today) and
    # daily after Upstox's 05:00 IST data refresh.
    scheduler.add_job(refresh_all_fundamentals, "date", run_date=datetime.now(scheduler.timezone), id="fundamentals_startup")
    scheduler.add_job(refresh_all_fundamentals, "cron", hour=6, minute=0, id="fundamentals_daily", coalesce=True)
    log.info("Fundamentals refresh scheduled: at startup and daily 06:00 IST")

    scheduler.start()

    yield

    if scheduler.running:
        scheduler.shutdown(wait=False)
    log.info("Stopped.")


app = FastAPI(title="Portfolio Service", lifespan=lifespan)
app.add_middleware(RequestLoggingMiddleware)
app.include_router(search.router)
app.include_router(watchlists.router)
app.include_router(portfolio.router)
app.include_router(market.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
