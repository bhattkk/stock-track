"""SQLAlchemy engine/session setup for the analysis service."""
from __future__ import annotations

from typing import Iterator

from sqlalchemy import create_engine
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


def init_schema() -> None:
    """Create missing analysis_* tables (never alters or drops anything)."""
    import models  # noqa: F401  (registers the tables on Base.metadata)

    Base.metadata.create_all(engine)
