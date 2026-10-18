"""XBRL parsing against two real Asian Paints consolidated filings (public NSE data)."""
from datetime import date
from pathlib import Path

from providers.xbrl import parse_xbrl

FIXTURES = Path(__file__).parent / "fixtures"


def _facts(name):
    facts, is_bank = parse_xbrl((FIXTURES / name).read_bytes())
    return {(f.period_type, f.period_end, f.metric): f.value for f in facts}, is_bank


def test_legacy_q4_filing_has_quarter_year_balance_sheet_and_cash_flow():
    f, is_bank = _facts("legacy_fy24_consolidated.xml")
    end = date(2024, 3, 31)
    assert not is_bank
    assert f[("FY", end, "revenue")] == 354947300000.0
    assert f[("Q", end, "revenue")] == 87307600000.0
    assert f[("FY", end, "pat")] == 55576900000.0
    assert f[("FY", end, "cfo")] == 61036000000.0
    assert f[("FY", end, "capex_ppe")] == 24960800000.0
    assert f[("I", end, "equity")] == 194236800000.0
    assert f[("I", end, "borrowings_current")] == 10527600000.0


def test_integrated_filing_parses_with_same_metric_names():
    f, _ = _facts("integrated_fy26_consolidated.xml")
    end = date(2026, 3, 31)
    assert f[("FY", end, "revenue")] == 355835400000.0
    assert f[("FY", end, "exceptional_items")] == -1576100000.0
    assert f[("I", end, "equity_owners")] == 213715900000.0
    assert f[("FY", end, "cfo")] == 70881800000.0


def test_dimensional_contexts_are_skipped():
    # OtherExpenses is also reported under an "OperatingExpenses" dimension;
    # only the entity-level fact (no segment) may survive.
    f, _ = _facts("legacy_fy24_consolidated.xml")
    keys = [k for k in f if k[2] == "total_expenses" and k[0] == "FY"]
    assert keys == [("FY", date(2024, 3, 31), "total_expenses")]


def test_legacy_year_to_date_context_uses_reported_dates():
    # In this Q2 filing FourD is dated Jul–Sep but holds Apr–Sep figures.
    f, _ = _facts("legacy_q2fy24_consolidated.xml")
    end = date(2023, 9, 30)
    assert f[("Q", end, "revenue")] == 84785700000.0
    assert f[("H", end, "revenue")] == 176608800000.0
    assert f[("H", end, "cfo")] == 33506900000.0
    assert f[("I", end, "equity")] == 171779100000.0


def test_older_filing_with_undefined_contexts():
    # Pre-mid-2022 filings reference OneD/FourD/OneI without defining them.
    # They carry P&L and cash flow but no balance sheet (only segment assets).
    f, _ = _facts("legacy_fy22_undefined_contexts.xml")
    end = date(2022, 3, 31)
    assert f[("Q", end, "revenue")] == 78926700000.0
    assert f[("FY", end, "revenue")] == 291012800000.0
    assert f[("FY", end, "cfo")] == 9864900000.0
    assert not any(k[0] == "I" for k in f)
