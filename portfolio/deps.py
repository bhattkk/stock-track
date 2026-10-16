"""Shared FastAPI dependencies."""
from __future__ import annotations

from functools import lru_cache

from config import settings
from redis_store import RedisStore


@lru_cache
def get_redis_store() -> RedisStore:
    """One RedisStore (and one underlying connection pool) per process."""
    return RedisStore(settings.redis_url)
