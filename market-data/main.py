"""Market-data microservice entrypoint.

Startup sequence:
  1. Connect to Redis.
  2. Build + authenticate the Upstox provider.
  3. Load the equity instrument master into Redis (if not already present).
  4. Schedule: a daily instrument-master refresh + a recurring sweep that
     polls SUBSCRIBED_INSTRUMENTS (owned by another service), batches in
     groups of `quote_batch_size`, fetches LTP quotes, and writes them into
     INSTRUMENT_PRICES.
  5. Block until SIGINT/SIGTERM.
"""
from __future__ import annotations

import logging
import signal
import threading
from datetime import datetime

from apscheduler.schedulers.background import BackgroundScheduler

from config import settings
from ingest import run_sweep
from instruments import refresh_instruments
from providers import build_provider
from store import Store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
)
log = logging.getLogger("market-data")


def main() -> None:
    log.info("Starting market-data service")

    store = Store(settings.redis_url)
    store.ping()
    log.info("Connected to Redis at %s", settings.redis_url)

    provider = build_provider(settings)
    provider.authenticate()
    log.info("Provider %s authenticated", provider.name)

    # Ensure the instrument master exists before the first sweep.
    if store.instrument_count() == 0:
        refresh_instruments(provider, store)

    scheduler = BackgroundScheduler(timezone="Asia/Kolkata")
    # Daily instrument master refresh.
    scheduler.add_job(
        refresh_instruments,
        "cron",
        hour=settings.instrument_refresh_hour,
        minute=0,
        args=[provider, store],
        id="instrument_refresh",
    )
    # Recurring subscription sweep, starting immediately.
    scheduler.add_job(
        run_sweep,
        "interval",
        seconds=settings.poll_interval_seconds,
        args=[provider, store, settings],
        id="quote_sweep",
        next_run_time=datetime.now(),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()
    log.info(
        "Scheduler started: sweep every %ss, instrument refresh at %02d:00 IST",
        settings.poll_interval_seconds,
        settings.instrument_refresh_hour,
    )

    stop = threading.Event()

    def shutdown(*_):
        log.info("Shutting down...")
        scheduler.shutdown(wait=False)
        stop.set()

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)

    stop.wait()
    log.info("Stopped.")


if __name__ == "__main__":
    main()
