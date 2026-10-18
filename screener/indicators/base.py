"""Indicator registry and the data helpers indicators compute from.

An indicator is a plain function of a `StockContext`, registered with the
`@indicator` decorator. Stack the decorator to register variants of one
function with different parameters:

    @indicator("cfo_to_pat_5y", "CFO / PAT (5Y)", "cash_flow", unit="x", params={"years": 5})
    @indicator("cfo_to_pat_3y", "CFO / PAT (3Y)", "cash_flow", unit="x", params={"years": 3})
    def cfo_to_pat(ctx, years):
        ...
        return Value(ratio, detail={...})   # or just return the number / None

compute.py runs every registered indicator for every stock and stores the
results in screener_indicator_values; screens only filter those stored
values. The API serves the registry metadata (GET /indicators) and the UI
builds its filter list from it — so a new indicator needs no API, schema or
UI change. Technical indicators (MACD, candlestick patterns) plug in the
same way once a "candles" input is loaded into the context.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable

log = logging.getLogger("screener.indicators")

CRORE = 1e7
# Reported PAT may differ from PBT − tax (associates' share, minorities,
# discontinued ops) — beyond this share of PBT it's treated as a typo.
PROFIT_TOLERANCE = 0.25

KINDS = {"number", "bool", "enum"}


@dataclass
class Value:
    value: float | bool | str | None
    detail: dict | None = None


@dataclass
class IndicatorDef:
    key: str
    name: str
    category: str
    fn: Callable[..., Any]
    kind: str = "number"
    unit: str = ""
    description: str = ""
    params: dict = field(default_factory=dict)
    inputs: frozenset = frozenset({"financials"})
    enum_values: list[str] | None = None
    # Most fundamentals assume the manufacturing/services results format;
    # banks, NBFCs and insurers get null for those.
    applies_to_financials: bool = False
    higher_is_better: bool | None = None  # for UI hints only

    def meta(self) -> dict:
        return {
            "key": self.key, "name": self.name, "category": self.category, "kind": self.kind,
            "unit": self.unit, "description": self.description, "params": self.params,
            "enum_values": self.enum_values, "higher_is_better": self.higher_is_better,
        }


REGISTRY: dict[str, IndicatorDef] = {}


def indicator(key: str, name: str, category: str, *, kind: str = "number", unit: str = "",
              description: str = "", params: dict | None = None, inputs: set[str] | None = None,
              enum_values: list[str] | None = None, applies_to_financials: bool = False,
              higher_is_better: bool | None = None):
    if kind not in KINDS:
        raise ValueError(f"{key}: unknown kind {kind!r}")

    def register(fn):
        if key in REGISTRY:
            raise ValueError(f"Indicator {key!r} registered twice")
        REGISTRY[key] = IndicatorDef(
            key=key, name=name, category=category, fn=fn, kind=kind, unit=unit, description=description,
            params=params or {}, inputs=frozenset(inputs or {"financials"}), enum_values=enum_values,
            applies_to_financials=applies_to_financials, higher_is_better=higher_is_better,
        )
        return fn

    return register


# -- maths helpers ----------------------------------------------------------

def div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return a / b


def cagr(start: float | None, end: float | None, years: float) -> float | None:
    """Compound annual growth in %; None unless both ends are positive."""
    if start is None or end is None or start <= 0 or end <= 0 or years <= 0:
        return None
    return ((end / start) ** (1 / years) - 1) * 100


def stdev(values: list[float | None], min_points: int) -> float | None:
    vals = [v for v in values if v is not None]
    return statistics.pstdev(vals) if len(vals) >= min_points else None


def crores(v: float | None) -> float | None:
    return None if v is None else round(v / CRORE, 2)


def rnd(v: float | None, digits: int = 2) -> float | None:
    return None if v is None else round(v, digits)


# -- financial statements ---------------------------------------------------

def _sum(*vals: float | None) -> float | None:
    """Sum treating missing parts as 0 — but None if every part is missing."""
    present = [v for v in vals if v is not None]
    return sum(present) if present else None


class Period:
    """One reporting period: its P&L / cash-flow durations plus the balance
    sheet as of its end date, with derived metrics computed on access."""

    def __init__(self, kind: str, end: date, values: dict[str, float], balance: dict[str, float]):
        self.kind = kind
        self.end = end
        self._values = values
        self._balance = balance

    def raw(self, metric: str) -> float | None:
        v = self._values.get(metric)
        return v if v is not None else self._balance.get(metric)

    def get(self, metric: str) -> float | None:
        derive = getattr(self, f"_d_{metric}", None)
        return derive() if derive else self.raw(metric)

    # Derived metrics — the definitions screens are built on.
    def _d_cogs(self):
        return _sum(self.raw("cost_of_materials"), self.raw("purchases_stock_in_trade"), self.raw("inventory_change"))

    def _d_gross_margin(self):
        # Not meaningful without material costs (IT services, banks, ...).
        rev, cogs = self.raw("revenue"), self._d_cogs()
        return None if rev is None or not cogs or rev <= 0 else (rev - cogs) / rev * 100

    def _d_ebitda(self):
        # Operating profit, as Screener.in defines it: revenue less expenses
        # excluding interest and depreciation, before other income.
        rev, exp = self.raw("revenue"), self.raw("total_expenses")
        if rev is None or exp is None:
            return None
        return rev - (exp - (self.raw("finance_costs") or 0) - (self.raw("depreciation") or 0))

    def _d_opm(self):
        rev, ebitda = self.raw("revenue"), self._d_ebitda()
        return None if rev is None or rev <= 0 or ebitda is None else ebitda / rev * 100

    def _d_ebit(self):
        pbt = self.raw("pbt")
        if pbt is None:
            return None
        return pbt - (self.raw("exceptional_items") or 0) + (self.raw("finance_costs") or 0)

    def _d_net_profit(self):
        """Total PAT, sanity-checked against mistyped filings:

        * PAT far above a positive PBT (a tax *credit* of over half of PBT)
          is implausible — seen in a real filing where deferred tax was off
          by 100x — so PBT − current tax is used instead;
        * otherwise, PAT that doesn't roughly reconcile with PBT − tax is
          replaced by PBT − tax.
        """
        pat, pbt, tax = self.raw("pat"), self.raw("pbt"), self.raw("tax")
        if pbt is None:
            return pat
        if pat is not None and pbt > 0 and pat > pbt * (1 + 2 * PROFIT_TOLERANCE) + CRORE:
            current = self.raw("current_tax")
            return None if current is None else pbt - current
        if tax is None:
            return pat
        derived = pbt - tax
        if pat is None or abs(pat - derived) > PROFIT_TOLERANCE * abs(pbt) + CRORE:
            return derived
        return pat

    def _d_profit(self):
        """Profit attributable to shareholders (consolidated) or PAT (standalone),
        with the same sanity check against total PAT."""
        total, owners = self._d_net_profit(), self.raw("pat_owners")
        if owners is None or total is None:
            return total if owners is None else owners
        return owners if abs(owners) <= abs(total) * (1 + PROFIT_TOLERANCE) + CRORE else total

    def _d_shareholders_equity(self):
        v = self.raw("equity_owners")
        return v if v is not None else self.raw("equity")

    def _d_debt(self):
        return _sum(self.raw("borrowings_noncurrent"), self.raw("borrowings_current"))

    def _d_cash_like(self):
        return _sum(self.raw("cash"), self.raw("bank_balances"), self.raw("current_investments"))

    def _d_capital_employed(self):
        eq = self.raw("equity")
        return None if eq is None else eq + (self._d_debt() or 0)

    def _d_capex(self):
        parts = [self.raw("capex_ppe"), self.raw("capex_intangibles")]
        return _sum(*[abs(p) for p in parts if p is not None])

    def _d_shares(self):
        return div(self.raw("paid_up_capital"), self.raw("face_value"))


class Financials:
    """All stored facts of one stock on one basis, organised by period."""

    def __init__(self, facts: list[tuple[str, date, str, float]]):
        durations: dict[tuple[str, date], dict[str, float]] = {}
        instants: dict[date, dict[str, float]] = {}
        for kind, end, metric, value in facts:
            if kind == "I":
                instants.setdefault(end, {})[metric] = value
            else:
                durations.setdefault((kind, end), {})[metric] = value
        self._instants = instants
        self._periods = {
            key: Period(key[0], key[1], vals, instants.get(key[1], {})) for key, vals in durations.items()
        }

    def _series(self, kind: str) -> list[Period]:
        return sorted((p for (k, _), p in self._periods.items() if k == kind), key=lambda p: p.end, reverse=True)

    def fy(self, n: int | None = None) -> list[Period]:
        """Full years with revenue reported, newest first."""
        years = [p for p in self._series("FY") if p.raw("revenue") is not None or p.raw("pat") is not None]
        return years[:n] if n else years

    def fy_ending(self, year: int) -> Period | None:
        return next((p for p in self.fy() if p.end.year == year), None)

    def quarters(self, n: int | None = None) -> list[Period]:
        qs = self._series("Q")
        return qs[:n] if n else qs

    def balance_at(self, end: date) -> Period | None:
        vals = self._instants.get(end)
        return Period("I", end, {}, vals) if vals else None

    def latest_balance(self) -> Period | None:
        """Most recent balance sheet (year-end or half-year), if it has equity."""
        for end in sorted(self._instants, reverse=True):
            if self._instants[end].get("equity") is not None:
                return Period("I", end, {}, self._instants[end])
        return None

    def ttm(self, metric: str, offset: int = 0) -> tuple[float | None, list[Period]]:
        """Sum of 4 consecutive quarters starting ``offset`` quarters back."""
        qs = self.quarters()[offset:offset + 4]
        if len(qs) < 4 or any(_months_apart(a.end, b.end) != 3 for a, b in zip(qs, qs[1:])):
            return None, qs
        vals = [q.get(metric) for q in qs]
        return (None if any(v is None for v in vals) else sum(vals)), qs

    def latest_shares(self) -> float | None:
        for p in self.quarters() + self.fy():
            s = p.get("shares")
            if s:
                return s
        return None


def _months_apart(later: date, earlier: date) -> int:
    return (later.year - earlier.year) * 12 + later.month - earlier.month


@dataclass
class StockContext:
    instrument_id: str
    symbol: str
    close: float | None
    is_financial: bool
    fin: Financials
