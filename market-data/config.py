"""Environment-driven configuration for the market-data service."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # Sweep pacing
    poll_interval_seconds: int = 60   # gap between subscription sweeps
    quote_batch_size: int = 10        # instrument keys per LTP request
    batch_pause_seconds: float = 1.0  # pause between batches within a sweep
    instrument_refresh_hour: int = 8  # local hour to refresh the instrument master

    # Upstox — year-long, read-only Analytics Token (no OAuth flow needed)
    upstox_access_token: str = ""


settings = Settings()
