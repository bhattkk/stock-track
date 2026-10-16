"""Upstox Company Fundamentals API → one normalized dict per instrument.

Three endpoints, all keyed by ISIN (see portfolio/README.md):

  /v2/fundamentals/{isin}/key-ratios      P/E (TTM), P/B, ROA, ROE, ROCE, EV/EBITDA vs sector
  /v2/fundamentals/{isin}/profile         sector + company description
  /v2/fundamentals/{isin}/share-holdings  promoter/FII/DII/... % by quarter

Normalized shape (any part may be missing if its call failed):

  {"sector": str, "profile": str,
   "ratios": [{"name", "company", "sector", "unit"}],          # numbers or None; unit "" or "%"
   "shareholding": {"periods": [...newest first], "rows": [{"category", "values"}]}}
"""
from __future__ import annotations

import logging

from upstox_client import upstox_get

log = logging.getLogger("portfolio.upstox_fundamentals")

PATH = "/v2/fundamentals/{isin}/{endpoint}"

# Display order + labels for share-holdings categories; unknown ones go last.
SHAREHOLDING_LABELS = {
    "promoters": "Promoters",
    "fii": "FII",
    "mutual_funds": "Mutual funds",
    "other_dii": "Other DII",
    "retail_and_other": "Retail & others",
}


def _parse_ratio(raw) -> tuple[float | None, str]:
    """'4.39%' -> (4.39, '%'); '20.15' -> (20.15, ''); junk/None -> (None, '')."""
    if raw is None:
        return None, ""
    text = str(raw).strip().replace(",", "")
    unit = "%" if text.endswith("%") else ""
    try:
        return float(text.rstrip("%")), unit
    except ValueError:
        return None, unit


def _normalize_ratios(data) -> list[dict]:
    ratios = []
    for r in data if isinstance(data, list) else []:
        company, unit = _parse_ratio(r.get("company_value"))
        sector, sector_unit = _parse_ratio(r.get("sector_value"))
        ratios.append({"name": r.get("name", ""), "company": company, "sector": sector, "unit": unit or sector_unit})
    return ratios


def _normalize_shareholding(data) -> dict | None:
    if not isinstance(data, list) or not data:
        return None
    # Union of periods across categories, in the API's newest-first order.
    periods: list[str] = []
    for cat in data:
        for h in cat.get("history", []):
            if h.get("period") not in periods:
                periods.append(h.get("period"))
    order = list(SHAREHOLDING_LABELS)
    rows = []
    for cat in sorted(data, key=lambda c: order.index(c.get("category")) if c.get("category") in order else len(order)):
        by_period = {h.get("period"): h.get("value") for h in cat.get("history", [])}
        rows.append({
            "category": SHAREHOLDING_LABELS.get(cat.get("category"), str(cat.get("category", "")).replace("_", " ").title()),
            "values": [by_period.get(p) for p in periods],
        })
    return {"periods": periods, "rows": rows}


def fetch_fundamentals(isin: str, token: str) -> dict | None:
    """Fetch and normalize all three endpoints; None only if every call failed."""
    ratios = upstox_get(PATH.format(isin=isin, endpoint="key-ratios"), token)
    profile = upstox_get(PATH.format(isin=isin, endpoint="profile"), token)
    holdings = upstox_get(PATH.format(isin=isin, endpoint="share-holdings"), token)
    if ratios is None and profile is None and holdings is None:
        return None

    out: dict = {}
    if ratios is not None:
        out["ratios"] = _normalize_ratios(ratios)
    if isinstance(profile, dict):
        out["sector"] = profile.get("sector") or None
        out["profile"] = profile.get("company_profile") or None
    if holdings is not None:
        out["shareholding"] = _normalize_shareholding(holdings)
    return out
