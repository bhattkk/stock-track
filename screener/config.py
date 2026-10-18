"""Environment-driven configuration for the screener service."""
from __future__ import annotations

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # Same Postgres database as the portfolio service; every table this
    # service owns is prefixed `screener_` and it never touches the others.
    database_url: str = "postgresql+psycopg://portfolio:portfolio@localhost:5432/portfolio"

    # Raw XBRL files are cached here so a tag-mapping fix can re-parse them
    # (`python cli.py reparse`) without re-downloading from NSE.
    raw_dir: str = "/data/raw"

    # Pause between consecutive NSE requests. NSE blocks aggressive clients;
    # at 0.4 s the first full backfill (~2,300 stocks) takes a few hours,
    # later refreshes only fetch what's new.
    nse_request_delay: float = 0.4

    # How much history a refresh keeps per stock: FY results for growth/
    # averages, quarters for TTM numbers.
    history_years: int = 6
    history_quarters: int = 8

    # Stock universe: NSE equity series to include (EQ = rolling settlement,
    # BE/BZ = trade-for-trade).
    series: str = "EQ,BE"

    @property
    def series_list(self) -> list[str]:
        return [s.strip() for s in self.series.split(",") if s.strip()]


settings = Settings()
