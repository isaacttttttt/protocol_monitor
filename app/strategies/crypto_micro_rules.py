from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
import math
from statistics import median
from typing import Literal, Mapping, Sequence

from app.indicators.atr import calculate_atr
from app.indicators.structure import detect_structure
from app.market.models import Kline

ConditionStatus = Literal["passed", "not_met", "insufficient"]
Direction = Literal["LONG", "SHORT"]
StrategyName = Literal["C-M2", "C-M3"]


@dataclass(frozen=True)
class OpenInterestPoint:
    """An OI observation and the time at which it became usable."""

    observed_at: datetime
    available_at: datetime
    contracts: float


@dataclass(frozen=True)
class FundingPoint:
    """A settled funding observation and the time at which it became usable."""

    observed_at: datetime
    available_at: datetime
    rate: float


@dataclass(frozen=True)
class RuleCondition:
    name: str
    status: ConditionStatus
    detail: str
    required: bool = True


@dataclass(frozen=True)
class MicroSignalCandidate:
    """Execution-independent signal that a replay can map to EntryIntent."""

    strategy: StrategyName
    direction: Direction
    signal_time: datetime
    available_at: datetime
    stop: float
    target: float
    reason: str
    risk_fraction: float = 0.0025
    minimum_rr: float = 1.5
    max_hold_bars: int = 576


@dataclass(frozen=True)
class MicroRuleEvaluation:
    strategy: StrategyName
    as_of: datetime
    conditions: tuple[RuleCondition, ...]
    metrics: Mapping[str, float | int | bool | str | None] = field(default_factory=dict)
    candidate: MicroSignalCandidate | None = None

    @property
    def status(self) -> ConditionStatus:
        """Summarize a condition funnel without conflating missing data and misses."""
        required = [condition for condition in self.conditions if condition.required]
        if any(condition.status == "insufficient" for condition in required):
            return "insufficient"
        if any(condition.status == "not_met" for condition in required):
            return "not_met"
        return "passed"


@dataclass(frozen=True)
class TakerFlowState:
    normalized_cvd: float
    normalized_delta: float
    available_at: datetime


@dataclass(frozen=True)
class OpenInterestWashout:
    log_change: float
    historical_median: float
    historical_mad: float
    historical_lower_quantile: float
    robust_z: float
    observed_at: datetime
    available_at: datetime


@dataclass(frozen=True)
class CryptoMicroRuleConfig:
    """Shared deterministic thresholds for five-minute C-M2/C-M3 replay."""

    atr_period: int = 14
    structure_lookback: int = 120
    pivot_left: int = 2
    pivot_right: int = 2
    flow_lookback: int = 20
    profile_lookback_15m: int = 48
    profile_min_bars: int = 20
    profile_bins: int = 12
    setup_search_bars: int = 12
    retest_tolerance_atr: float = 0.15
    equal_low_tolerance_atr: float = 0.12
    minimum_sweep_atr: float = 0.02
    reclaim_buffer_atr: float = 0.02
    minimum_lower_wick_ratio: float = 0.30
    stop_buffer_atr: float = 0.10
    oi_lookback: int = 96
    oi_min_history: int = 20
    oi_washout_z: float = -2.0
    lower_quantile: float = 0.40
    upper_quantile: float = 0.60
    crowded_funding_rate: float = 0.0005
    minimum_rr: float = 1.5
    max_hold_bars: int = 576
    risk_fraction: float = 0.0025
    enable_cvd_gate: bool = True
    enable_oi_gate: bool = True
    enable_funding_gate: bool = True

    def __post_init__(self) -> None:
        positive_integers = {
            "atr_period": self.atr_period,
            "structure_lookback": self.structure_lookback,
            "pivot_left": self.pivot_left,
            "pivot_right": self.pivot_right,
            "flow_lookback": self.flow_lookback,
            "profile_lookback_15m": self.profile_lookback_15m,
            "profile_min_bars": self.profile_min_bars,
            "profile_bins": self.profile_bins,
            "setup_search_bars": self.setup_search_bars,
            "oi_lookback": self.oi_lookback,
            "oi_min_history": self.oi_min_history,
            "max_hold_bars": self.max_hold_bars,
        }
        invalid = [name for name, value in positive_integers.items() if value < 1]
        if invalid:
            raise ValueError(f"configuration values must be positive: {', '.join(invalid)}")
        if self.minimum_rr <= 0 or self.risk_fraction <= 0:
            raise ValueError("minimum_rr and risk_fraction must be positive")
        if not 0.0 < self.lower_quantile < 0.5 < self.upper_quantile < 1.0:
            raise ValueError("quantile thresholds must satisfy 0 < lower < 0.5 < upper < 1")


@dataclass(frozen=True)
class _ConfirmedPivot:
    index: int
    confirmed_index: int
    price: float


@dataclass(frozen=True)
class _VolumeNode:
    low: float
    high: float
    level: float
    available_at: datetime


@dataclass(frozen=True)
class _Cm2Setup:
    loss_index: int
    node: _VolumeNode


@dataclass(frozen=True)
class _Cm3Setup:
    sweep_index: int
    equal_low: float
    sweep_low: float
    atr: float


def calculate_normalized_taker_flow(
    candles: Sequence[Kline],
    lookback: int = 20,
) -> TakerFlowState | None:
    """Return rolling taker delta divided by rolling base volume.

    No OHLCV-sign proxy is used here. A window with missing taker-buy volume is
    deliberately unavailable so a backtest cannot silently change data source.
    """
    if lookback < 1:
        raise ValueError("lookback must be positive")
    if len(candles) < lookback:
        return None
    window = candles[-lookback:]
    deltas: list[float] = []
    total_volume = 0.0
    for candle in window:
        volume = float(candle.volume)
        if volume <= 0 or candle.taker_buy_volume is None:
            return None
        taker_buy = float(candle.taker_buy_volume)
        if taker_buy < 0 or taker_buy > volume:
            return None
        deltas.append(2.0 * taker_buy - volume)
        total_volume += volume
    if total_volume <= 0:
        return None
    last_volume = float(window[-1].volume)
    return TakerFlowState(
        normalized_cvd=sum(deltas) / total_volume,
        normalized_delta=deltas[-1] / last_volume,
        available_at=window[-1].close_time,
    )


def calculate_oi_washout(
    points: Sequence[OpenInterestPoint],
    as_of: datetime,
    *,
    lookback: int = 96,
    minimum_history: int = 20,
    lower_quantile: float = 0.40,
) -> OpenInterestWashout | None:
    """Robustly normalize the latest contract-quantity log change.

    The latest change is compared with the median/MAD of earlier changes only.
    Both observation time and publication time must be no later than ``as_of``.
    """
    if lookback < 1 or minimum_history < 1:
        raise ValueError("lookback and minimum_history must be positive")
    if not 0.0 < lower_quantile < 0.5:
        raise ValueError("lower_quantile must be between zero and 0.5")
    causal = _causal_oi_points(points, as_of)
    changes: list[tuple[OpenInterestPoint, float]] = []
    for previous, current in zip(causal, causal[1:]):
        if previous.contracts <= 0 or current.contracts <= 0:
            continue
        changes.append((current, math.log(current.contracts / previous.contracts)))
    if len(changes) <= minimum_history:
        return None
    current_point, current_change = changes[-1]
    history = [change for _, change in changes[-lookback - 1 : -1]]
    if len(history) < minimum_history:
        return None
    center = median(history)
    mad = median(abs(change - center) for change in history)
    if math.isclose(mad, 0.0, abs_tol=1e-15):
        if math.isclose(current_change, center, abs_tol=1e-15):
            robust_z = 0.0
        else:
            robust_z = math.copysign(math.inf, current_change - center)
    else:
        robust_z = (current_change - center) / (1.4826 * mad)
    return OpenInterestWashout(
        log_change=current_change,
        historical_median=center,
        historical_mad=mad,
        historical_lower_quantile=_quantile(history, lower_quantile),
        robust_z=robust_z,
        observed_at=current_point.observed_at,
        available_at=current_point.available_at,
    )


def evaluate_cm2_short(
    *,
    as_of: datetime,
    eth_5m: Sequence[Kline],
    eth_15m: Sequence[Kline],
    eth_4h: Sequence[Kline],
    btc_5m: Sequence[Kline],
    funding: Sequence[FundingPoint],
    config: CryptoMicroRuleConfig | None = None,
) -> MicroRuleEvaluation:
    """Evaluate the causal C-M2 POC/HVN-loss and failed-retest short."""
    settings = config or CryptoMicroRuleConfig()
    bars_5m = _causal_bars(eth_5m, as_of, "5m")
    bars_15m = _causal_bars(eth_15m, as_of, "15m")
    bars_4h = _causal_bars(eth_4h, as_of, "4h")
    btc_bars = _causal_bars(btc_5m, as_of, "5m")
    conditions: list[RuleCondition] = []
    metrics: dict[str, float | int | bool | str | None] = {}

    minimum_5m = settings.pivot_left + settings.pivot_right + 2
    if settings.enable_cvd_gate:
        minimum_5m = max(minimum_5m, settings.flow_lookback + 1)
    bars_ready = len(bars_5m) >= minimum_5m and len(bars_15m) >= settings.profile_min_bars
    conditions.append(
        _condition(
            "closed_history",
            bars_ready if bars_ready else None,
            f"ETH closed bars: 5m={len(bars_5m)}, 15m={len(bars_15m)}",
        )
    )

    bearish_4h: bool | None = None
    if len(bars_4h) >= settings.pivot_left + settings.pivot_right + 2:
        structure_4h = detect_structure(
            bars_4h,
            lookback=settings.structure_lookback,
            pivot_left=settings.pivot_left,
            pivot_right=settings.pivot_right,
        )
        bearish_4h = structure_4h.trend == "DOWN"
        metrics["structure_4h"] = structure_4h.trend
    conditions.append(_condition("bearish_4h_context", bearish_4h, "4h confirmed structure trend is DOWN"))

    atr = calculate_atr(bars_5m, settings.atr_period) if bars_ready else 0.0
    setup = _find_cm2_setup(bars_5m, bars_15m, atr, settings) if atr > 0 else None
    setup_status = None if not bars_ready or atr <= 0 else setup is not None
    conditions.append(_condition("poc_hvn_loss_and_retest", setup_status, "causal profile node lost, then retested from below"))
    if setup is not None:
        metrics["profile_level"] = setup.node.level
        metrics["profile_available_at"] = setup.node.available_at.isoformat()
        metrics["loss_bar_index"] = setup.loss_index

    structure_5m = None
    if len(bars_5m) >= settings.pivot_left + settings.pivot_right + 2:
        structure_5m = detect_structure(
            bars_5m,
            lookback=settings.structure_lookback,
            pivot_left=settings.pivot_left,
            pivot_right=settings.pivot_right,
        )
    choch_down = structure_5m.choch_down if structure_5m is not None else None
    conditions.append(_condition("bearish_5m_choch", choch_down, "latest closed 5m bar confirms bearish CHoCH"))

    flow_series = _normalized_taker_flow_series(bars_5m, settings.flow_lookback)
    no_follow: bool | None = None
    if setup is not None and flow_series and flow_series[-1] is not None:
        previous_flow = [value for value in flow_series[setup.loss_index : -1] if value is not None]
        current_flow = flow_series[-1]
        if previous_flow and current_flow is not None:
            current_delta = _normalized_taker_delta(bars_5m[-1])
            flow_threshold = _quantile(previous_flow, settings.lower_quantile)
            no_follow = current_flow <= flow_threshold and current_delta is not None and current_delta <= 0.0
            metrics["normalized_cvd"] = current_flow
            metrics["normalized_taker_delta"] = current_delta
            metrics["cvd_lower_quantile"] = flow_threshold
    conditions.append(
        _condition(
            "cvd_no_follow",
            no_follow,
            "retest has no normalized taker-CVD high and current delta is non-positive",
            required=settings.enable_cvd_gate,
        )
    )

    btc_opposed = _btc_opposition(btc_bars, "SHORT", settings)
    btc_not_opposed = None if btc_opposed is None else not btc_opposed
    conditions.append(_condition("btc_not_opposed", btc_not_opposed, "BTC is not in a confirmed bullish break with positive taker flow"))

    funding_rate = _latest_funding_rate(funding, as_of)
    funding_ok = None if funding_rate is None else funding_rate >= -settings.crowded_funding_rate
    if funding_rate is not None:
        metrics["funding_rate"] = funding_rate
    conditions.append(
        _condition(
            "funding_not_crowded",
            funding_ok,
            "short blocked only when settled funding is excessively negative",
            required=settings.enable_funding_gate,
        )
    )

    target, target_status = _confirmed_structure_target(bars_5m, "SHORT", settings)
    conditions.append(target_status)
    if target is not None:
        metrics["target_swing"] = target

    candidate = None
    if _all_passed(conditions) and setup is not None and target is not None:
        current = bars_5m[-1]
        stop = max(setup.node.level, *(float(bar.high) for bar in bars_5m[setup.loss_index :])) + atr * settings.stop_buffer_atr
        if stop > float(current.close) and target < float(current.close):
            available_at = _latest_datetime(
                current.close_time,
                bars_15m[-1].close_time,
                bars_4h[-1].close_time,
                btc_bars[-1].close_time,
                setup.node.available_at,
                _latest_funding_available_at(funding, as_of),
            )
            candidate = MicroSignalCandidate(
                strategy="C-M2",
                direction="SHORT",
                signal_time=current.close_time,
                available_at=available_at,
                stop=stop,
                target=target,
                reason=(
                    f"C-M2: 4h down; profile {setup.node.level:.8g} lost and failed on retest; "
                    "5m bearish CHoCH"
                    + (" with taker-CVD non-confirmation" if settings.enable_cvd_gate else "")
                ),
                risk_fraction=settings.risk_fraction,
                minimum_rr=settings.minimum_rr,
                max_hold_bars=settings.max_hold_bars,
            )
    return MicroRuleEvaluation("C-M2", as_of, tuple(conditions), metrics, candidate)


def evaluate_cm3_long(
    *,
    as_of: datetime,
    eth_5m: Sequence[Kline],
    btc_5m: Sequence[Kline],
    open_interest: Sequence[OpenInterestPoint],
    funding: Sequence[FundingPoint],
    config: CryptoMicroRuleConfig | None = None,
) -> MicroRuleEvaluation:
    """Evaluate the causal C-M3 equal-low sweep and reclaim long."""
    settings = config or CryptoMicroRuleConfig()
    bars_5m = _causal_bars(eth_5m, as_of, "5m")
    btc_bars = _causal_bars(btc_5m, as_of, "5m")
    conditions: list[RuleCondition] = []
    metrics: dict[str, float | int | bool | str | None] = {}

    minimum_5m = max(settings.pivot_left + settings.pivot_right + 2, settings.atr_period + 1)
    if settings.enable_cvd_gate:
        minimum_5m = max(minimum_5m, settings.flow_lookback + settings.pivot_left + settings.pivot_right + 2)
    bars_ready = len(bars_5m) >= minimum_5m
    conditions.append(
        _condition(
            "closed_history",
            bars_ready if bars_ready else None,
            f"ETH closed 5m bars={len(bars_5m)}",
        )
    )
    atr = calculate_atr(bars_5m, settings.atr_period) if bars_ready else 0.0
    setup = _find_cm3_setup(bars_5m, atr, settings) if atr > 0 else None
    setup_status = None if not bars_ready or atr <= 0 else setup is not None
    conditions.append(_condition("equal_low_sweep_reclaim", setup_status, "two confirmed pivot lows are swept and the level is reclaimed"))
    wick_ok: bool | None = None
    if setup is not None:
        sweep_bar = bars_5m[setup.sweep_index]
        wick_ok = _lower_wick_ratio(sweep_bar) >= settings.minimum_lower_wick_ratio and not _is_bearish_displacement(
            bars_5m, setup.sweep_index
        )
        metrics.update(
            {
                "equal_low": setup.equal_low,
                "sweep_low": setup.sweep_low,
                "lower_wick_ratio": _lower_wick_ratio(sweep_bar),
            }
        )
    conditions.append(_condition("wick_without_displacement", wick_ok, "sweep has a lower wick and is not bearish displacement"))

    flow_series = _normalized_taker_flow_series(bars_5m, settings.flow_lookback)
    bullish_divergence: bool | None = None
    if setup is not None and flow_series:
        sweep_flow = flow_series[setup.sweep_index]
        prior_start = max(0, setup.sweep_index - settings.flow_lookback)
        prior_flow = [value for value in flow_series[prior_start : setup.sweep_index] if value is not None]
        if sweep_flow is not None and prior_flow:
            prior_lows = [float(bar.low) for bar in bars_5m[prior_start : setup.sweep_index]]
            flow_threshold = _quantile(prior_flow, settings.upper_quantile)
            bullish_divergence = bool(prior_lows) and setup.sweep_low < min(prior_lows) and sweep_flow >= flow_threshold
            metrics["normalized_cvd_at_sweep"] = sweep_flow
            metrics["cvd_upper_quantile"] = flow_threshold
    conditions.append(
        _condition(
            "bullish_cvd_divergence",
            bullish_divergence,
            "price lower low is not confirmed by normalized taker CVD",
            required=settings.enable_cvd_gate,
        )
    )

    oi_state = _oi_washout_near_sweep(open_interest, as_of, bars_5m, setup, settings)
    oi_ok = (
        None
        if oi_state is None
        else oi_state.robust_z <= settings.oi_washout_z
        and oi_state.log_change <= oi_state.historical_lower_quantile
    )
    if oi_state is not None:
        metrics.update(
            {
                "oi_log_change": oi_state.log_change,
                "oi_historical_median": oi_state.historical_median,
                "oi_historical_mad": oi_state.historical_mad,
                "oi_historical_lower_quantile": oi_state.historical_lower_quantile,
                "oi_robust_z": oi_state.robust_z,
                "oi_available_at": oi_state.available_at.isoformat(),
            }
        )
    conditions.append(
        _condition(
            "oi_washout",
            oi_ok,
            "contract-quantity log change is below its historical median/MAD threshold",
            required=settings.enable_oi_gate,
        )
    )

    btc_opposed = _btc_opposition(btc_bars, "LONG", settings)
    btc_not_opposed = None if btc_opposed is None else not btc_opposed
    conditions.append(_condition("btc_not_opposed", btc_not_opposed, "BTC is not in a confirmed bearish break with negative taker flow"))

    funding_rate = _latest_funding_rate(funding, as_of)
    funding_ok = None if funding_rate is None else funding_rate <= settings.crowded_funding_rate
    if funding_rate is not None:
        metrics["funding_rate"] = funding_rate
    conditions.append(
        _condition(
            "funding_not_crowded",
            funding_ok,
            "long blocked only when settled funding is excessively positive",
            required=settings.enable_funding_gate,
        )
    )

    target, target_status = _confirmed_structure_target(bars_5m, "LONG", settings)
    conditions.append(target_status)
    if target is not None:
        metrics["target_swing"] = target

    candidate = None
    if _all_passed(conditions) and setup is not None and target is not None:
        current = bars_5m[-1]
        stop = setup.sweep_low - setup.atr * settings.stop_buffer_atr
        if stop < float(current.close) and target > float(current.close):
            available_at = _latest_datetime(
                current.close_time,
                btc_bars[-1].close_time,
                oi_state.available_at if oi_state is not None else None,
                _latest_funding_available_at(funding, as_of),
            )
            candidate = MicroSignalCandidate(
                strategy="C-M3",
                direction="LONG",
                signal_time=current.close_time,
                available_at=available_at,
                stop=stop,
                target=target,
                reason=(
                    f"C-M3: confirmed equal lows near {setup.equal_low:.8g} swept and reclaimed; "
                    + ("bullish taker-CVD divergence; " if settings.enable_cvd_gate else "")
                    + ("OI washout" if settings.enable_oi_gate else "liquidity reversal")
                ),
                risk_fraction=settings.risk_fraction,
                minimum_rr=settings.minimum_rr,
                max_hold_bars=settings.max_hold_bars,
            )
    return MicroRuleEvaluation("C-M3", as_of, tuple(conditions), metrics, candidate)


def _condition(name: str, result: bool | None, detail: str, *, required: bool = True) -> RuleCondition:
    status: ConditionStatus = "insufficient" if result is None else "passed" if result else "not_met"
    return RuleCondition(name, status, detail, required)


def _all_passed(conditions: Sequence[RuleCondition]) -> bool:
    return all(condition.status == "passed" for condition in conditions if condition.required)


def _causal_bars(candles: Sequence[Kline], as_of: datetime, interval: str) -> list[Kline]:
    cutoff = _timestamp(as_of)
    causal = [
        candle
        for candle in candles
        if candle.is_closed and candle.interval == interval and _timestamp(candle.close_time) <= cutoff
    ]
    return sorted(causal, key=lambda candle: (_timestamp(candle.close_time), _timestamp(candle.open_time)))


def _causal_oi_points(points: Sequence[OpenInterestPoint], as_of: datetime) -> list[OpenInterestPoint]:
    cutoff = _timestamp(as_of)
    causal = [
        point
        for point in points
        if _timestamp(point.observed_at) <= cutoff
        and _timestamp(point.available_at) <= cutoff
        and math.isfinite(point.contracts)
    ]
    return sorted(causal, key=lambda point: (_timestamp(point.observed_at), _timestamp(point.available_at)))


def _causal_funding_points(points: Sequence[FundingPoint], as_of: datetime) -> list[FundingPoint]:
    cutoff = _timestamp(as_of)
    causal = [
        point
        for point in points
        if _timestamp(point.observed_at) <= cutoff
        and _timestamp(point.available_at) <= cutoff
        and math.isfinite(point.rate)
    ]
    return sorted(causal, key=lambda point: (_timestamp(point.observed_at), _timestamp(point.available_at)))


def _normalized_taker_flow_series(candles: Sequence[Kline], lookback: int) -> list[float | None]:
    result: list[float | None] = []
    for end in range(1, len(candles) + 1):
        state = calculate_normalized_taker_flow(candles[:end], lookback)
        result.append(state.normalized_cvd if state is not None else None)
    return result


def _normalized_taker_delta(candle: Kline) -> float | None:
    volume = float(candle.volume)
    if volume <= 0 or candle.taker_buy_volume is None:
        return None
    taker_buy = float(candle.taker_buy_volume)
    if taker_buy < 0 or taker_buy > volume:
        return None
    return (2.0 * taker_buy - volume) / volume


def _find_cm2_setup(
    bars_5m: Sequence[Kline],
    bars_15m: Sequence[Kline],
    atr: float,
    config: CryptoMicroRuleConfig,
) -> _Cm2Setup | None:
    if len(bars_5m) < 2 or atr <= 0:
        return None
    current_index = len(bars_5m) - 1
    start = max(1, current_index - config.setup_search_bars)
    tolerance = atr * config.retest_tolerance_atr
    for loss_index in range(current_index - 1, start - 1, -1):
        profile_history = [
            bar
            for bar in bars_15m
            if _timestamp(bar.close_time) <= _timestamp(bars_5m[loss_index].open_time)
        ]
        node = _volume_node(profile_history, config)
        if node is None:
            continue
        previous_close = float(bars_5m[loss_index - 1].close)
        loss_close = float(bars_5m[loss_index].close)
        current = bars_5m[current_index]
        crossed_below = previous_close >= node.level and loss_close < node.level
        retested = float(current.high) >= node.low - tolerance and float(current.close) < node.level
        if crossed_below and retested:
            return _Cm2Setup(loss_index, node)
    return None


def _volume_node(candles: Sequence[Kline], config: CryptoMicroRuleConfig) -> _VolumeNode | None:
    if len(candles) < config.profile_min_bars:
        return None
    window = list(candles[-config.profile_lookback_15m :])
    low = min(float(candle.low) for candle in window)
    high = max(float(candle.high) for candle in window)
    if not high > low:
        return None
    step = (high - low) / config.profile_bins
    volumes = [0.0] * config.profile_bins
    for candle in window:
        typical = (float(candle.high) + float(candle.low) + float(candle.close)) / 3.0
        bucket = min(int((typical - low) / step), config.profile_bins - 1)
        volumes[max(bucket, 0)] += max(float(candle.volume), 0.0)
    index = max(range(config.profile_bins), key=volumes.__getitem__)
    node_low = low + index * step
    node_high = node_low + step
    return _VolumeNode(node_low, node_high, (node_low + node_high) / 2.0, window[-1].close_time)


def _find_cm3_setup(
    bars: Sequence[Kline],
    atr: float,
    config: CryptoMicroRuleConfig,
) -> _Cm3Setup | None:
    if len(bars) < 2 or atr <= 0:
        return None
    current_index = len(bars) - 1
    start = max(0, current_index - config.setup_search_bars)
    all_lows = _confirmed_pivots(bars, "LOW", config)
    for sweep_index in range(current_index, start - 1, -1):
        available_lows = [pivot for pivot in all_lows if pivot.confirmed_index < sweep_index]
        pair = _latest_equal_low_pair(available_lows, atr * config.equal_low_tolerance_atr)
        if pair is None:
            continue
        equal_low = (pair[0].price + pair[1].price) / 2.0
        sweep_bar = bars[sweep_index]
        sweep_low = float(sweep_bar.low)
        swept = sweep_low < equal_low - atr * config.minimum_sweep_atr
        reclaim = equal_low + atr * config.reclaim_buffer_atr
        first_reclaim = float(bars[current_index].close) > reclaim and all(
            float(bar.close) <= reclaim for bar in bars[sweep_index:current_index]
        )
        not_invalidated = all(float(bar.close) > sweep_low for bar in bars[sweep_index + 1 : current_index + 1])
        if swept and first_reclaim and not_invalidated:
            return _Cm3Setup(sweep_index, equal_low, sweep_low, atr)
    return None


def _confirmed_pivots(
    bars: Sequence[Kline],
    side: Literal["HIGH", "LOW"],
    config: CryptoMicroRuleConfig,
) -> list[_ConfirmedPivot]:
    values = [float(bar.high if side == "HIGH" else bar.low) for bar in bars]
    pivots: list[_ConfirmedPivot] = []
    for index in range(config.pivot_left, len(values) - config.pivot_right):
        window = values[index - config.pivot_left : index + config.pivot_right + 1]
        value = values[index]
        extreme = max(window) if side == "HIGH" else min(window)
        if value == extreme and window.count(value) == 1:
            pivots.append(_ConfirmedPivot(index, index + config.pivot_right, value))
    return pivots


def _latest_equal_low_pair(
    pivots: Sequence[_ConfirmedPivot],
    tolerance: float,
) -> tuple[_ConfirmedPivot, _ConfirmedPivot] | None:
    for right_index in range(len(pivots) - 1, 0, -1):
        right = pivots[right_index]
        for left in reversed(pivots[:right_index]):
            if abs(right.price - left.price) <= tolerance:
                return left, right
    return None


def _confirmed_structure_target(
    bars: Sequence[Kline],
    direction: Direction,
    config: CryptoMicroRuleConfig,
) -> tuple[float | None, RuleCondition]:
    if not bars:
        return None, _condition("confirmed_structure_target", None, "no closed 5m bars")
    side: Literal["HIGH", "LOW"] = "HIGH" if direction == "LONG" else "LOW"
    pivots = _confirmed_pivots(bars, side, config)
    if not pivots:
        return None, _condition("confirmed_structure_target", None, f"no confirmed 5m swing {side.lower()}")
    close = float(bars[-1].close)
    directional = [pivot.price for pivot in pivots if pivot.price > close] if direction == "LONG" else [pivot.price for pivot in pivots if pivot.price < close]
    if not directional:
        return None, _condition("confirmed_structure_target", False, "confirmed swings exist, but none are beyond current price")
    target = min(directional) if direction == "LONG" else max(directional)
    return target, _condition("confirmed_structure_target", True, "target is the nearest confirmed 5m swing")


def _btc_opposition(
    bars: Sequence[Kline],
    direction: Direction,
    config: CryptoMicroRuleConfig,
) -> bool | None:
    minimum = max(config.flow_lookback, config.pivot_left + config.pivot_right + 2)
    if len(bars) < minimum:
        return None
    structure = detect_structure(
        list(bars),
        lookback=config.structure_lookback,
        pivot_left=config.pivot_left,
        pivot_right=config.pivot_right,
    )
    flow = calculate_normalized_taker_flow(bars, config.flow_lookback)
    if (
        flow is None
        or structure.last_swing_high is None
        or structure.last_swing_low is None
        or not structure.swing_high_confirmed
        or not structure.swing_low_confirmed
    ):
        return None
    close = float(bars[-1].close)
    strong_bullish = structure.trend == "UP" and close > structure.last_swing_high and flow.normalized_delta > 0
    strong_bearish = structure.trend == "DOWN" and close < structure.last_swing_low and flow.normalized_delta < 0
    return strong_bullish if direction == "SHORT" else strong_bearish


def _oi_washout_near_sweep(
    points: Sequence[OpenInterestPoint],
    as_of: datetime,
    bars: Sequence[Kline],
    setup: _Cm3Setup | None,
    config: CryptoMicroRuleConfig,
) -> OpenInterestWashout | None:
    if setup is None:
        return None
    causal = _causal_oi_points(points, as_of)
    cutoff = _timestamp(bars[setup.sweep_index].open_time)
    candidates: list[OpenInterestWashout] = []
    for index, point in enumerate(causal):
        if _timestamp(point.observed_at) < cutoff:
            continue
        state = calculate_oi_washout(
            causal[: index + 1],
            point.available_at,
            lookback=config.oi_lookback,
            minimum_history=config.oi_min_history,
            lower_quantile=config.lower_quantile,
        )
        if state is not None:
            candidates.append(state)
    return min(candidates, key=lambda state: state.robust_z) if candidates else None


def _latest_funding_rate(points: Sequence[FundingPoint], as_of: datetime) -> float | None:
    causal = _causal_funding_points(points, as_of)
    return causal[-1].rate if causal else None


def _latest_funding_available_at(points: Sequence[FundingPoint], as_of: datetime) -> datetime | None:
    causal = _causal_funding_points(points, as_of)
    return causal[-1].available_at if causal else None


def _lower_wick_ratio(candle: Kline) -> float:
    full_range = max(float(candle.high - candle.low), 1e-12)
    lower_wick = float(min(candle.open, candle.close) - candle.low)
    return lower_wick / full_range


def _is_bearish_displacement(bars: Sequence[Kline], index: int) -> bool:
    history = bars[max(0, index - 20) : index]
    if len(history) < 5:
        return False
    candle = bars[index]
    average_range = sum(float(bar.high - bar.low) for bar in history) / len(history)
    average_volume = sum(float(bar.volume) for bar in history) / len(history)
    full_range = max(float(candle.high - candle.low), 1e-12)
    body_ratio = abs(float(candle.close - candle.open)) / full_range
    close_position = float(candle.close - candle.low) / full_range
    return bool(
        candle.close < candle.open
        and full_range >= average_range * 1.15
        and float(candle.volume) >= average_volume * 1.05
        and body_ratio >= 0.55
        and close_position <= 0.30
    )


def _latest_datetime(*values: datetime | None) -> datetime:
    available = [value for value in values if value is not None]
    if not available:
        raise ValueError("at least one datetime is required")
    return max(available, key=_timestamp)


def _timestamp(value: datetime) -> float:
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.timestamp()


def _quantile(values: Sequence[float], probability: float) -> float:
    if not values:
        raise ValueError("at least one value is required")
    ordered = sorted(values)
    position = (len(ordered) - 1) * probability
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight
