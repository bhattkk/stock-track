"""Upstox provider (read-only market data via the year-long Analytics Token).

Upstox's Analytics Token is generated once from the Developer Apps dashboard
(account.upstox.com/developer/apps -> Analytics tab -> Generate Token) with no
interactive OAuth login. It is valid for a year and scoped to GET-only
market-data endpoints, so this provider only needs UPSTOX_ACCESS_TOKEN.

Data:
  1. Instruments — Upstox's daily "BOD" (Beginning-Of-Day) full instrument
     dump for NSE, fetched fresh from assets.upstox.com and filtered down to
     equities. Upstox's own "instrument_key" field (e.g.
     "NSE_EQ|INE733E01010") is used as this system's instrument_id
     everywhere, so no local file or id-translation step is needed.
  2. Quotes — the v3 LTP endpoint, keyed by instrument_id (Upstox's
     "instrument_key"). Its response is keyed by "EXCHANGE:SYMBOL", but each
     row's "instrument_token" field echoes back the exact instrument_id we
     requested (verified against the live API for equities), so we map
     results with that instead of parsing the response key.
"""
from __future__ import annotations

import gzip
import json
import logging
import time

import requests

from config import Settings
from providers.base import Instrument, MarketDataProvider, Quote

BOD_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
LTP_URL = "https://api.upstox.com/v3/market-quote/ltp"

log = logging.getLogger("market-data.upstox")


class UpstoxProvider(MarketDataProvider):
    name = "upstox"

    def __init__(self, settings: Settings):
        self.settings = settings
        self._session: requests.Session | None = None

    # -- auth ---------------------------------------------------------------
    def authenticate(self) -> None:
        if not self.settings.upstox_access_token:
            raise RuntimeError(
                "UPSTOX_ACCESS_TOKEN is empty. Generate a year-long Analytics "
                "Token at account.upstox.com/developer/apps -> Analytics tab -> "
                "Generate Token."
            )
        session = requests.Session()
        session.headers.update(
            {
                "Authorization": f"Bearer {self.settings.upstox_access_token}",
                "Accept": "application/json",
            }
        )
        self._session = session

    # -- instruments --------------------------------------------------------
    def get_instruments(self) -> list[Instrument]:
        resp = requests.get(BOD_URL, timeout=60)
        resp.raise_for_status()
        rows = json.loads(gzip.decompress(resp.content))

        out: list[Instrument] = []
        for row in rows:
            if row.get("instrument_type") != "EQ":
                continue
            out.append(
                Instrument(
                    instrument_id=row["instrument_key"],
                    symbol=row.get("trading_symbol", ""),
                    name=row.get("name", ""),
                    exchange=row.get("exchange", "NSE"),
                    segment=row.get("segment", ""),
                    instrument_type=row.get("instrument_type", ""),
                    isin=row.get("isin", ""),
                )
            )
        return out

    # -- quotes -------------------------------------------------------------
    def get_ltp_quotes(self, instrument_ids: list[str]) -> dict[str, Quote]:
        if not self._session:
            raise RuntimeError("UpstoxProvider.authenticate() must run first")
        resp = self._session.get(
            LTP_URL,
            params={"instrument_key": ",".join(instrument_ids)},
            timeout=15,
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("status") != "success":
            raise RuntimeError(f"Upstox LTP error: {payload}")

        now = int(time.time())
        out: dict[str, Quote] = {}
        for row in payload.get("data", {}).values():
            instrument_id = row.get("instrument_token")
            if not instrument_id:
                continue
            out[instrument_id] = Quote(
                instrument_id=instrument_id,
                ltp=float(row.get("last_price", 0) or 0),
                cp=float(row.get("cp", 0) or 0),
                volume=int(row.get("volume", 0) or 0),
                ltq=int(row.get("ltq", 0) or 0),
                ts=now,
            )
        return out
