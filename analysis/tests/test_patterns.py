"""Each candlestick pattern on a textbook example, and not on near-misses."""
from technicals import patterns as p
from tests.conftest import bars

DECLINE = [(110, 111, 107, 108), (108, 109, 105, 106), (106, 107, 103, 104), (104, 105, 101, 102)]
ADVANCE = [(100, 103, 99, 102), (102, 105, 101, 104), (104, 107, 103, 106), (106, 109, 105, 108)]


def test_hammer_after_decline():
    df = bars(DECLINE + [(101, 102, 95, 101.8)])         # long lower shadow, close near high
    assert p.hammer(df)
    assert not p.hammer(bars(ADVANCE + [(108, 109, 102, 108.8)]))  # same shape, but no prior decline
    assert not p.hammer(bars(DECLINE + [(101, 104, 95, 101.8)]))   # upper shadow too long


def test_shooting_star_after_advance():
    assert p.shooting_star(bars(ADVANCE + [(109, 115, 107.8, 108.2)]))
    assert not p.shooting_star(bars(DECLINE + [(102.5, 109, 102, 102.2)]))


def test_engulfing_both_ways():
    assert p.bullish_engulfing(bars(DECLINE + [(101, 101.5, 99, 100), (99.5, 103, 99, 102.5)]))
    assert not p.bullish_engulfing(bars(DECLINE + [(101, 101.5, 99, 100), (100.2, 101, 100, 100.8)]))  # body too small
    assert p.bearish_engulfing(bars(ADVANCE + [(108, 110, 107.5, 109), (109.5, 110, 106, 107.5)]))


def test_piercing():
    df = bars(DECLINE + [(102, 102.5, 97.5, 98), (97.5, 101, 97, 100.5)])  # closes above 100 midpoint
    assert p.piercing(df)
    assert not p.piercing(bars(DECLINE + [(102, 102.5, 97.5, 98), (97.5, 99.5, 97, 99)]))  # below midpoint


def test_morning_and_evening_star():
    assert p.morning_star(bars(DECLINE + [(102, 102.5, 96.5, 97), (96.8, 97.5, 96, 97.2), (97.5, 101, 97, 100.5)]))
    assert not p.morning_star(bars(DECLINE + [(102, 102.5, 96.5, 97), (97, 101, 96, 100.5), (100.5, 101, 100, 100.8)]))
    assert p.evening_star(bars(ADVANCE + [(108, 113.5, 107.5, 113), (113.2, 114, 112.5, 113), (112.5, 113, 109, 109.5)]))


def test_doji_and_inside_bar():
    assert p.doji(bars([(100, 103, 97, 100.2)]))
    assert not p.doji(bars([(100, 103, 97, 102)]))
    assert p.inside_bar(bars([(100, 105, 95, 102), (101, 104, 97, 103)]))
    assert not p.inside_bar(bars([(100, 105, 95, 102), (101, 106, 97, 103)]))


def test_detect_names_and_unknown_pattern():
    df = bars(DECLINE + [(101, 102, 95, 101.8)])
    assert "hammer" in p.detect(df)
    try:
        p.detect(df, names=["not_a_pattern"])
        raise AssertionError("expected ValueError")
    except ValueError:
        pass
