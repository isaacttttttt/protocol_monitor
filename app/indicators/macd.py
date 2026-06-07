from dataclasses import dataclass

from app.market.models import Kline


@dataclass(frozen=True)
class MacdResult:
    macd: float
    signal: float
    histogram: float


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append((value * alpha) + (result[-1] * (1 - alpha)))
    return result


def calculate_macd(candles: list[Kline], fast: int = 12, slow: int = 26, signal: int = 9) -> MacdResult:
    closes = [float(c.close) for c in candles]
    if len(closes) < slow:
        return MacdResult(0.0, 0.0, 0.0)
    fast_ema = _ema(closes, fast)
    slow_ema = _ema(closes, slow)
    macd_series = [fast_value - slow_value for fast_value, slow_value in zip(fast_ema, slow_ema)]
    signal_series = _ema(macd_series, signal)
    macd_value = macd_series[-1]
    signal_value = signal_series[-1]
    return MacdResult(macd_value, signal_value, macd_value - signal_value)
