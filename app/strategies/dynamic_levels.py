from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from app.indicators.atr import calculate_atr
from app.indicators.vwap import calculate_vwap
from app.market.kline_store import KlineStore


@dataclass(frozen=True)
class DynamicPullbackPlan:
    zone_low: float
    zone_high: float
    fail_back: float
    invalid: float
    atr: float
    reference: float


@dataclass(frozen=True)
class DynamicSweepPlan:
    sweep_level: float
    reclaim: float
    atr: float


def build_pullback_short_plan(
    store: KlineStore,
    exchange: str,
    symbol: str,
    decision_time: datetime,
) -> DynamicPullbackPlan | None:
    """Build a causal VWAP/ATR pullback zone from completed 15-minute bars."""
    candles = _prior_candles(store, exchange, symbol, "15m", decision_time, 80)
    return build_pullback_short_plan_from_candles(candles)


def build_pullback_short_plan_from_candles(candles) -> DynamicPullbackPlan | None:
    """Build a plan from bars that were closed before the decision candle."""
    if len(candles) < 20:
        return None
    window = candles[-40:]
    atr = calculate_atr(window)
    reference = calculate_vwap(window[-20:])
    if atr <= 0 or reference <= 0:
        return None
    return DynamicPullbackPlan(
        zone_low=reference - 0.20 * atr,
        zone_high=reference + 0.25 * atr,
        fail_back=reference - 0.15 * atr,
        invalid=reference + 0.75 * atr,
        atr=atr,
        reference=reference,
    )


def build_liquidity_sweep_plan(
    store: KlineStore,
    exchange: str,
    symbol: str,
    decision_time: datetime,
) -> DynamicSweepPlan | None:
    """Use the prior 20-bar low so the current sweep cannot define itself."""
    candles = _prior_candles(store, exchange, symbol, "15m", decision_time, 80)
    return build_liquidity_sweep_plan_from_candles(candles)


def build_liquidity_sweep_plan_from_candles(candles) -> DynamicSweepPlan | None:
    """Build a sweep plan without including the candle being evaluated."""
    if len(candles) < 20:
        return None
    window = candles[-20:]
    atr = calculate_atr(candles[-40:])
    sweep_level = min(float(candle.low) for candle in window)
    if atr <= 0 or sweep_level <= 0:
        return None
    return DynamicSweepPlan(
        sweep_level=sweep_level,
        reclaim=sweep_level + 0.10 * atr,
        atr=atr,
    )


def _prior_candles(
    store: KlineStore,
    exchange: str,
    symbol: str,
    interval: str,
    decision_time: datetime,
    limit: int,
):
    candles = store.get_recent(exchange, symbol, interval, limit + 1)
    return [candle for candle in candles if candle.is_closed and candle.close_time < decision_time][-limit:]
