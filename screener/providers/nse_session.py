"""Polite HTTP client for nseindia.com and its archives host.

NSE's JSON API only answers requests that look like a browser and carry the
cookies its home page sets, so the session warms up once (and again whenever
a call is rejected). Every request waits `nse_request_delay` after the
previous one, and transient failures are retried with backoff.
"""
from __future__ import annotations

import logging
import threading
import time

import requests

from config import settings

log = logging.getLogger("screener.nse")

HOME = "https://www.nseindia.com/"
API = "https://www.nseindia.com/api/"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/129.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": HOME,
}
RETRIES = 3


class NseError(Exception):
    pass


class NseSession:
    def __init__(self, delay: float | None = None):
        self.delay = settings.nse_request_delay if delay is None else delay
        self._s = requests.Session()
        self._s.headers.update(HEADERS)
        self._warm = False
        self._last = 0.0
        self._lock = threading.Lock()

    def _throttle(self) -> None:
        with self._lock:
            wait = self._last + self.delay - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    def _warm_up(self) -> None:
        # The home page often answers 403 but still sets the cookies the API needs.
        self._throttle()
        try:
            self._s.get(HOME, timeout=15)
        except requests.RequestException as exc:
            log.warning("NSE warm-up failed: %s", exc)
        self._warm = True

    def get(self, url: str, timeout: float = 30) -> requests.Response:
        """GET with throttle, cookie warm-up and retries; raises NseError when it gives up."""
        if not self._warm and url.startswith(API):
            self._warm_up()
        last_exc: Exception | None = None
        for attempt in range(RETRIES):
            self._throttle()
            try:
                resp = self._s.get(url, timeout=timeout)
                if resp.status_code in (401, 403) and url.startswith(API):
                    self._warm_up()
                    raise NseError(f"HTTP {resp.status_code}")
                if resp.status_code == 404:
                    raise NseError("HTTP 404")  # not transient; don't retry
                resp.raise_for_status()
                return resp
            except NseError as exc:
                last_exc = exc
                if str(exc) == "HTTP 404":
                    break
            except requests.RequestException as exc:
                last_exc = exc
            time.sleep(2 ** attempt)
        raise NseError(f"GET {url} failed: {last_exc}")

    def get_json(self, path: str) -> dict | list:
        resp = self.get(API + path)
        try:
            return resp.json()
        except ValueError as exc:
            raise NseError(f"GET {path}: not JSON ({exc})") from exc
