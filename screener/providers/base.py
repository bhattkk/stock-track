"""Provider-agnostic interface for fundamentals sources.

A provider is the only place that knows a vendor's endpoints and formats.
It lists a stock's filings and turns one filing into neutral `FactRow`s;
refresh.py stores those in screener_facts and the indicators only ever read
facts — so a new source (BSE, BRSR, a paid feed) is one new provider.
"""
from __future__ import annotations

import abc
from dataclasses import dataclass
from datetime import date, datetime


@dataclass(frozen=True)
class FilingRef:
    url: str
    period_end: date
    basis: str  # "consolidated" | "standalone"
    filed_at: datetime | None
    is_financial: bool = False  # bank / NBFC / insurance format


@dataclass(frozen=True)
class FactRow:
    period_type: str  # Q | H | M9 | FY | I
    period_end: date
    metric: str
    value: float


class FundamentalsProvider(abc.ABC):
    name: str = "base"

    @abc.abstractmethod
    def list_filings(self, symbol: str, since: date | None) -> list[FilingRef]:
        """Filings for ``symbol``; ``since`` (if given) lets the provider skip older listings."""

    @abc.abstractmethod
    def fetch_facts(self, filing: FilingRef) -> tuple[list[FactRow], bool]:
        """Download (or read from cache) and parse one filing.

        Returns (facts, is_financial) — the flag marks a bank/NBFC/insurer
        format detected from the content.
        """
