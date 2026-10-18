"""Logging setup for the screener service.

Logs go to stdout (so `docker compose up` / `docker compose logs` show them
live) *and* to a single file that is appended to across restarts — not
re-created per run — so history survives a container recreate:

    <repo_root>/logs/screener.log

In docker-compose, LOG_DIR is set to /app/logs, which is bind-mounted from
the repo root's ./logs so the file lands on the host. Outside docker (e.g.
running `uvicorn main:app` directly), it defaults to ../logs relative to
this file, i.e. the repo root next to this service's own directory.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

SERVICE_NAME = "screener"
DEFAULT_LOG_DIR = Path(__file__).resolve().parent.parent / "logs"
LOG_DIR = Path(os.environ.get("LOG_DIR", str(DEFAULT_LOG_DIR)))
LOG_FILE = LOG_DIR / f"{SERVICE_NAME}.log"


def setup_logging() -> None:
    """Configure the root logger once, at process startup."""
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    file_handler = logging.FileHandler(LOG_FILE, mode="a")
    file_handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers.clear()
    root.addHandler(console_handler)
    root.addHandler(file_handler)

    logging.getLogger(SERVICE_NAME).info("Logging to %s", LOG_FILE)
