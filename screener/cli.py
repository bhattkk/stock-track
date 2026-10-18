"""Command-line refreshes, for when the UI isn't the right tool (e.g. the
first multi-hour backfill):

    docker compose --profile screener run --rm screener python cli.py refresh
    docker compose --profile screener run --rm screener python cli.py refresh --symbols ASIANPAINT,TCS
    docker compose --profile screener run --rm screener python cli.py refresh --full
    docker compose --profile screener run --rm screener python cli.py compute
    docker compose --profile screener run --rm screener python cli.py reparse
"""
from __future__ import annotations

import argparse
import logging

from compute import compute_all
from db import SessionLocal, init_schema
from logging_config import setup_logging
from providers.nse_results import NseResultsProvider
from providers.nse_session import NseSession
from refresh import RefreshBusy, create_run, run_refresh, reparse_cached

log = logging.getLogger("screener.cli")


def main() -> int:
    parser = argparse.ArgumentParser(description="Screener data refresh")
    sub = parser.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("refresh", help="Fetch new NSE data, then recompute indicators")
    r.add_argument("--symbols", help="Comma-separated NSE symbols (default: every stock that may have new results)")
    r.add_argument("--full", action="store_true", help="Re-list and re-parse every filing, ignoring what's stored")
    sub.add_parser("compute", help="Recompute indicators from stored facts (no NSE calls)")
    sub.add_parser("reparse", help="Re-parse cached XBRL files (after a tag-mapping change), then recompute")
    args = parser.parse_args()

    setup_logging()
    init_schema()

    if args.cmd == "reparse":
        with SessionLocal() as db:
            n = reparse_cached(db, NseResultsProvider(NseSession()))
            log.info("Re-parsed %d filing(s)", n)
            compute_all(db)
        return 0

    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()] if getattr(args, "symbols", None) else None
    full = getattr(args, "full", False)
    compute_only = args.cmd == "compute"
    try:
        run_id = create_run({"symbols": symbols, "full": full, "compute_only": compute_only, "source": "cli"})
    except RefreshBusy as exc:
        log.error("%s", exc)
        return 1
    run_refresh(run_id, symbols, full, compute_only)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
