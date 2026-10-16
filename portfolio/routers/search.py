"""SearchStock — search the Redis instrument master market-data maintains."""
from __future__ import annotations

from fastapi import APIRouter, Depends, Query

from config import settings
from deps import get_redis_store
from redis_store import RedisStore
from schemas import SearchResult

router = APIRouter(tags=["search"])


@router.get("/search", response_model=list[SearchResult])
def search_stock(
    q: str = Query(min_length=1, description="Symbol or name fragment, e.g. 'NTPC'"),
    store: RedisStore = Depends(get_redis_store),
) -> list[SearchResult]:
    """Match ``q`` against symbol/name in Redis' INSTRUMENTS hash.

    Returns at most ``search_result_limit`` (default 5) results, symbol
    matches ranked above name-only matches.
    """
    matches = store.search_instruments(q, settings.search_result_limit)
    return [
        SearchResult(
            instrument_id=m["instrument_id"],
            symbol=m.get("symbol", ""),
            name=m.get("name", ""),
        )
        for m in matches
    ]
