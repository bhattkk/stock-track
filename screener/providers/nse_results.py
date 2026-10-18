"""NSE quarterly results (XBRL) — the main fundamentals source.

Two listings, merged:

  /api/integrated-filing-results          SEBI Integrated Filing, Dec 2024 onwards
  /api/corporates-financial-results       legacy results filings, up to Dec 2024

Each row gives the quarter end, standalone/consolidated and an XBRL URL on
nsearchives.nseindia.com. A Q4 filing also carries the full-year P&L, the
year-end balance sheet and the full-year cash flow; a Q2 filing carries the
half-year balance sheet and cash flow. Revisions are separate rows — the
latest one per (quarter, basis) wins (see refresh.select_filings).
"""
from __future__ import annotations

import logging
from datetime import date, datetime
from pathlib import Path
from urllib.parse import quote

from config import settings
from providers.base import FactRow, FilingRef, FundamentalsProvider
from providers.nse_session import NseSession
from providers.xbrl import parse_xbrl

log = logging.getLogger("screener.nse_results")

INTEGRATED_PATH = "integrated-filing-results?index=equities&symbol={symbol}&size=100"
LEGACY_PATH = "corporates-financial-results?index=equities&symbol={symbol}&period=Quarterly"
INTEGRATED_FINANCIALS = "Integrated Filing- Financials"
# The legacy listing stops at the Dec 2024 quarter, so a stock whose stored
# history already reaches it never needs that call again.
LEGACY_LAST_QUARTER = date(2024, 12, 31)
FINANCIAL_URL_MARKERS = ("BANKING", "NBFC", "INSURANCE")


def _parse_date(text: str | None) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%B-%Y"):
        try:
            return datetime.strptime(text.strip().title(), fmt).date()
        except (AttributeError, ValueError):
            continue
    return None


def _parse_datetime(text: str | None) -> datetime | None:
    try:
        return datetime.strptime(text.strip(), "%d-%b-%Y %H:%M:%S")
    except (AttributeError, ValueError):
        return None


def _basis(text: str | None) -> str:
    return "consolidated" if (text or "").strip().lower() == "consolidated" else "standalone"


class NseResultsProvider(FundamentalsProvider):
    name = "nse_results"

    def __init__(self, session: NseSession, raw_dir: str | None = None):
        self.session = session
        self.raw_dir = Path(raw_dir or settings.raw_dir) / "xbrl"

    def list_filings(self, symbol: str, since: date | None) -> list[FilingRef]:
        sym = quote(symbol, safe="")
        refs = self._integrated(sym)
        if since is None or since < LEGACY_LAST_QUARTER:
            refs += self._legacy(sym)
        return refs

    def _integrated(self, sym: str) -> list[FilingRef]:
        data = self.session.get_json(INTEGRATED_PATH.format(symbol=sym))
        rows = data.get("data", []) if isinstance(data, dict) else []
        refs = []
        for r in rows:
            url, end = r.get("xbrl") or "", _parse_date(r.get("qe_Date"))
            if r.get("type") != INTEGRATED_FINANCIALS or not url.startswith("http") or not end:
                continue
            refs.append(FilingRef(
                url=url, period_end=end, basis=_basis(r.get("consolidated")),
                filed_at=_parse_datetime(r.get("creation_Date") or r.get("broadcast_Date")),
                is_financial=any(m in url.upper() for m in FINANCIAL_URL_MARKERS),
            ))
        return refs

    def _legacy(self, sym: str) -> list[FilingRef]:
        data = self.session.get_json(LEGACY_PATH.format(symbol=sym))
        refs = []
        for r in data if isinstance(data, list) else []:
            url, end = r.get("xbrl") or "", _parse_date(r.get("toDate"))
            if not url.startswith("http") or not end:
                continue
            refs.append(FilingRef(
                url=url, period_end=end, basis=_basis(r.get("consolidated")),
                filed_at=_parse_datetime(r.get("broadCastDate")),
                is_financial=(r.get("bank") or "N") != "N" or any(m in url.upper() for m in FINANCIAL_URL_MARKERS),
            ))
        return refs

    def cache_path(self, url: str) -> Path:
        return self.raw_dir / url.rsplit("/", 1)[-1]

    def fetch_facts(self, filing: FilingRef) -> tuple[list[FactRow], bool]:
        path = self.cache_path(filing.url)
        if path.exists():
            facts, is_bank = parse_xbrl(path.read_bytes())
        else:
            content = self.session.get(filing.url).content
            facts, is_bank = parse_xbrl(content)  # parse before caching so an error page never lands in the cache
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
        return facts, is_bank or filing.is_financial
