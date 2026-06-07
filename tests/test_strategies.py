import pytest

from app.market.kline_store import KlineStore
from app.signals.models import SignalLevel
from app.strategies.base import StrategyContext
from app.strategies.eth_cm2_pullback_fail_short import EthCm2PullbackFailShort
from app.strategies.eth_cm3_liquidity_sweep_long import EthCm3LiquiditySweepLong
from app.strategies.eth_stand_above_level import EthStandAboveLevel

from conftest import DummyRepo


@pytest.fixture
def store():
    return KlineStore(DummyRepo())


async def push(store, candle):
    await store.upsert_kline(candle)
    return StrategyContext(event=candle, store=store)


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
