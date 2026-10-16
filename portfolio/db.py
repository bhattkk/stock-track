"""SQLAlchemy engine/session setup for the portfolio service."""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import settings

engine = create_engine(settings.database_url, pool_pre_ping=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


def get_db() -> Iterator[Session]:
    """FastAPI dependency: one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# create_all() only creates missing tables — it never alters existing ones.
# Columns added after a table first shipped go here; every statement must be
# idempotent since this runs on each startup.
_COLUMN_MIGRATIONS = [
    "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS notes TEXT",
    "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS sell_date DATE",
    "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS sell_price NUMERIC(14, 4)",
    "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS peak_close NUMERIC(14, 4)",
    "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS peak_close_date DATE",
    "ALTER TABLE portfolio_items ADD COLUMN IF NOT EXISTS peak_refreshed_on DATE",
]


def init_schema() -> None:
    """Create missing tables, then apply additive column migrations."""
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        for stmt in _COLUMN_MIGRATIONS:
            conn.execute(text(stmt))
