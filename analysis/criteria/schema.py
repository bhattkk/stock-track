"""Shape of criteria/technicals.yaml, validated with pydantic on load.

Validation catches typos early, before a 50-minute run: an unknown setup or
flag id, an unknown candlestick pattern, a bad operator, or a missing `why`.
Gate metric names are checked against the metrics actually computed, the
first time a stock is evaluated (see technicals/rules.py).
"""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

from technicals.patterns import PATTERNS
from technicals.setups import FLAGS, SETUPS

Op = Literal[">", ">=", "<", "<=", "==", "!="]


class GateRule(BaseModel):
    """A must-pass comparison: `metric op value`, e.g. close >= 50."""
    id: str
    title: str
    metric: str
    op: Op
    value: float
    why: str = Field(min_length=10)
    enabled: bool = True


class SignalRule(BaseModel):
    """A setup or flag: one of the detectors in technicals/setups.py, with its parameters."""
    id: str
    title: str
    why: str = Field(min_length=10)
    enabled: bool = True
    params: dict = Field(default_factory=dict)

    @field_validator("params")
    @classmethod
    def _known_patterns(cls, params: dict) -> dict:
        """Any `*_patterns` list must name patterns that exist in patterns.py."""
        for key, val in params.items():
            if key.endswith("_patterns"):
                unknown = set(val) - set(PATTERNS)
                if unknown:
                    raise ValueError(f"{key}: unknown pattern(s) {sorted(unknown)}; known: {sorted(PATTERNS)}")
        return params


class FlagRule(SignalRule):
    """A red flag: `warn` only lowers the score; `reject` fails the stock."""
    action: Literal["warn", "reject"] = "warn"


class Setups(BaseModel):
    min_fired: int = Field(1, ge=0)
    rules: list[SignalRule]

    @model_validator(mode="after")
    def _known_ids(self):
        """Every setup id must have a detector in SETUPS."""
        for r in self.rules:
            if r.id not in SETUPS:
                raise ValueError(f"Unknown setup {r.id!r}; known: {sorted(SETUPS)}")
        return self


class Score(BaseModel):
    """Weights for ranking the stocks that pass (never used to filter)."""
    setup_points: float = 2.0
    rs_percentile_divisor: float = 25.0
    weekly_rsi_band: tuple[float, float] = (50.0, 75.0)
    weekly_rsi_points: float = 1.0
    warn_penalty: float = 1.0


class Criteria(BaseModel):
    version: int = 1
    tradability: list[GateRule]
    trend: list[GateRule]
    setups: Setups
    flags: list[FlagRule]
    score: Score = Score()

    @model_validator(mode="after")
    def _known_flags_and_unique_ids(self):
        """Flag ids must have detectors; rule ids must be unique across the file."""
        for f in self.flags:
            if f.id not in FLAGS:
                raise ValueError(f"Unknown flag {f.id!r}; known: {sorted(FLAGS)}")
        ids = [r.id for r in self.tradability + self.trend + self.setups.rules + self.flags]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ValueError(f"Duplicate rule ids: {sorted(dupes)}")
        return self


def load(path: Path) -> tuple[Criteria, str]:
    """Parse and validate the YAML file. Returns (criteria, sha256 of the file).

    The hash is stored on each run, so you can tell which version of the rules
    produced a set of results.
    """
    raw = path.read_bytes()
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:  # bad indentation, unclosed quote, ...
        raise ValueError(f"{path.name} is not valid YAML: {exc}") from exc
    return Criteria.model_validate(data), hashlib.sha256(raw).hexdigest()
