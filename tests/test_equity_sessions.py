from datetime import UTC, datetime

from app.review.indicator_snapshot import _opening_range, _premarket_summary, _regular_session_open
from app.strategies.equity_orb_retest import OrbRetestConfig, evaluate_orb_retest


def _bar(iso_time: str, open_: float, high: float, low: float, close: float, volume: float = 100):
    return {
        "time": iso_time,
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def test_opening_range_uses_new_york_regular_session_and_requires_two_bars():
    # July is EDT: 09:30 ET == 13:30 UTC.
    candles = [
        _bar("2026-07-13T12:00:00+00:00", 90, 95, 89, 94),
        _bar("2026-07-13T13:30:00+00:00", 100, 103, 99, 102),
    ]
    forming = _opening_range(candles)
    candles.append(_bar("2026-07-13T13:45:00+00:00", 102, 104, 101, 103))
    complete = _opening_range(candles)

    assert forming["status"] == "FORMING"
    assert complete["complete"] is True
    assert complete["high"] == 104
    assert complete["low"] == 99
    assert _regular_session_open(candles, "2026-07-13") == 100


def test_premarket_summary_compares_same_time_of_day():
    candles = [
        _bar("2026-07-10T12:00:00+00:00", 90, 91, 89, 90, 100),
        _bar("2026-07-13T12:00:00+00:00", 100, 102, 99, 101, 200),
    ]

    summary = _premarket_summary(candles)

    assert summary["relative_volume"] == 2
    assert summary["through_et"] == "08:00"


def test_orb_retest_requires_breakout_then_retest_confirmation():
    candles = [
        _bar("2026-07-10T12:00:00+00:00", 98, 99, 97, 98, 100),
        _bar("2026-07-13T12:00:00+00:00", 101, 102, 100, 101, 300),
        _bar("2026-07-13T13:30:00+00:00", 102, 104, 101, 103, 500),
        _bar("2026-07-13T13:45:00+00:00", 103, 105, 102, 104, 500),
        _bar("2026-07-13T14:00:00+00:00", 104, 107, 104, 106, 800),
        _bar("2026-07-13T14:15:00+00:00", 106, 107, 104.9, 106, 10_000),
    ]

    result = evaluate_orb_retest(
        candles,
        previous_close=100,
        config=OrbRetestConfig(minimum_premarket_rvol=1.5, minimum_confirmation_rvol=1.2),
    )

    assert result["status"] == "TRIGGERED"
    assert result["direction"] == "LONG"
    assert result["stop"] < result["entry_reference"] < result["target"]
