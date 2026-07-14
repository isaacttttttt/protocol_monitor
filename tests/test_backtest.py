from datetime import UTC, datetime

import pytest

from app.backtest.engine import BacktestConfig, BacktestEngine, EntryIntent, FundingRate


def test_backtest_enters_next_bar_and_uses_stop_first_for_ambiguous_bar(kline_factory):
    bars = [
        kline_factory("TEST", "15m", 100, 101, 99, 100, minutes=0),
        kline_factory("TEST", "15m", 100, 112, 89, 101, minutes=15),
    ]
    engine = BacktestEngine(BacktestConfig(fee_bps_per_side=0, slippage_bps_per_side=0))

    result = engine.run(bars, {0: EntryIntent("LONG", stop=90, target=110, reason="test")})

    assert len(result.trades) == 1
    assert result.trades[0].entry_time == bars[1].open_time
    assert result.trades[0].exit_reason == "AMBIGUOUS_STOP_FIRST"
    assert result.trades[0].gross_r == -1
    assert result.max_drawdown_pct > 0


def test_backtest_costs_reduce_net_r(kline_factory):
    bars = [
        kline_factory("TEST", "15m", 100, 101, 99, 100, minutes=0),
        kline_factory("TEST", "15m", 100, 111, 99, 110, minutes=15),
    ]

    result = BacktestEngine(BacktestConfig(fee_bps_per_side=10, slippage_bps_per_side=0)).run(
        bars,
        {0: EntryIntent("LONG", stop=90, target=110, reason="test")},
    )

    assert result.trades[0].gross_r == 1
    assert result.trades[0].net_r < 1


def test_backtest_rechecks_minimum_rr_at_actual_next_open(kline_factory):
    bars = [
        kline_factory("TEST", "5m", 100, 101, 99, 100, minutes=0),
        kline_factory("TEST", "5m", 108, 109, 107, 108, minutes=5),
    ]

    result = BacktestEngine(
        BacktestConfig(fee_bps_per_side=0, slippage_bps_per_side=0)
    ).run(
        bars,
        {
            0: EntryIntent(
                "LONG",
                stop=90,
                target=110,
                reason="test",
                minimum_rr=1.5,
            )
        },
    )

    assert result.trades == []


def test_backtest_stop_gap_exits_at_open(kline_factory):
    bars = [
        kline_factory("TEST", "5m", 100, 101, 99, 100, minutes=0),
        kline_factory("TEST", "5m", 100, 102, 99, 101, minutes=5),
        kline_factory("TEST", "5m", 85, 88, 80, 82, minutes=10),
    ]

    result = BacktestEngine(
        BacktestConfig(fee_bps_per_side=0, slippage_bps_per_side=0)
    ).run(
        bars,
        {0: EntryIntent("LONG", stop=90, target=120, reason="test")},
    )

    assert result.trades[0].exit_reason == "STOP_GAP"
    assert result.trades[0].exit == 85
    assert result.trades[0].gross_r == -1.5


def test_backtest_applies_aware_funding_to_naive_market_times(kline_factory):
    bars = [
        kline_factory("TEST", "5m", 100, 101, 99, 100, minutes=0),
        kline_factory("TEST", "5m", 100, 102, 99, 101, minutes=5),
        kline_factory("TEST", "5m", 101, 102, 100, 101, minutes=10),
    ]
    funding = [FundingRate(datetime(2026, 1, 1, 0, 7, tzinfo=UTC), 0.001, 100)]

    result = BacktestEngine(
        BacktestConfig(fee_bps_per_side=0, slippage_bps_per_side=0)
    ).run(
        bars,
        {0: EntryIntent("LONG", stop=90, target=120, reason="test", max_hold_bars=2)},
        funding,
    )

    assert result.trades[0].funding_r == pytest.approx(-0.01)
    assert len(result.equity_curve) == len(bars) + 1
