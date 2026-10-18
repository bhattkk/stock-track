"""Parse an NSE results XBRL file into FactRows.

Covers both formats NSE has served: the legacy results filings (namespace
in-bse-fin, up to Dec 2024) and SEBI's Integrated Filing (namespace
in-capmkt, from Dec 2024). Element local names are the same in both, so
facts are matched on local name only.

Each fact's period comes from dates, not the context id ("OneD", "FourD",
...): a ~3-month duration is a quarter, ~6 a half year, ~9 nine months,
~12 a full year, and an instant is a balance-sheet date. Legacy filings
often stamp every duration context with the quarter's dates (FourD holding
year-to-date figures but dated like the quarter), so the
DateOfStart/EndOfReportingPeriod facts each context carries take
precedence over the context's own period. Filings from before mid-2022
go further and reference OneD / FourD / OneI without defining those
contexts at all; for these simple ids the reported dates stand in for the
missing context (an "...I" id is the balance sheet at the period end). Contexts with dimensions (segment/scenario members, e.g.
segment-wise revenue) are skipped — only entity-level totals are kept.
"""
from __future__ import annotations

import re
from datetime import date
from xml.etree import ElementTree as ET

from providers.base import FactRow

XBRLI = "{http://www.xbrl.org/2003/instance}"

# XBRL element local name -> our metric name. Add a line here to start
# collecting a new line item; `python cli.py reparse` back-fills it from the
# cached files.
TAG_MAP = {
    # Profit & loss (durations)
    "RevenueFromOperations": "revenue",
    "OtherIncome": "other_income",
    "CostOfMaterialsConsumed": "cost_of_materials",
    "PurchasesOfStockInTrade": "purchases_stock_in_trade",
    "ChangesInInventoriesOfFinishedGoodsWorkInProgressAndStockInTrade": "inventory_change",
    "EmployeeBenefitExpense": "employee_expense",
    "FinanceCosts": "finance_costs",
    "DepreciationDepletionAndAmortisationExpense": "depreciation",
    "Expenses": "total_expenses",
    "ExceptionalItemsBeforeTax": "exceptional_items",
    "ProfitBeforeTax": "pbt",
    "CurrentTax": "current_tax",
    "TaxExpense": "tax",
    "ProfitLossForPeriod": "pat",
    "ProfitOrLossAttributableToOwnersOfParent": "pat_owners",
    "BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations": "eps",
    "PaidUpValueOfEquityShareCapital": "paid_up_capital",
    "FaceValueOfEquityShareCapital": "face_value",
    # Balance sheet (instants)
    "PropertyPlantAndEquipment": "ppe",
    "CapitalWorkInProgress": "cwip",
    "Inventories": "inventories",
    "TradeReceivablesCurrent": "trade_receivables",
    "CashAndCashEquivalents": "cash",
    "BankBalanceOtherThanCashAndCashEquivalents": "bank_balances",
    "CurrentInvestments": "current_investments",
    "CurrentAssets": "current_assets",
    "Assets": "total_assets",
    "Equity": "equity",
    "EquityAttributableToOwnersOfParent": "equity_owners",
    "BorrowingsNoncurrent": "borrowings_noncurrent",
    "BorrowingsCurrent": "borrowings_current",
    "TradePayablesCurrent": "trade_payables",
    "CurrentLiabilities": "current_liabilities",
    # Cash flow (durations)
    "CashFlowsFromUsedInOperatingActivities": "cfo",
    "CashFlowsFromUsedInInvestingActivities": "cfi",
    "CashFlowsFromUsedInFinancingActivities": "cff",
    "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "capex_ppe",
    "PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities": "capex_intangibles",
    "ProceedsFromSalesOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities": "ppe_sale_proceeds",
    "DividendsPaidClassifiedAsFinancingActivities": "dividends_paid",
}

# Elements that only appear in the banking results format.
BANK_MARKERS = {"InterestEarned", "InterestExpended", "GrossNonPerformingAssets"}

# Duration length in days -> period type.
_DURATIONS = [(80, 100, "Q"), (170, 195, "H"), (260, 285, "M9"), (355, 375, "FY")]
# Plain (dimension-free) context ids used by NSE filings: OneD, FourD, OneI, ...
_SIMPLE_ID = re.compile(r"^(One|Two|Three|Four)([DI])$")


def _classify(start: date, end: date) -> str | None:
    days = (end - start).days + 1
    for lo, hi, kind in _DURATIONS:
        if lo <= days <= hi:
            return kind
    return None


def _reported_periods(root: ET.Element) -> dict[str, tuple[date, date]]:
    """context id -> (start, end) from its DateOfStart/EndOfReportingPeriod facts."""
    starts, ends = {}, {}
    for el in root:
        tag, ref = el.tag.rsplit("}", 1)[-1], el.get("contextRef")
        if tag in ("DateOfStartOfReportingPeriod", "DateOfEndOfReportingPeriod") and ref and el.text:
            try:
                d = date.fromisoformat(el.text.strip())
            except ValueError:
                continue
            (starts if tag.startswith("DateOfStart") else ends)[ref] = d
    return {ref: (starts[ref], ends[ref]) for ref in starts.keys() & ends.keys()}


def _contexts(root: ET.Element) -> dict[str, tuple[str, date]]:
    """context id -> (period_type, period_end) for dimension-free contexts."""
    reported = _reported_periods(root)
    out = {}
    for ctx in root.iter(XBRLI + "context"):
        if ctx.find(f".//{XBRLI}segment") is not None or ctx.find(f".//{XBRLI}scenario") is not None:
            continue
        period = ctx.find(XBRLI + "period")
        if period is None:
            continue
        instant = period.find(XBRLI + "instant")
        if instant is not None and instant.text:
            out[ctx.get("id")] = ("I", date.fromisoformat(instant.text.strip()))
            continue
        start, end = period.find(XBRLI + "startDate"), period.find(XBRLI + "endDate")
        if start is None or end is None:
            continue
        s, e = reported.get(ctx.get("id")) or (date.fromisoformat(start.text.strip()), date.fromisoformat(end.text.strip()))
        kind = _classify(s, e)
        if kind:
            out[ctx.get("id")] = (kind, e)

    # Undefined simple contexts (older filings): rebuild from reported dates.
    defined = {ctx.get("id") for ctx in root.iter(XBRLI + "context")}
    refs = {el.get("contextRef") for el in root if el.get("contextRef")}
    for ref in refs - defined:
        m = _SIMPLE_ID.match(ref)
        if not m:
            continue
        if m.group(2) == "D" and ref in reported:
            kind = _classify(*reported[ref])
            if kind:
                out[ref] = (kind, reported[ref][1])
        elif m.group(2) == "I":
            period = reported.get(m.group(1) + "D") or reported.get("OneD")
            if period:
                out[ref] = ("I", period[1])
    return out


def parse_xbrl(content: bytes) -> tuple[list[FactRow], bool]:
    """Returns (facts, looks_like_bank_format)."""
    root = ET.fromstring(content)
    contexts = _contexts(root)
    facts: dict[tuple[str, date, str], float] = {}
    is_bank = False
    for el in root:
        tag = el.tag.rsplit("}", 1)[-1]
        if tag in BANK_MARKERS:
            is_bank = True
        metric = TAG_MAP.get(tag)
        ref = el.get("contextRef")
        if not metric or ref not in contexts or el.text is None:
            continue
        try:
            value = float(el.text.strip())
        except ValueError:
            continue
        kind, end = contexts[ref]
        facts[(kind, end, metric)] = value
    rows = [FactRow(period_type=k, period_end=e, metric=m, value=v) for (k, e, m), v in facts.items()]
    return rows, is_bank
