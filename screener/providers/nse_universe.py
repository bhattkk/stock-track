"""The stock universe and latest closes, straight from NSE (no market-data needed).

  EQUITY_L.csv   every listed equity: symbol, name, series, ISIN
  BhavCopy       end-of-day prices for the whole cash market, one zip per day
"""
from __future__ import annotations

import csv
import io
import logging
import zipfile
from dataclasses import dataclass
from datetime import date, timedelta

from providers.nse_session import NseError, NseSession

log = logging.getLogger("screener.nse_universe")

EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BHAVCOPY_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip"
BHAVCOPY_LOOKBACK_DAYS = 10  # covers long weekends / holiday runs
PRICE_SERIES = {"EQ", "BE", "BZ"}  # skip block-deal / other series rows for the same ISIN


@dataclass(frozen=True)
class ListedStock:
    symbol: str
    name: str
    series: str
    isin: str

    @property
    def instrument_id(self) -> str:
        return f"NSE_EQ|{self.isin}"


def fetch_equity_list(session: NseSession, series: list[str]) -> list[ListedStock]:
    text = session.get(EQUITY_LIST_URL).content.decode("utf-8-sig")
    reader = csv.DictReader(io.StringIO(text))
    reader.fieldnames = [f.strip() for f in reader.fieldnames or []]
    stocks = []
    for row in reader:
        row = {k: (v or "").strip() for k, v in row.items()}
        if row.get("SERIES") in series and row.get("ISIN NUMBER"):
            stocks.append(ListedStock(row["SYMBOL"], row["NAME OF COMPANY"], row["SERIES"], row["ISIN NUMBER"]))
    return stocks


def fetch_latest_closes(session: NseSession, today: date | None = None) -> tuple[dict[str, float], date | None]:
    """{ISIN: close} from the most recent available bhavcopy, and its trade date."""
    day = today or date.today()
    for _ in range(BHAVCOPY_LOOKBACK_DAYS):
        if day.weekday() < 5:
            try:
                content = session.get(BHAVCOPY_URL.format(ymd=day.strftime("%Y%m%d"))).content
            except NseError:
                day -= timedelta(days=1)
                continue
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                text = zf.read(zf.namelist()[0]).decode("utf-8")
            closes = {}
            for row in csv.DictReader(io.StringIO(text)):
                if row.get("SctySrs") not in PRICE_SERIES:
                    continue
                try:
                    closes[row["ISIN"]] = float(row["ClsPric"])
                except (KeyError, ValueError):
                    continue
            return closes, day
        day -= timedelta(days=1)
    log.warning("No bhavcopy found in the last %d days", BHAVCOPY_LOOKBACK_DAYS)
    return {}, None
