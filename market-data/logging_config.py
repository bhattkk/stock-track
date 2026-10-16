"""Logging setup for the market-data service.

Logs go to stdout (so `docker compose up` / `docker compose logs` show them
live) *and* to a single file that is appended to across restarts — not
re-created per run — so history survives a container recreate:

    <repo_root>/logs/market-data.log

In docker-compose, LOG_DIR is set to /app/logs, which is bind-mounted from
the repo root's ./logs so the file lands on the host. Outside docker (e.g.
running `python main.py` directly), it defaults to ../logs relative to this
file, i.e. the repo root next to this service's own directory.

The sweep that polls Upstox runs every `poll_interval_seconds` (60s by
default), so per-batch/per-quote detail is deliberately *not* logged at INFO
— it would drown out everything else. Set `settings.verbose` (env var
VERBOSE=true) to drop the level to DEBUG and see that detail when you
actually need it.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

SERVICE_NAME = "market-data"
DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR = Path(os.environ.get("LOG_DIR", str(DEFAULT_LOG_DIR)))
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"


def setup_logging(verbose: bool = False) -> None:
    """Configure the root logger once, at process startup."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, mode="a")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(level)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.getLogger(SERVICE_NAME).info(
        "Logging to %s (level=%s, verbose=%s)", LOG_FILE, logging.getLevelName(level), verbose
    )
