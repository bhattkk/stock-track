"""Screener service entrypoint.

Screens every NSE stock on fundamentals computed from its XBRL results
filings (see README.md). Runs only on demand: it isn't part of the default
`docker compose up` (compose profile "screener"), has no scheduler, and
fetches new data only when a refresh is triggered — POST /refresh from the
UI, or `python cli.py refresh`.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from db import SessionLocal, init_schema
from logging_config import setup_logging
from refresh import mark_interrupted_runs
from routers import indicators, refresh, screens, stocks
from routers.screens import seed_default_screen

setup_logging()
log = logging.getLogger("screener")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("Starting screener service")
    init_schema()
    mark_interrupted_runs()
    with SessionLocal() as db:
        seed_default_screen(db)
    log.info("Postgres schema ready")
    yield
    log.info("Stopped.")


app = FastAPI(title="Screener Service", lifespan=lifespan)
app.include_router(indicators.router)
app.include_router(screens.router)
app.include_router(stocks.router)
app.include_router(refresh.router)


@app.get("/health", tags=["health"])
def health() -> dict[str, str]:
    return {"status": "ok"}
