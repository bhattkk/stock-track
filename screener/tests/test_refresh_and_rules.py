"""Filing selection, the 'is new data possible' check, and rule validation."""
from datetime import date, datetime, timedelta, timezone

import pytest

from models import Stock
from providers.base import FilingRef
from refresh import is_due, select_filings
from screen_engine import RuleError, compile_rules, rule_keys


def ref(end, basis="consolidated", filed=None, name=None):
    return FilingRef(url=name or f"https://x/{end}-{basis}-{filed}.xml", period_end=end, basis=basis,
                     filed_at=filed or datetime(end.year, end.month, 28) + timedelta(days=40))


def quarter_ends(n, last=date(2026, 6, 30)):
    out, y, m = [], last.year, last.month
    for _ in range(n):
        out.append(date(y, m, 30 if m in (6, 9) else 31))
        m -= 3
        if m <= 0:
            m, y = m + 12, y - 1
    return out


def test_selects_recent_quarters_plus_fy_ends_consolidated_and_latest_revision():
    refs = []
    for end in quarter_ends(30):
        refs += [ref(end, "consolidated"), ref(end, "standalone")]
    revised = ref(date(2026, 3, 31), "consolidated", filed=datetime(2026, 7, 15), name="https://x/revised.xml")
    refs.append(revised)

    chosen = select_filings(refs, years=6, quarters=8)
    ends = [r.period_end for r in chosen]
    assert ends == sorted(ends)
    assert all(r.basis == "consolidated" for r in chosen)
    assert set(quarter_ends(8)) <= set(ends)
    assert {date(y, 3, 31) for y in range(2021, 2027)} <= set(ends)
    assert len(chosen) == 8 + 4  # 8 quarters (incl. FY25/FY26 ends) + FY21..FY24
    assert revised in chosen


def test_standalone_used_when_no_consolidated():
    chosen = select_filings([ref(date(2026, 3, 31), "standalone")], years=6, quarters=8)
    assert [r.basis for r in chosen] == ["standalone"]


def test_is_due():
    checked = datetime.now(timezone.utc) - timedelta(days=5)
    stock = Stock(instrument_id="x", results_checked_at=checked)
    assert is_due(stock, None, date(2026, 10, 18))
    # Jun quarter stored; Sep results can't be out before ~10 Oct.
    assert not is_due(stock, date(2026, 6, 30), date(2026, 10, 5))
    assert is_due(stock, date(2026, 6, 30), date(2026, 10, 18))
    # Checked yesterday → wait before asking NSE again.
    stock.results_checked_at = datetime.now(timezone.utc) - timedelta(days=1)
    assert not is_due(stock, date(2026, 6, 30), date(2026, 10, 18))


def test_rules_validate():
    rules = {"all": [{"ind": "roce_3y_avg", "op": ">=", "value": 15},
                     {"any": [{"ind": "capex_self_funded_3y", "op": "is_true"},
                              {"ind": "debt_to_equity", "op": "between", "value": 0, "value2": 0.3}]}]}
    compile_rules(rules)
    assert rule_keys(rules) == ["roce_3y_avg", "capex_self_funded_3y", "debt_to_equity"]
    with pytest.raises(RuleError):
        compile_rules({"all": [{"ind": "nope", "op": ">", "value": 1}]})
    with pytest.raises(RuleError):
        compile_rules({"all": [{"ind": "roce_3y_avg", "op": ">", "value": "high"}]})
    with pytest.raises(RuleError):
        compile_rules({"all": [{"ind": "roce_3y_avg", "op": "is_true"}]})
