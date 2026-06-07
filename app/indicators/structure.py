from dataclasses import dataclass
from typing import Literal

from app.market.models import Kline


@dataclass(frozen=True)
class StructureState:
    trend: Literal["UP", "DOWN", "RANGE"]
    bos_up: bool
    bos_down: bool
    choch_up: bool
    choch_down: bool
    last_swing_high: float | None
    last_swing_low: float | None


def detect_structure(candles: list[Kline], lookback: int = 20) -> StructureState:
    if len(candles) < lookback + 1:
        return StructureState("RANGE", False, False, False, False, None, None)
    window = candles[-lookback - 1 : -1]
    last = candles[-1]
    swing_high = max(float(c.high) for c in window)
    swing_low = min(float(c.low) for c in window)
    close = float(last.close)
    bos_up = close > swing_high
    bos_down = close < swing_low
    if bos_up:
        trend = "UP"
    elif bos_down:
        trend = "DOWN"
    else:
        trend = "RANGE"
    return StructureState(
        trend=trend,
        bos_up=bos_up,
        bos_down=bos_down,
        choch_up=bos_up,
        choch_down=bos_down,
        last_swing_high=swing_high,
        last_swing_low=swing_low,
    )
