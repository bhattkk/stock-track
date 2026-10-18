"""All screener indicators. Importing this package registers them.

To add one: write a function in the module for its family (or a new
module listed below) and decorate it with `@indicator` — see base.py.
"""
from indicators import cashflow, growth, leverage, profitability, returns, valuation  # noqa: F401
from indicators.base import REGISTRY, IndicatorDef, StockContext, Value

__all__ = ["REGISTRY", "IndicatorDef", "StockContext", "Value"]

# Display order of categories in the UI.
CATEGORIES = [
    ("profitability", "Profitability"),
    ("returns", "Returns"),
    ("leverage", "Debt"),
    ("capex", "Capex"),
    ("cash_flow", "Cash flow"),
    ("growth", "Growth"),
    ("valuation", "Valuation"),
]
