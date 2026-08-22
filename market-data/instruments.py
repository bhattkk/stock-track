"""Load the NSE equity instrument master from a provider into Redis."""
from __future__ import annotations

import logging

from providers.base import MarketDataProvider
from store import Store

log = logging.getLogger("market-data.instruments")


def refresh_instruments(provider: MarketDataProvider, store: Store) -> int:
    """Fetch the equity instrument master from the provider and persist it."""
    log.info("Refreshing instrument master from provider=%s", provider.name)
    instruments = provider.get_instruments()
    count = store.save_instruments(instruments)
    log.info("Stored %d instruments in Redis", count)
    return count
