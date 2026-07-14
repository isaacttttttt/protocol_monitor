from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from app.market.models import Kline
from app.strategies.crypto_micro_rules import (
    CryptoMicroRuleConfig,
    FundingPoint,
    OpenInterestPoint,
    calculate_normalized_taker_flow,
    calculate_oi_washout,
    evaluate_cm2_short,
    evaluate_cm3_long,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _bar(
    index: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    symbol: str = "ETHUSDT",
    interval: str = "5m",
    volume: float = 100.0,
    taker_buy: float | None = 50.0,
    start: datetime = START,
) -> Kline:
    minutes = {"5m": 5, "15m": 15, "4h": 240}[interval]
    open_time = start + timedelta(minutes=index * minutes)
    return Kline(
        exchange="BINANCE",
        symbol=symbol,
        interval=interval,
        open_time=open_time,
        close_time=open_time + timedelta(minutes=minutes),
        open=Decimal(str(open_)),
        high=Decimal(str(high)),
        low=Decimal(str(low)),
        close=Decimal(str(close)),
        volume=Decimal(str(volume)),
        quote_volume=Decimal(str(volume * close)),
        is_closed=True,
        taker_buy_volume=Decimal(str(taker_buy)) if taker_buy is not None else None,
    )


def _btc_context(start: datetime) -> list[Kline]:
    highs = [10, 11, 14, 12, 11, 15, 13, 12]
    lows = [8, 9, 10, 9, 8, 11, 9, 7]
    closes = [9, 10, 13, 10, 9, 14.5, 10, 7]
    return [
        _bar(index, close, high, low, close, symbol="BTCUSDT", taker_buy=50, start=start)
        for index, (high, low, close) in enumerate(zip(highs, lows, closes))
    ]


def _down_4h(start: datetime) -> list[Kline]:
    highs = [10, 11, 14, 12, 11, 15, 13, 12]
    lows = [8, 9, 10, 9, 8, 11, 9, 7]
    closes = [9, 10, 13, 10, 9, 14.5, 10, 7]
    return [
        _bar(index, close, high, low, close, interval="4h", start=start)
        for index, (high, low, close) in enumerate(zip(highs, lows, closes))
    ]


def _funding(at: datetime, rate: float = 0.0001) -> list[FundingPoint]:
    return [FundingPoint(observed_at=at, available_at=at, rate=rate)]


def test_normalized_taker_flow_uses_real_taker_delta_and_rejects_missing_values():
    bars = [
        _bar(0, 10, 11, 9, 10, volume=100, taker_buy=60),
        _bar(1, 10, 11, 9, 10, volume=200, taker_buy=50),
    ]

    state = calculate_normalized_taker_flow(bars, lookback=2)

    assert state is not None
    assert state.normalized_cvd == pytest.approx(-80 / 300)
    assert state.normalized_delta == pytest.approx(-0.5)
    assert calculate_normalized_taker_flow([bars[0], _bar(1, 10, 11, 9, 10, taker_buy=None)], 2) is None


def test_oi_washout_uses_contract_log_change_and_excludes_current_from_median_mad():
    points = [
        OpenInterestPoint(
            observed_at=START + timedelta(minutes=5 * index),
            available_at=START + timedelta(minutes=5 * index),
            contracts=100.0,
        )
        for index in range(6)
    ]
    points.append(
        OpenInterestPoint(
            observed_at=START + timedelta(minutes=30),
            available_at=START + timedelta(minutes=30),
            contracts=90.0,
        )
    )

    state = calculate_oi_washout(points, points[-1].available_at, lookback=5, minimum_history=3)

    assert state is not None
    assert state.log_change == pytest.approx(math_log(0.9))
    assert state.historical_median == 0.0
    assert state.historical_mad == 0.0
    assert state.robust_z == float("-inf")


def math_log(value: float) -> float:
    # Keeps the test expectation visibly tied to a contract ratio.
    import math

    return math.log(value)


def test_as_of_filters_unavailable_oi_revision():
    points = [
        OpenInterestPoint(START + timedelta(minutes=5 * index), START + timedelta(minutes=5 * index), 100.0)
        for index in range(6)
    ]
    delayed = OpenInterestPoint(START + timedelta(minutes=30), START + timedelta(hours=2), 80.0)

    assert calculate_oi_washout(points + [delayed], START + timedelta(minutes=30), minimum_history=3) is not None
    state = calculate_oi_washout(points + [delayed], START + timedelta(hours=2), minimum_history=3)
    assert state is not None
    assert state.log_change == pytest.approx(math_log(0.8))


def test_cm2_without_confirmed_downside_swing_is_not_a_candidate():
    bars = [_bar(index, 10, 11, 9, 10, taker_buy=40) for index in range(8)]
    history_start = START - timedelta(days=10)
    profile = [_bar(index, 10, 11, 9, 10, interval="15m", start=history_start) for index in range(20)]
    evaluation = evaluate_cm2_short(
        as_of=bars[-1].close_time,
        eth_5m=bars,
        eth_15m=profile,
        eth_4h=_down_4h(history_start),
        btc_5m=_btc_context(history_start),
        funding=_funding(history_start),
        config=CryptoMicroRuleConfig(flow_lookback=3, profile_min_bars=10, profile_lookback_15m=10),
    )

    target = next(condition for condition in evaluation.conditions if condition.name == "confirmed_structure_target")
    assert target.status == "insufficient"
    assert evaluation.candidate is None


def _cm2_bars() -> list[Kline]:
    rows = [
        (10, 11, 9, 10),
        (10, 11, 8, 9),
        (9, 10, 5, 7),
        (7, 10, 7, 9),
        (9, 14, 8, 13),
        (13, 13, 10, 11),
        (11, 12, 9, 10),
        (10, 16, 9.5, 15),
        (15, 15, 10, 13),
        (13, 13, 8, 10),
        (10, 14, 9, 13),
        (13, 13.5, 10, 12),
        (12, 12.5, 7, 7.5),
    ]
    return [
        _bar(index, *row, taker_buy=20 if index == len(rows) - 1 else 50)
        for index, row in enumerate(rows)
    ]


def test_cm2_candidate_and_prefix_invariance():
    bars = _cm2_bars()
    history_start = START - timedelta(days=10)
    profile = [
        _bar(index, 10, 11, 9, 10, interval="15m", start=history_start)
        for index in range(20)
    ]
    config = CryptoMicroRuleConfig(
        atr_period=5,
        flow_lookback=3,
        profile_min_bars=10,
        profile_lookback_15m=10,
        setup_search_bars=5,
    )
    inputs = {
        "as_of": bars[-1].close_time,
        "eth_5m": bars,
        "eth_15m": profile,
        "eth_4h": _down_4h(history_start),
        "btc_5m": _btc_context(history_start),
        "funding": _funding(history_start),
        "config": config,
    }

    baseline = evaluate_cm2_short(**inputs)
    future = _bar(13, 7.5, 50, 1, 40, taker_buy=100)
    extended = evaluate_cm2_short(
        **{
            **inputs,
            "eth_5m": bars + [future],
            "funding": inputs["funding"] + [FundingPoint(future.close_time, future.close_time, -0.01)],
        }
    )

    assert baseline.status == "passed"
    assert baseline.candidate is not None
    assert baseline.candidate.direction == "SHORT"
    assert baseline.candidate.target == pytest.approx(5.0)
    assert baseline.candidate.minimum_rr == 1.5
    assert baseline.candidate.max_hold_bars == 576
    assert extended == baseline


def _cm3_bars() -> list[Kline]:
    rows = [
        (10, 11, 10, 10),
        (10, 12, 9, 11),
        (11, 15, 10, 14),
        (14, 14, 11, 12),
        (12, 13, 8.00, 10),
        (10, 12, 10, 11),
        (11, 12.5, 11, 12),
        (12, 12.2, 9.5, 10),
        (10, 12, 8.05, 10.5),
        (10.5, 11, 10, 10.5),
        (10.5, 11.5, 10.5, 11),
        (9.5, 10, 7, 9.5),
    ]
    bars = []
    for index, row in enumerate(rows):
        taker_buy = 70 if index == len(rows) - 1 else 30 if index >= 6 else 50
        bars.append(_bar(index, *row, taker_buy=taker_buy))
    return bars


def _oi_points_for_bars(bars: list[Kline]) -> list[OpenInterestPoint]:
    points = [
        OpenInterestPoint(bar.close_time, bar.close_time, 100.0)
        for bar in bars[:-1]
    ]
    points.append(OpenInterestPoint(bars[-1].close_time, bars[-1].close_time, 90.0))
    return points


def test_cm3_candidate_is_causal_and_has_execution_constraints():
    bars = _cm3_bars()
    context_start = START - timedelta(days=1)
    config = CryptoMicroRuleConfig(
        atr_period=5,
        flow_lookback=3,
        oi_min_history=3,
        oi_lookback=8,
        setup_search_bars=4,
    )

    evaluation = evaluate_cm3_long(
        as_of=bars[-1].close_time,
        eth_5m=bars,
        btc_5m=_btc_context(context_start),
        open_interest=_oi_points_for_bars(bars),
        funding=_funding(context_start),
        config=config,
    )

    assert [(condition.name, condition.status) for condition in evaluation.conditions] == [
        ("closed_history", "passed"),
        ("equal_low_sweep_reclaim", "passed"),
        ("wick_without_displacement", "passed"),
        ("bullish_cvd_divergence", "passed"),
        ("oi_washout", "passed"),
        ("btc_not_opposed", "passed"),
        ("funding_not_crowded", "passed"),
        ("confirmed_structure_target", "passed"),
    ]
    assert evaluation.status == "passed"
    assert evaluation.candidate is not None
    assert evaluation.candidate.direction == "LONG"
    assert evaluation.candidate.target == pytest.approx(15.0)
    assert evaluation.candidate.minimum_rr == 1.5
    assert evaluation.candidate.max_hold_bars == 576


def test_cm3_prefix_invariance_ignores_future_bars_and_derivatives():
    bars = _cm3_bars()
    context_start = START - timedelta(days=1)
    oi = _oi_points_for_bars(bars)
    funding = _funding(context_start)
    config = CryptoMicroRuleConfig(
        atr_period=5,
        flow_lookback=3,
        oi_min_history=3,
        oi_lookback=8,
        setup_search_bars=4,
    )
    as_of = bars[-1].close_time
    baseline = evaluate_cm3_long(
        as_of=as_of,
        eth_5m=bars,
        btc_5m=_btc_context(context_start),
        open_interest=oi,
        funding=funding,
        config=config,
    )
    future_bar = _bar(12, 9.5, 30, 2, 25, taker_buy=99)
    future_oi = OpenInterestPoint(future_bar.close_time, future_bar.close_time, 200.0)
    future_funding = FundingPoint(future_bar.close_time, future_bar.close_time, 0.01)

    extended = evaluate_cm3_long(
        as_of=as_of,
        eth_5m=bars + [future_bar],
        btc_5m=_btc_context(context_start) + [
            _bar(100, 10, 30, 9, 25, symbol="BTCUSDT", start=context_start, taker_buy=99)
        ],
        open_interest=oi + [future_oi],
        funding=funding + [future_funding],
        config=config,
    )

    assert extended == baseline


@pytest.mark.parametrize(
    ("rate", "expected"),
    [
        (0.0005, "passed"),
        (0.0005001, "not_met"),
    ],
)
def test_cm3_funding_crowding_boundary(rate: float, expected: str):
    bars = _cm3_bars()
    config = CryptoMicroRuleConfig(
        atr_period=5,
        flow_lookback=3,
        oi_min_history=3,
        oi_lookback=8,
        setup_search_bars=4,
    )
    evaluation = evaluate_cm3_long(
        as_of=bars[-1].close_time,
        eth_5m=bars,
        btc_5m=_btc_context(START - timedelta(days=1)),
        open_interest=_oi_points_for_bars(bars),
        funding=_funding(START, rate),
        config=config,
    )

    condition = next(item for item in evaluation.conditions if item.name == "funding_not_crowded")
    assert condition.status == expected


def test_disabled_gate_keeps_diagnostic_but_does_not_block_candidate():
    bars = _cm3_bars()
    config = CryptoMicroRuleConfig(
        atr_period=5,
        flow_lookback=3,
        oi_min_history=3,
        oi_lookback=8,
        setup_search_bars=4,
        enable_funding_gate=False,
    )
    evaluation = evaluate_cm3_long(
        as_of=bars[-1].close_time,
        eth_5m=bars,
        btc_5m=_btc_context(START - timedelta(days=1)),
        open_interest=_oi_points_for_bars(bars),
        funding=_funding(START, 0.01),
        config=config,
    )

    condition = next(item for item in evaluation.conditions if item.name == "funding_not_crowded")
    assert condition.status == "not_met"
    assert condition.required is False
    assert evaluation.status == "passed"
    assert evaluation.candidate is not None
