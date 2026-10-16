"""Minimal authenticated GET against the Upstox API.

Shared by upstox_history.py and upstox_fundamentals.py. Uses the read-only
Analytics token (UPSTOX_ACCESS_TOKEN), which Upstox allows for historical
candles and the fundamentals endpoints.
"""
from __future__ import annotations

import logging

import requests

log = logging.getLogger("portfolio.upstox")

BASE_URL = "https://api.upstox.com"


def upstox_get(path: str, token: str) -> dict | list | None:
    """GET ``BASE_URL + path`` and return the response's ``data``.

    Returns None — after logging why — on a missing token, HTTP/network
    error or malformed body, so callers can fall back to cached values.
    """
    if not token:
        log.warning("UPSTOX_ACCESS_TOKEN not set; skipping %s", path)
        return None
    try:
        resp = requests.get(
            BASE_URL + path,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        return resp.json()["data"]
    except (requests.RequestException, KeyError, ValueError) as exc:
        log.warning("Upstox GET %s failed: %s", path, exc)
        return None
