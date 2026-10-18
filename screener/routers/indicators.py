"""GET /indicators — the registry, which drives the UI's filter builder."""
from __future__ import annotations

from fastapi import APIRouter

from indicators import CATEGORIES, REGISTRY

router = APIRouter(tags=["indicators"])


@router.get("/indicators")
def list_indicators() -> dict:
    known = {c for c, _ in CATEGORIES}
    extra = sorted({i.category for i in REGISTRY.values()} - known)  # new categories still show, at the end
    return {
        "categories": [{"key": k, "label": label} for k, label in CATEGORIES] +
                      [{"key": k, "label": k.replace("_", " ").title()} for k in extra],
        "indicators": [ind.meta() for ind in REGISTRY.values()],
    }
