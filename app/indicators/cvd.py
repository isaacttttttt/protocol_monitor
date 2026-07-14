from dataclasses import dataclass
from typing import Literal

from app.market.models import Kline


@dataclass(frozen=True)
class CvdResult:
    cvd: float
    delta: float
    makes_new_high: bool
    makes_new_low: bool
    trend: Literal["UP", "DOWN", "FLAT"]
    source: Literal["TAKER_DELTA", "MIXED", "OHLCV_PROXY"] = "OHLCV_PROXY"


def calculate_cvd_proxy(candles: list[Kline], lookback: int = 20) -> CvdResult:
    if not candles:
        return CvdResult(0.0, 0.0, False, False, "FLAT")
    deltas: list[float] = []
    sources: list[str] = []
    for candle in candles:
        if candle.taker_buy_volume is not None and candle.volume > 0:
            taker_buy = min(max(float(candle.taker_buy_volume), 0.0), float(candle.volume))
            deltas.append(taker_buy * 2 - float(candle.volume))
            sources.append("TAKER_DELTA")
        elif candle.close > candle.open:
            deltas.append(float(candle.volume))
            sources.append("OHLCV_PROXY")
        elif candle.close < candle.open:
            deltas.append(-float(candle.volume))
            sources.append("OHLCV_PROXY")
        else:
            deltas.append(0.0)
            sources.append("OHLCV_PROXY")
    rolling = deltas[-lookback:]
    rolling_sources = sources[-lookback:]
    cvd_values: list[float] = []
    total = 0.0
    for delta in rolling:
        total += delta
        cvd_values.append(total)
    current = cvd_values[-1]
    previous_values = cvd_values[:-1]
    trend = "UP" if deltas[-1] > 0 else "DOWN" if deltas[-1] < 0 else "FLAT"
    taker_count = rolling_sources.count("TAKER_DELTA")
    source = "TAKER_DELTA" if taker_count == len(rolling_sources) else "MIXED" if taker_count else "OHLCV_PROXY"
    return CvdResult(
        cvd=current,
        delta=deltas[-1],
        makes_new_high=bool(previous_values) and current > max(previous_values),
        makes_new_low=bool(previous_values) and current < min(previous_values),
        trend=trend,
        source=source,
    )
