"""Environment-driven configuration for the portfolio service."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Postgres — owns watchlists + portfolio.
    database_url: str = "postgresql+psycopg://portfolio:portfolio@localhost:5432/portfolio"

    # Redis — shared with market-data: read INSTRUMENTS for search, write
    # SUBSCRIBED_INSTRUMENTS so market-data starts polling new instruments.
    redis_url: str = "redis://localhost:6379/0"

    # Search
    search_result_limit: int = 5

    # Upstox Analytics token (same one market-data uses) — read-only access to
    # historical daily candles for the trailing-SL peak (see peaks.py).
    upstox_access_token: str = ""

    # Postgres backups (pg_dump on a timer, so a redeploy doesn't start from
    # scratch). Disabled entirely if backup_interval_minutes <= 0.
    backup_dir: str = "/backups"
    backup_interval_minutes: int = 10
    backup_retention: int = 12  # how many timestamped dumps to keep


settings = Settings()
