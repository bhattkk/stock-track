"""Provider-agnostic interface and data models.

A provider is the only place that knows vendor-specific details (auth,
endpoints, response shapes). Everything downstream works with the neutral
``Instrument``/``Quote`` models below, keyed by ``instrument_id`` — the
provider's own instrument identifier (e.g. Upstox's ISIN-based
"NSE_EQ|INE733E01010") — which this system uses as its instrument id
throughout, so no separate id scheme or resolution step is needed.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass


@dataclass
class Instrument:
    instrument_id: str
    symbol: str            # trading symbol, e.g. "NTPC"
    name: str = ""
    exchange: str = "NSE"
    segment: str = ""
    instrument_type: str = ""
    isin: str = ""


@dataclass
class Quote:
    instrument_id: str
    ltp: float = 0.0        # last traded price
    cp: float = 0.0         # previous close
    volume: int = 0         # cumulative volume for the day
    ltq: int = 0            # last traded quantity
    ts: int = 0             # epoch seconds when captured


class MarketDataProvider(abc.ABC):
    """Interface every market-data source must implement."""

    name: str = "base"

    @abc.abstractmethod
    def authenticate(self) -> None:
        """Establish/refresh whatever session or token the provider needs."""

    @abc.abstractmethod
    def get_instruments(self) -> list[Instrument]:
        """Return the full equity instrument master."""

    @abc.abstractmethod
    def get_ltp_quotes(self, instrument_ids: list[str]) -> dict[str, Quote]:
        """Fetch latest LTP quotes for a batch, keyed by instrument_id.

        The batch size is controlled by the caller (ingest loop) to respect
        provider rate limits; implementations should assume
        ``instrument_ids`` is already an appropriately sized chunk.
        """
