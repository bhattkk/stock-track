"""Evaluate one stock against the criteria: gates, then setups, then flags.

Pass logic, in layer order:
  A. tradability  every enabled gate must PASS
  B. trend        every enabled gate must PASS
  C. setups       at least `setups.min_fired` setups must fire
  D. flags        no flag with action `reject` may fire (a `warn` only lowers the score)

All layers are evaluated for every stock, even after a failure, so a rejected
stock can show its whole picture. `failed_at` records the first layer that failed.

Each rule outcome is a dict:
  {layer, id, title, why, status, note, value?, op?, threshold?, action?}
with status PASS / FAIL / NA for gates, FIRED / NOT_FIRED / NA for setups,
and WARN / REJECT / CLEAR / NA for flags. NA (missing data) never counts as
passing or firing.
"""
from __future__ import annotations

import operator
from dataclasses import dataclass

from criteria.schema import Criteria, GateRule
from technicals.metrics import Context
from technicals.setups import FLAGS, SETUPS

OPS = {">": operator.gt, ">=": operator.ge, "<": operator.lt, "<=": operator.le,
       "==": operator.eq, "!=": operator.ne}
LAYERS = ("tradability", "trend", "setup", "flag")


@dataclass
class Evaluation:
    """Result of evaluating one stock.

    passed         cleared all four layers
    failed_at      first failing layer ("tradability" / "trend" / "setup" / "flag"), or None
    rules          every rule outcome, in YAML order
    setups_fired   ids of the setups that fired
    warns          ids of the warn-level flags that fired
    """
    passed: bool
    failed_at: str | None
    rules: list[dict]
    setups_fired: list[str]
    warns: list[str]


def _gate(layer: str, rule: GateRule, metrics: dict) -> dict:
    """Compare one metric with its threshold: PASS / FAIL, or NA if the metric is missing."""
    if rule.metric not in metrics:
        raise ValueError(f"Rule {rule.id!r}: unknown metric {rule.metric!r}; known: {sorted(metrics)}")
    value = metrics[rule.metric]
    out = {"layer": layer, "id": rule.id, "title": rule.title, "why": rule.why, "metric": rule.metric,
           "value": value, "op": rule.op, "threshold": rule.value}
    if value is None:
        return {**out, "status": "NA", "note": f"{rule.metric} unavailable (not enough history)"}
    ok = OPS[rule.op](value, rule.value)
    return {**out, "status": "PASS" if ok else "FAIL", "note": f"{rule.metric} = {value} (needs {rule.op} {rule.value:g})"}


def evaluate(criteria: Criteria, ctx: Context, metrics: dict) -> Evaluation:
    """Run every enabled rule in the criteria against one stock."""
    rules: list[dict] = []
    failed: list[str] = []

    for layer, gates in (("tradability", criteria.tradability), ("trend", criteria.trend)):
        outcomes = [_gate(layer, g, metrics) for g in gates if g.enabled]
        rules += outcomes
        if any(o["status"] != "PASS" for o in outcomes):
            failed.append(layer)

    fired = []
    for s in (r for r in criteria.setups.rules if r.enabled):
        sig = SETUPS[s.id](ctx, **s.params)
        status = "NA" if sig.fired is None else "FIRED" if sig.fired else "NOT_FIRED"
        rules.append({"layer": "setup", "id": s.id, "title": s.title, "why": s.why, "status": status,
                      "note": sig.note, "details": sig.details})
        if sig.fired:
            fired.append(s.id)
    if len(fired) < criteria.setups.min_fired:
        failed.append("setup")

    warns, rejected = [], False
    for f in (r for r in criteria.flags if r.enabled):
        sig = FLAGS[f.id](ctx, **f.params)
        if sig.fired is None:
            status = "NA"
        elif sig.fired:
            status = "REJECT" if f.action == "reject" else "WARN"
        else:
            status = "CLEAR"
        rules.append({"layer": "flag", "id": f.id, "title": f.title, "why": f.why, "status": status,
                      "action": f.action, "note": sig.note, "details": sig.details})
        if status == "WARN":
            warns.append(f.id)
        rejected |= status == "REJECT"
    if rejected:
        failed.append("flag")

    first = next((layer for layer in LAYERS if layer in failed), None)
    return Evaluation(passed=first is None, failed_at=first, rules=rules, setups_fired=fired, warns=warns)


def score(criteria: Criteria, ev: Evaluation, rs_percentile: float | None, w_rsi: float | None) -> float:
    """Ranking score (higher = better setup). Only orders the stocks; never filters them.

    setup_points × setups fired + RS percentile / rs_percentile_divisor
    + weekly_rsi_points if weekly RSI is within weekly_rsi_band − warn_penalty × warns.
    With defaults: 2 per setup, up to 4 for the strongest relative strength,
    +1 for healthy weekly momentum, −1 per warning.
    """
    sc = criteria.score
    total = sc.setup_points * len(ev.setups_fired) - sc.warn_penalty * len(ev.warns)
    if rs_percentile is not None:
        total += rs_percentile / sc.rs_percentile_divisor
    lo, hi = sc.weekly_rsi_band
    if w_rsi is not None and lo <= w_rsi <= hi:
        total += sc.weekly_rsi_points
    return round(total, 2)
