"""Turn a screen's rule tree into SQL over screener_indicator_values.

Rules are nested groups of conditions:

    {"all": [
        {"ind": "roce_3y_avg", "op": ">=", "value": 15},
        {"ind": "capex_self_funded_3y", "op": "is_true"},
        {"any": [{"ind": "debt_to_equity", "op": "<", "value": 0.5},
                 {"ind": "interest_coverage", "op": ">", "value": 8}]}
    ]}

Each condition becomes an EXISTS on the stock's stored value, so a stock
with no value for an indicator (not enough history, not applicable) never
matches a comparison — use "is_null" to find those explicitly.
"""
from __future__ import annotations

from sqlalchemy import and_, exists, not_, or_, select, true
from sqlalchemy.orm import Session

from indicators import REGISTRY
from models import IndicatorValue, Stock

NUMERIC_OPS = {">", ">=", "<", "<=", "eq", "neq", "between"}
UNARY_OPS = {"is_true", "is_false", "is_null", "not_null"}
ENUM_OPS = {"eq", "neq", "in"}
MAX_DEPTH = 5
DEFAULT_COLUMNS = ["market_cap", "pe", "roce_3y_avg", "roe_3y_avg", "opm_latest", "opm_change_3y",
                   "debt_to_equity", "cfo_to_pat_5y", "sales_cagr_3y"]


class RuleError(ValueError):
    pass


def _num(v, what: str) -> float:
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise RuleError(f"{what}: expected a number, got {v!r}")
    return float(v)


def _condition(rule: dict):
    key, op = rule.get("ind"), rule.get("op")
    ind = REGISTRY.get(key)
    if ind is None:
        raise RuleError(f"Unknown indicator {key!r}")
    v = IndicatorValue
    has_row = and_(v.instrument_id == Stock.instrument_id, v.key == key)

    if op == "is_null":
        return not_(exists().where(has_row))
    if op == "not_null":
        return exists().where(has_row)
    if op in ("is_true", "is_false"):
        if ind.kind != "bool":
            raise RuleError(f"{key}: {op} only applies to yes/no indicators")
        return exists().where(has_row, v.value_num == (1.0 if op == "is_true" else 0.0))

    if ind.kind == "enum":
        if op not in ENUM_OPS:
            raise RuleError(f"{key}: operator {op!r} not valid for a category indicator")
        val = rule.get("value")
        if op == "in":
            if not isinstance(val, list) or not val:
                raise RuleError(f"{key}: 'in' needs a non-empty list")
            return exists().where(has_row, v.value_text.in_([str(x) for x in val]))
        cond = v.value_text == str(val)
        return exists().where(has_row, cond if op == "eq" else not_(cond))

    if op not in NUMERIC_OPS:
        raise RuleError(f"{key}: unknown operator {op!r}")
    if op == "between":
        lo, hi = rule.get("value"), rule.get("value2")
        cond = v.value_num.between(_num(lo, key), _num(hi, key))
    else:
        x = _num(rule.get("value"), key)
        cond = {">": v.value_num > x, ">=": v.value_num >= x, "<": v.value_num < x, "<=": v.value_num <= x,
                "eq": v.value_num == x, "neq": v.value_num != x}[op]
    return exists().where(has_row, cond)


def compile_rules(rules: dict | None, depth: int = 0):
    """Rule tree → SQLAlchemy boolean expression (raises RuleError on bad input)."""
    if not rules:
        return true()
    if depth > MAX_DEPTH:
        raise RuleError("Rules nested too deeply")
    if "ind" in rules:
        return _condition(rules)
    for group, combine in (("all", and_), ("any", or_)):
        if group in rules:
            children = rules[group]
            if not isinstance(children, list):
                raise RuleError(f"'{group}' must be a list")
            if not children:
                return true()
            return combine(*[compile_rules(c, depth + 1) for c in children])
    raise RuleError("A rule needs 'ind', 'all' or 'any'")


def rule_keys(rules: dict | None) -> list[str]:
    if not rules:
        return []
    if "ind" in rules:
        return [rules["ind"]]
    return [k for c in rules.get("all", []) + rules.get("any", []) for k in rule_keys(c)]


def run_screen(db: Session, rules: dict | None, columns: list[str] | None = None,
               sort: dict | None = None, limit: int = 200) -> dict:
    columns = [c for c in (columns or DEFAULT_COLUMNS) if c in REGISTRY]
    cond = compile_rules(rules)
    stocks = db.execute(
        select(Stock.instrument_id, Stock.symbol, Stock.name, Stock.close, Stock.basis)
        .where(Stock.listed.is_(True), cond)
    ).all()

    wanted = list(dict.fromkeys(columns + rule_keys(rules) + ([sort["key"]] if sort and sort.get("key") else [])))
    ids = [s.instrument_id for s in stocks]
    values: dict[str, dict] = {i: {} for i in ids}
    if ids and wanted:
        for row in db.execute(
            select(IndicatorValue.instrument_id, IndicatorValue.key, IndicatorValue.value_num, IndicatorValue.value_text)
            .where(IndicatorValue.instrument_id.in_(ids), IndicatorValue.key.in_(wanted))
        ):
            ind = REGISTRY.get(row.key)
            if ind is None:
                continue
            if ind.kind == "bool":
                values[row.instrument_id][row.key] = bool(row.value_num)
            elif ind.kind == "enum":
                values[row.instrument_id][row.key] = row.value_text
            else:
                values[row.instrument_id][row.key] = row.value_num

    rows = [{"instrument_id": s.instrument_id, "symbol": s.symbol, "name": s.name, "basis": s.basis,
             "values": values[s.instrument_id]} for s in stocks]

    sort_key = (sort or {}).get("key") or "market_cap"
    desc = (sort or {}).get("dir", "desc") != "asc"
    if sort_key == "symbol":
        rows.sort(key=lambda r: r["symbol"], reverse=desc)
    else:
        present = [r for r in rows if r["values"].get(sort_key) is not None]
        missing = [r for r in rows if r["values"].get(sort_key) is None]
        present.sort(key=lambda r: r["values"][sort_key], reverse=desc)
        rows = present + sorted(missing, key=lambda r: r["symbol"])  # nulls always last

    return {"total": len(rows), "columns": columns, "rows": rows[:limit]}
