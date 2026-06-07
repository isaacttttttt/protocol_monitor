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


def calculate_cvd_proxy(candles: list[Kline], lookback: int = 20) -> CvdResult:
    if not candles:
        return CvdResult(0.0, 0.0, False, False, "FLAT")
    deltas: list[float] = []
    for candle in candles:
        if candle.close > candle.open:
            deltas.append(float(candle.volume))
        elif candle.close < candle.open:
            deltas.append(-float(candle.volume))
        else:
            deltas.append(0.0)
    rolling = deltas[-lookback:]
    cvd_values: list[float] = []
    total = 0.0
    for delta in rolling:
        total += delta
        cvd_values.append(total)
    current = cvd_values[-1]
    previous_values = cvd_values[:-1]
    trend = "UP" if deltas[-1] > 0 else "DOWN" if deltas[-1] < 0 else "FLAT"
    return CvdResult(
        cvd=current,
        delta=deltas[-1],
        makes_new_high=bool(previous_values) and current > max(previous_values),
        makes_new_low=bool(previous_values) and current < min(previous_values),
        trend=trend,
    )
