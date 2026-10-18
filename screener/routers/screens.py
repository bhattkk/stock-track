"""Run ad-hoc screens and manage saved ones."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db import get_db
from models import Screen
from screen_engine import DEFAULT_COLUMNS, RuleError, compile_rules, run_screen

router = APIRouter(tags=["screens"])

# Seeded on first start so the Screener tab opens on something useful:
# the numeric half of the criteria this service was built for.
DEFAULT_SCREEN = {
    "name": "Quality compounders",
    "rules": {"all": [
        {"ind": "opm_change_3y", "op": ">", "value": 0},
        {"ind": "debt_to_equity", "op": "<", "value": 0.5},
        {"ind": "capex_self_funded_3y", "op": "is_true"},
        {"ind": "roce_3y_avg", "op": ">=", "value": 15},
        {"ind": "roe_3y_avg", "op": ">=", "value": 15},
        {"ind": "cfo_to_pat_5y", "op": ">=", "value": 0.7},
    ]},
    "columns": DEFAULT_COLUMNS,
    "sort": {"key": "roce_3y_avg", "dir": "desc"},
}


class Sort(BaseModel):
    key: str
    dir: str = Field("desc", pattern="^(asc|desc)$")


class RunRequest(BaseModel):
    rules: dict | None = None
    columns: list[str] | None = None
    sort: Sort | None = None
    limit: int = Field(200, ge=1, le=5000)


class ScreenIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    rules: dict
    columns: list[str] = Field(default_factory=list)
    sort: Sort | None = None


def _out(s: Screen) -> dict:
    return {"id": s.id, "name": s.name, "rules": s.rules, "columns": s.columns, "sort": s.sort,
            "updated_at": s.updated_at.isoformat()}


def _validate(rules: dict) -> None:
    try:
        compile_rules(rules)
    except RuleError as exc:
        raise HTTPException(422, str(exc)) from exc


def seed_default_screen(db: Session) -> None:
    if db.scalar(select(Screen.id).limit(1)) is None:
        db.add(Screen(**DEFAULT_SCREEN))
        db.commit()


@router.post("/run")
def run(req: RunRequest, db: Session = Depends(get_db)) -> dict:
    try:
        return run_screen(db, req.rules, req.columns, req.sort.model_dump() if req.sort else None, req.limit)
    except RuleError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/screens")
def list_screens(db: Session = Depends(get_db)) -> list[dict]:
    return [_out(s) for s in db.scalars(select(Screen).order_by(Screen.name))]


@router.post("/screens", status_code=201)
def create_screen(body: ScreenIn, db: Session = Depends(get_db)) -> dict:
    _validate(body.rules)
    screen = Screen(name=body.name.strip(), rules=body.rules, columns=body.columns,
                    sort=body.sort.model_dump() if body.sort else None)
    db.add(screen)
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"A screen named {body.name!r} already exists") from exc
    return _out(screen)


@router.put("/screens/{screen_id}")
def update_screen(screen_id: int, body: ScreenIn, db: Session = Depends(get_db)) -> dict:
    screen = db.get(Screen, screen_id)
    if screen is None:
        raise HTTPException(404, "Screen not found")
    _validate(body.rules)
    screen.name, screen.rules, screen.columns = body.name.strip(), body.rules, body.columns
    screen.sort = body.sort.model_dump() if body.sort else None
    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        raise HTTPException(409, f"A screen named {body.name!r} already exists") from exc
    return _out(screen)


@router.delete("/screens/{screen_id}", status_code=204)
def delete_screen(screen_id: int, db: Session = Depends(get_db)) -> Response:
    screen = db.get(Screen, screen_id)
    if screen is None:
        raise HTTPException(404, "Screen not found")
    db.delete(screen)
    db.commit()
    return Response(status_code=204)
