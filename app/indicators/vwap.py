from app.market.models import Kline


def calculate_vwap(candles: list[Kline]) -> float:
    numerator = 0.0
    denominator = 0.0
    for candle in candles:
        typical = (float(candle.high) + float(candle.low) + float(candle.close)) / 3
        volume = float(candle.volume)
        numerator += typical * volume
        denominator += volume
    return numerator / denominator if denominator else 0.0
