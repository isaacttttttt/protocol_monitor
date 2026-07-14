from app.indicators.cvd import calculate_cvd_proxy


def test_cvd_proxy(kline_factory):
    candles = [
        kline_factory("ETHUSDT", "15m", 10, 11, 9, 11, 100),
        kline_factory("ETHUSDT", "15m", 11, 12, 10, 10, 50),
        kline_factory("ETHUSDT", "15m", 10, 11, 9, 12, 200),
    ]
    result = calculate_cvd_proxy(candles)
    assert result.cvd == 250
    assert result.delta == 200
    assert result.makes_new_high is True
    assert result.trend == "UP"


def test_cvd_uses_binance_taker_volume_when_available(kline_factory):
    candle = kline_factory("ETHUSDT", "15m", 11, 12, 9, 10, 100)
    candle = type(candle)(**{**candle.__dict__, "taker_buy_volume": 70})

    result = calculate_cvd_proxy([candle])

    assert result.delta == 40
    assert result.trend == "UP"
    assert result.source == "TAKER_DELTA"
