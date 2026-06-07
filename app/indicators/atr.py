from app.market.models import Kline


def calculate_atr(candles: list[Kline], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    true_ranges: list[float] = []
    for previous, current in zip(candles, candles[1:]):
        high = float(current.high)
        low = float(current.low)
        prev_close = float(previous.close)
        true_ranges.append(max(high - low, abs(high - prev_close), abs(low - prev_close)))
    window = true_ranges[-period:]
    return sum(window) / len(window) if window else 0.0
