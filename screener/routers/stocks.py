"""GET /stocks/{instrument_id} — everything the screener knows about one stock."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from compute import merge_bases
from db import get_db
from indicators import REGISTRY
from indicators.base import Financials, crores, rnd
from models import Fact, IndicatorValue, Stock

router = APIRouter(tags=["stocks"])

# Statement lines shown in the stock drawer: (metric, label, unit).
YEARLY_LINES = [
    ("revenue", "Revenue", "cr"), ("ebitda", "Operating profit", "cr"), ("opm", "OPM", "%"),
    ("gross_margin", "Gross margin", "%"), ("profit", "Net profit", "cr"), ("eps", "EPS", "₹"),
    ("cfo", "Cash from operations", "cr"), ("capex", "Capex", "cr"),
    ("debt", "Borrowings", "cr"), ("shareholders_equity", "Equity", "cr"),
]
QUARTERLY_LINES = [
    ("revenue", "Revenue", "cr"), ("ebitda", "Operating profit", "cr"), ("opm", "OPM", "%"),
    ("profit", "Net profit", "cr"),
]


def _fmt(v, unit):
    return crores(v) if unit == "cr" else rnd(v)


def _table(periods, lines, label):
    return {
        "periods": [label(p) for p in periods],
        "rows": [{"metric": m, "label": name, "unit": unit, "values": [_fmt(p.get(m), unit) for p in periods]}
                 for m, name, unit in lines],
    }


@router.get("/stocks/{instrument_id}")
def get_stock(instrument_id: str, db: Session = Depends(get_db)) -> dict:
    stock = db.get(Stock, instrument_id)
    if stock is None:
        raise HTTPException(404, "Stock not in the screener universe")

    facts = db.execute(select(Fact.basis, Fact.period_type, Fact.period_end, Fact.metric, Fact.value)
                       .where(Fact.instrument_id == instrument_id)).all()
    merged, _ = merge_bases([tuple(f) for f in facts])
    fin = Financials(merged)
    years = list(reversed(fin.fy(6)))
    quarters = list(reversed(fin.quarters(8)))

    stored = {v.key: v for v in db.scalars(select(IndicatorValue).where(IndicatorValue.instrument_id == instrument_id))}
    indicators = []
    for key, ind in REGISTRY.items():
        v = stored.get(key)
        value = None
        if v is not None:
            value = bool(v.value_num) if ind.kind == "bool" else v.value_text if ind.kind == "enum" else v.value_num
        indicators.append({**ind.meta(), "value": value, "detail": v.detail if v else None})

    return {
        "instrument_id": stock.instrument_id, "symbol": stock.symbol, "name": stock.name, "series": stock.series,
        "basis": stock.basis, "is_financial": stock.is_financial, "close": stock.close,
        "close_date": stock.close_date.isoformat() if stock.close_date else None,
        "results_checked_at": stock.results_checked_at.isoformat() if stock.results_checked_at else None,
        "last_error": stock.last_error,
        "yearly": _table(years, YEARLY_LINES, lambda p: f"FY{p.end.year % 100:02d}"),
        "quarterly": _table(quarters, QUARTERLY_LINES, lambda p: p.end.strftime("%b %Y")),
        "indicators": indicators,
    }
