"""Indicator maths on hand-built statements (values in rupees; 1 Cr = 1e7)."""
from datetime import date

import pytest

from compute import evaluate, merge_bases
from indicators import REGISTRY, StockContext
from indicators.base import Financials

CR = 1e7


def year(y, revenue, expenses, finance=1, dep=5, pbt=None, pat=None, cfo=None, capex=None,
         equity=None, debt=None, cogs=None):
    """One FY's facts (crores in, rupees out)."""
    end = date(y, 3, 31)
    pbt = revenue - expenses if pbt is None else pbt
    rows = [
        ("FY", end, "revenue", revenue), ("FY", end, "total_expenses", expenses),
        ("FY", end, "finance_costs", finance), ("FY", end, "depreciation", dep),
        ("FY", end, "pbt", pbt), ("FY", end, "pat", pat if pat is not None else pbt * 0.75),
    ]
    if cogs is not None:
        rows.append(("FY", end, "cost_of_materials", cogs))
    if cfo is not None:
        rows.append(("FY", end, "cfo", cfo))
    if capex is not None:
        rows.append(("FY", end, "capex_ppe", capex))
    if equity is not None:
        rows += [("I", end, "equity", equity), ("I", end, "borrowings_noncurrent", debt or 0)]
    return [(k, e, m, v * CR) for k, e, m, v in rows]


def ctx(facts, close=None):
    return StockContext("NSE_EQ|TEST", "TEST", close, False, Financials(facts))


def value(c, key):
    ind = REGISTRY[key]
    r = ind.fn(c, **ind.params)
    return getattr(r, "value", r)


@pytest.fixture
def steady():
    # Revenue 100→150 over FY21-FY26, OPM rising 20%→25%, CFO ≈ PAT, capex funded internally.
    facts = []
    for i, y in enumerate(range(2021, 2027)):
        rev = 100 + 10 * i
        opm = 20 + i
        ebitda = rev * opm / 100
        exp = rev - ebitda + 1 + 5  # + finance + depreciation
        facts += year(y, rev, exp, cfo=(ebitda - 6) * 0.8, capex=5, equity=200 + 10 * i, debt=20,
                      cogs=rev * 0.5)
    return ctx(facts)


def test_opm_and_its_change(steady):
    assert value(steady, "opm_latest") == pytest.approx(25.0)
    assert value(steady, "opm_change_3y") == pytest.approx(3.0)
    assert value(steady, "opm_change_5y") == pytest.approx(5.0)
    assert value(steady, "opm_rising_years") == 5


def test_cagr(steady):
    assert value(steady, "sales_cagr_5y") == pytest.approx(((150 / 100) ** (1 / 5) - 1) * 100, abs=0.01)


def test_cfo_to_pat_and_capex(steady):
    # PAT = 0.75 × PBT, CFO = 0.8 × PBT → 1.07
    assert value(steady, "cfo_to_pat_5y") == pytest.approx(0.8 / 0.75, abs=0.01)
    assert value(steady, "capex_3y") == pytest.approx(15.0)
    assert value(steady, "capex_self_funded_3y") is True
    assert value(steady, "debt_to_equity") == pytest.approx(20 / 250, abs=0.001)


def test_roce_uses_average_capital_employed(steady):
    fy26 = steady.fin.fy_ending(2026)
    ebit = fy26.get("ebit")
    avg_ce = ((240 + 20) + (250 + 20)) / 2 * CR
    assert value(steady, "roce_latest") == pytest.approx(ebit / avg_ce * 100, abs=0.01)


def test_debt_funded_capex_is_flagged():
    facts = []
    for i, y in enumerate(range(2022, 2027)):
        facts += year(y, 100, 80, cfo=10, capex=30, equity=100, debt=10 + 25 * i)
    c = ctx(facts)
    assert value(c, "capex_self_funded_3y") is False  # debt +75 Cr vs 90 Cr capex


def test_missing_history_gives_none_not_garbage():
    c = ctx(year(2026, 100, 80))
    assert value(c, "opm_change_3y") is None
    assert value(c, "cfo_to_pat_5y") is None
    assert value(c, "sales_cagr_3y") is None


def test_loss_making_has_no_pe():
    facts = year(2026, 100, 120, pat=-15, equity=100)
    facts += [("I", date(2026, 3, 31), "paid_up_capital", 10 * CR), ("FY", date(2026, 3, 31), "face_value", 10)]
    c = ctx(facts, close=50.0)
    assert value(c, "market_cap") == pytest.approx(50.0)  # 1 Cr shares × ₹50 = ₹50 Cr
    assert value(c, "pe") is None


def test_evaluate_skips_nulls_and_stores_bools_as_numbers(steady):
    rows = {r["key"]: r for r in evaluate(steady)}
    assert rows["capex_self_funded_3y"]["value_num"] == 1.0
    assert "pe" not in rows  # no price → no value row


def test_financial_companies_get_only_applicable_indicators(steady):
    steady.is_financial = True
    steady.close = 100.0
    keys = {r["key"] for r in evaluate(steady)}
    assert keys <= {k for k, i in REGISTRY.items() if i.applies_to_financials}


def test_merge_bases_prefers_consolidated_per_period():
    end_old, end_new = date(2022, 3, 31), date(2026, 3, 31)
    rows = [
        ("standalone", "FY", end_old, "revenue", 1.0),
        ("standalone", "FY", end_new, "revenue", 2.0),
        ("consolidated", "FY", end_new, "revenue", 3.0),
    ]
    merged, basis = merge_bases(rows)
    assert sorted(merged) == [("FY", end_old, "revenue", 1.0), ("FY", end_new, "revenue", 3.0)]
    assert basis == "consolidated"


def test_implausible_pat_falls_back_to_pbt_minus_current_tax():
    # Real case: deferred tax mistyped as −6,144 Cr, so tax −4,980 Cr and
    # PAT 9,167 Cr on PBT of 4,156 Cr.
    end = date(2022, 3, 31)
    facts = [("FY", end, "revenue", 29101 * CR), ("FY", end, "pbt", 4156 * CR), ("FY", end, "tax", -4980 * CR),
             ("FY", end, "current_tax", 1164 * CR), ("FY", end, "pat", 9167 * CR), ("FY", end, "pat_owners", 3031 * CR)]
    fy = Financials(facts).fy(1)[0]
    assert fy.get("net_profit") == pytest.approx(2992 * CR)
    assert fy.get("profit") == pytest.approx(3031 * CR)


def test_pat_not_reconciling_with_tax_uses_pbt_minus_tax():
    end = date(2025, 3, 31)
    facts = [("FY", end, "revenue", 1000 * CR), ("FY", end, "pbt", 100 * CR), ("FY", end, "tax", 25 * CR),
             ("FY", end, "pat", 7.5 * CR)]  # off by 10x
    assert Financials(facts).fy(1)[0].get("net_profit") == pytest.approx(75 * CR)
