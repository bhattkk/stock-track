"""Environment-driven configuration for the analysis service.

Values come from the repo-root `.env` (shared with the other services) or the
environment; docker-compose overrides the Redis / Postgres hosts so the
container reaches the other containers instead of localhost.
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parent


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=(ROOT.parent / ".env", ".env"), extra="ignore")

    redis_url: str = "redis://localhost:6379/0"
    # Same Postgres database as the other services. Every table this service
    # owns is prefixed `analysis_`; it never writes to anyone else's tables.
    database_url: str = "postgresql+psycopg://portfolio:portfolio@localhost:5432/portfolio"
    upstox_access_token: str = ""

    # Seconds between Upstox candle requests. Upstox allows ~2,000 requests per
    # 30 minutes per user, and market-data / portfolio share the same token, so
    # 1.1 s (~1,640 per 30 min) leaves headroom. A full run over ~2,700 stocks
    # therefore takes ~50 minutes — fine for a Saturday-morning batch.
    upstox_request_delay: float = 1.1

    # How much daily history to keep per stock. 52-week high/low and the
    # 200-day average need ~250 trading days; 420 calendar days ≈ 290 bars.
    history_days: int = 420

    # Weekly run: day of week + hour, Asia/Kolkata. Saturday so the week's
    # last candle (Friday) is final.
    schedule_day_of_week: str = "sat"
    schedule_hour: int = 7
    schedule_enabled: bool = True

    # The rule file. Edit it and the next run uses the new rules; each run
    # stores a hash of the file so results can be traced to the rules used.
    criteria_file: Path = ROOT / "criteria" / "technicals.yaml"

    # Upstox instrument key for the benchmark used by relative strength.
    benchmark_key: str = "NSE_INDEX|Nifty 50"


settings = Settings()

IST = ZoneInfo("Asia/Kolkata")


def ist_today() -> date:
    """Today's date in India. The container clock is UTC, and NSE sessions are IST days."""
    return datetime.now(IST).date()
