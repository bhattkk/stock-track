"""Run every registered indicator for every stock and store the results.

Facts are loaded in batches. For each period the consolidated figures are
used when the company filed them, else standalone (refresh.select_filings
makes the same choice when downloading), and the basis of the latest full
year is saved on screener_stocks for display. A stock's old indicator rows
are replaced wholesale, so removed indicators disappear too.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone
from typing import Callable

from sqlalchemy import delete, insert, select
from sqlalchemy.orm import Session

from indicators import REGISTRY, StockContext, Value
from indicators.base import Financials
from models import Fact, IndicatorValue, Stock

log = logging.getLogger("screener.compute")

BATCH = 200


def merge_bases(rows: list[tuple[str, str, object, str, float]]) -> tuple[list[tuple], str | None]:
    """rows: (basis, period_type, period_end, metric, value).

    Returns the facts to compute from — per (period_type, period_end), every
    metric from one basis only, consolidated preferred — and the basis of the
    latest full year.
    """
    groups: dict[tuple, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for basis, kind, end, metric, value in rows:
        groups[(kind, end)][basis].append((kind, end, metric, value))
    merged, fy_basis = [], {}
    for (kind, end), by_basis in groups.items():
        basis = "consolidated" if by_basis.get("consolidated") else "standalone"
        merged.extend(by_basis[basis])
        if kind == "FY":
            fy_basis[end] = basis
    latest_basis = fy_basis[max(fy_basis)] if fy_basis else None
    return merged, latest_basis


def evaluate(ctx: StockContext) -> list[dict]:
    """Run all applicable indicators for one stock → rows for screener_indicator_values."""
    now = datetime.now(timezone.utc)
    out = []
    for key, ind in REGISTRY.items():
        if ctx.is_financial and not ind.applies_to_financials:
            continue
        try:
            result = ind.fn(ctx, **ind.params)
        except Exception:  # one bad indicator must not sink the stock
            log.exception("Indicator %s failed for %s", key, ctx.symbol)
            continue
        val = result if isinstance(result, Value) else Value(result)
        if val.value is None:
            continue
        row = {"instrument_id": ctx.instrument_id, "key": key, "value_num": None, "value_text": None,
               "detail": val.detail, "computed_at": now}
        if ind.kind == "enum":
            row["value_text"] = str(val.value)
        else:
            row["value_num"] = float(val.value)  # bools stored as 1.0 / 0.0
        out.append(row)
    return out


def compute_all(db: Session, instrument_ids: list[str] | None = None,
                progress: Callable[[int, int], None] | None = None) -> int:
    """Recompute indicators for the given stocks (default: all listed). Returns stocks processed."""
    q = select(Stock).where(Stock.listed.is_(True))
    if instrument_ids:
        q = q.where(Stock.instrument_id.in_(instrument_ids))
    stocks = db.scalars(q.order_by(Stock.symbol)).all()
    for start in range(0, len(stocks), BATCH):
        batch = stocks[start:start + BATCH]
        ids = [s.instrument_id for s in batch]
        facts: dict[str, list] = defaultdict(list)
        for f in db.execute(
            select(Fact.instrument_id, Fact.basis, Fact.period_type, Fact.period_end, Fact.metric, Fact.value)
            .where(Fact.instrument_id.in_(ids))
        ):
            facts[f.instrument_id].append((f.basis, f.period_type, f.period_end, f.metric, f.value))

        rows = []
        for stock in batch:
            merged, stock.basis = merge_bases(facts.get(stock.instrument_id, []))
            fin = Financials(merged)
            ctx = StockContext(stock.instrument_id, stock.symbol, stock.close, stock.is_financial, fin)
            rows.extend(evaluate(ctx))

        db.execute(delete(IndicatorValue).where(IndicatorValue.instrument_id.in_(ids)))
        if rows:
            db.execute(insert(IndicatorValue), rows)
        db.commit()
        if progress:
            progress(min(start + BATCH, len(stocks)), len(stocks))
    log.info("Computed indicators for %d stock(s)", len(stocks))
    return len(stocks)
