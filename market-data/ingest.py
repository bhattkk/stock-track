"""One sweep of subscribed instruments: batched LTP fetch -> Redis."""
from __future__ import annotations

import logging
import time

from config import Settings
from providers.base import MarketDataProvider
from store import Store

log = logging.getLogger("market-data.ingest")


def _chunks(items: list[str], size: int):
    for i in range(0, len(items), size):
        yield i, items[i : i + size]


def run_sweep(provider: MarketDataProvider, store: Store, settings: Settings) -> int:
    """Fetch LTP quotes for every subscribed instrument, one batch at a time.

    Returns the number of quotes written. Batch failures are logged and skipped
    so a single bad batch never aborts the whole sweep.
    """
    subscriptions = store.get_subscriptions()
    if not subscriptions:
        log.warning(
            "No instrument subscriptions in Redis (%s); skipping sweep",
            Store.SUBSCRIBED_KEY,
        )
        return 0

    total = 0
    started = time.time()
    for offset, batch in _chunks(subscriptions, settings.quote_batch_size):
        try:
            quotes = provider.get_ltp_quotes(batch)
            store.save_prices(quotes)
            total += len(quotes)
        except Exception:  # noqa: BLE001 - keep the sweep resilient
            log.exception("Batch at offset %d failed; skipping", offset)
        time.sleep(settings.batch_pause_seconds)

    log.info("Sweep complete: %d/%d quotes in %.1fs", total, len(subscriptions), time.time() - started)
    return total
