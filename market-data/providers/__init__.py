"""Market-data provider implementations and factory."""
from __future__ import annotations

from config import Settings
from providers.base import MarketDataProvider


def build_provider(settings: Settings) -> MarketDataProvider:
    """Instantiate the configured provider."""
    from providers.upstox import UpstoxProvider

    return UpstoxProvider(settings)
