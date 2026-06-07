from app.indicators.atr import calculate_atr


def test_atr(kline_factory):
    candles = [
        kline_factory("ETHUSDT", "1m", 10, 12, 9, 11),
        kline_factory("ETHUSDT", "1m", 11, 15, 10, 14),
        kline_factory("ETHUSDT", "1m", 14, 16, 13, 15),
    ]
    assert calculate_atr(candles, period=2) == 4
