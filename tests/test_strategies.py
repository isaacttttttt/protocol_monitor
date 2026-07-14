from datetime import datetime, timedelta

import pytest

from app.market.kline_store import KlineStore
from app.signals.models import SignalLevel
from app.strategies.base import StrategyContext
from app.strategies.eth_cm2_pullback_fail_short import EthCm2PullbackFailShort
from app.strategies.eth_cm3_liquidity_sweep_long import EthCm3LiquiditySweepLong
from app.strategies.eth_stand_above_level import EthStandAboveLevel
from app.strategies.dynamic_levels import (
    build_liquidity_sweep_plan_from_candles,
    build_pullback_short_plan_from_candles,
)
from app.strategies.state_machine import StrategyStateEnum

from conftest import DummyRepo


@pytest.fixture
def store():
    return KlineStore(DummyRepo())


async def push(store, candle):
    await store.upsert_kline(candle)
    return StrategyContext(event=candle, store=store)


async def seed_flat_15m(store, kline_factory, count=30, price=2000):
    candles = []
    for index in range(count):
        candle = kline_factory(
            "ETHUSDT",
            "15m",
            price,
            price + 2,
            price - 2,
            price,
            minutes=index * 15,
        )
        candles.append(candle)
        await push(store, candle)
    return candles


def cm2_config(stop=1600):
    return {
        "id": "ETH_C_M2_PULLBACK_FAIL_SHORT_V1",
        "exchange": "BINANCE",
        "symbol": "ETHUSDT",
        "book": "Micro",
        "strategy_name": "C-M2 Pullback Fail Short",
        "direction": "SHORT",
        "zones": {"pullback_zone_1": {"low": 1583, "high": 1605}},
        "levels": {"fail_back_level": 1583, "first_invalid": 1605},
        "targets": {"tp1": 1544, "tp2": 1500, "tp3": 1457},
        "stop_loss": {"price": stop},
        "risk": {"min_rr_to_tp1": 1.5, "default_position_r": 0.25},
    }


@pytest.mark.asyncio
async def test_eth_cm2_l2_condition(store, kline_factory):
    strategy = EthCm2PullbackFailShort(cm2_config())
    event = kline_factory("ETHUSDT", "15m", 1580, 1600, 1578, 1590)
    signals = await strategy.on_market_update(await push(store, event))
    assert any(signal.level == SignalLevel.L2 for signal in signals)


@pytest.mark.asyncio
async def test_eth_cm2_l3_condition(store, kline_factory):
    strategy = EthCm2PullbackFailShort(cm2_config())
    await strategy.on_market_update(await push(store, kline_factory("ETHUSDT", "15m", 1580, 1600, 1578, 1590)))
    signals = await strategy.on_market_update(await push(store, kline_factory("ETHUSDT", "5m", 1590, 1592, 1575, 1580)))
    assert any(signal.level == SignalLevel.L3 for signal in signals)


@pytest.mark.asyncio
async def test_eth_cm2_l4_invalid(store, kline_factory):
    strategy = EthCm2PullbackFailShort(cm2_config())
    signals = await strategy.on_market_update(await push(store, kline_factory("ETHUSDT", "15m", 1600, 1610, 1590, 1606)))
    assert any(signal.level == SignalLevel.L4 for signal in signals)


@pytest.mark.asyncio
async def test_eth_stand_above_1605_l3(store, kline_factory):
    config = {
        "id": "ETH_STAND_ABOVE_1605_V1",
        "exchange": "BINANCE",
        "symbol": "ETHUSDT",
        "book": "Micro",
        "strategy_name": "ETH Stand Above 1605",
        "direction": "NEUTRAL",
        "levels": {"stand_above": 1605, "support_low": 1600, "fail_back": 1583},
    }
    strategy = EthStandAboveLevel(config)
    await push(store, kline_factory("ETHUSDT", "5m", 1605, 1610, 1602, 1608, minutes=0))
    await push(store, kline_factory("ETHUSDT", "5m", 1608, 1612, 1601, 1610, minutes=5))
    signals = await strategy.on_market_update(await push(store, kline_factory("ETHUSDT", "15m", 1600, 1615, 1595, 1608, minutes=15)))
    assert any(signal.level == SignalLevel.L3 for signal in signals)


@pytest.mark.asyncio
async def test_eth_cm3_liquidity_sweep_l3(store, kline_factory):
    config = {
        "id": "ETH_C_M3_LIQUIDITY_SWEEP_LONG_V1",
        "exchange": "BINANCE",
        "symbol": "ETHUSDT",
        "book": "Micro",
        "strategy_name": "C-M3 Liquidity Sweep Long",
        "direction": "LONG",
        "levels": {"sweep_level_1": 1544, "reclaim_level_1": 1570},
        "targets": {"tp1": 1645, "tp2": 1746, "tp3": 1982},
        "risk": {"min_rr_to_tp1": 1.5, "default_position_r": 0.25},
    }
    strategy = EthCm3LiquiditySweepLong(config)
    await strategy.on_market_update(await push(store, kline_factory("ETHUSDT", "5m", 1550, 1552, 1530, 1545)))
    signals = await strategy.on_market_update(await push(store, kline_factory("ETHUSDT", "15m", 1545, 1580, 1540, 1575)))
    assert any(signal.level == SignalLevel.L3 for signal in signals)


@pytest.mark.asyncio
async def test_dynamic_cm2_freezes_plan_and_ignores_legacy_fixed_stop(store, kline_factory):
    candles = await seed_flat_15m(store, kline_factory)
    plan = build_pullback_short_plan_from_candles(candles)
    assert plan is not None
    config = cm2_config(stop=1605)
    config["dynamic_levels"] = {"enabled": True}
    strategy = EthCm2PullbackFailShort(config)
    touch_price = (plan.zone_low + plan.zone_high) / 2
    touch = kline_factory(
        "ETHUSDT",
        "15m",
        touch_price,
        plan.zone_high,
        plan.zone_low,
        touch_price,
        minutes=30 * 15,
    )

    touch_signals = await strategy.on_market_update(await push(store, touch))
    frozen = strategy.state.context["dynamic_plan"]
    trigger_price = plan.fail_back - 0.2
    trigger = kline_factory(
        "ETHUSDT",
        "5m",
        touch_price,
        touch_price,
        trigger_price - 0.2,
        trigger_price,
        minutes=30 * 15 + 5,
    )
    trigger_signals = await strategy.on_market_update(await push(store, trigger))

    assert any(signal.level == SignalLevel.L2 for signal in touch_signals)
    assert strategy.state.context["dynamic_plan"] == frozen
    l3 = next(signal for signal in trigger_signals if signal.level == SignalLevel.L3)
    assert float(l3.sl) == pytest.approx(plan.invalid)
    assert float(l3.sl) != 1605


@pytest.mark.asyncio
async def test_dynamic_cm3_requires_micro_interval_and_freezes_sweep_plan(store, kline_factory):
    candles = await seed_flat_15m(store, kline_factory)
    plan = build_liquidity_sweep_plan_from_candles(candles)
    assert plan is not None
    config = {
        "id": "ETH_C_M3_LIQUIDITY_SWEEP_LONG_V1",
        "exchange": "BINANCE",
        "symbol": "ETHUSDT",
        "book": "Micro",
        "strategy_name": "C-M3 Liquidity Sweep Long",
        "direction": "LONG",
        "dynamic_levels": {"enabled": True, "min_lower_wick_ratio": 0.3},
        "levels": {"sweep_level_1": 1544, "reclaim_level_1": 1570},
        "targets": {"tp1": 1645, "tp2": 1746, "tp3": 1982},
        "stop_loss": {"atr_buffer_multiplier": 0.3},
        "risk": {"min_rr_to_tp1": 1.5, "default_position_r": 0.25},
    }
    strategy = EthCm3LiquiditySweepLong(config)
    ignored = kline_factory("ETHUSDT", "4h", 2000, 2002, plan.sweep_level - 5, 2001, minutes=30 * 15)
    ignored_signals = await strategy.on_market_update(await push(store, ignored))
    sweep = kline_factory("ETHUSDT", "15m", 2000, 2002, plan.sweep_level - 4, 2001, minutes=30 * 15)

    signals = await strategy.on_market_update(await push(store, sweep))

    assert ignored_signals == []
    assert any(signal.level == SignalLevel.L3 for signal in signals)
    assert strategy.state.context["dynamic_plan"]["sweep_level"] == pytest.approx(plan.sweep_level)


@pytest.mark.asyncio
async def test_triggered_micro_strategy_expires_after_configured_time_stop(store, kline_factory):
    config = {
        "id": "ETH_STAND_ABOVE_1605_V1",
        "exchange": "BINANCE",
        "symbol": "ETHUSDT",
        "book": "Micro",
        "strategy_name": "ETH Stand Above 1605",
        "direction": "NEUTRAL",
        "max_hold_hours": 48,
        "levels": {"stand_above": 1605, "support_low": 1600, "fail_back": 1583},
    }
    strategy = EthStandAboveLevel(config)
    strategy.state.state = StrategyStateEnum.TRIGGERED
    strategy.state.entered_state_at = datetime(2026, 1, 1)
    event = kline_factory("ETHUSDT", "15m", 1600, 1610, 1590, 1608)
    context = StrategyContext(event=event, store=store, now=datetime(2026, 1, 3))

    signals = await strategy.on_market_update(context)

    assert signals[0].status == "EXPIRED"
    assert signals[0].risk_flags["time_stop"] is True
