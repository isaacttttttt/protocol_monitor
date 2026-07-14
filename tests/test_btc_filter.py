from app.indicators.cvd import CvdResult
from app.indicators.macd import MacdResult
from app.indicators.structure import StructureState
from app.risk.btc_filter import evaluate_btc_filter


def test_btc_strong_bullish(kline_factory):
    candles = [kline_factory("BTCUSDT", "15m", 100, 121, 99, 120)]
    result = evaluate_btc_filter(
        candles,
        MacdResult(2, 1, 1),
        CvdResult(10, 5, True, False, "UP"),
        StructureState(
            "RANGE", False, False, False, False, 119, 90, True, True
        ),
    )
    assert result.strong_bullish is True


def test_btc_strong_bearish(kline_factory):
    candles = [kline_factory("BTCUSDT", "15m", 100, 101, 79, 80)]
    result = evaluate_btc_filter(
        candles,
        MacdResult(-2, -1, -1),
        CvdResult(-10, -5, False, True, "DOWN"),
        StructureState(
            "RANGE", False, False, False, False, 120, 81, True, True
        ),
    )
    assert result.strong_bearish is True


def test_btc_filter_does_not_trade_on_unconfirmed_display_extrema(kline_factory):
    candles = [kline_factory("BTCUSDT", "15m", 100, 121, 79, 120)]

    result = evaluate_btc_filter(
        candles,
        MacdResult(2, 1, 1),
        CvdResult(10, 5, True, False, "UP"),
        StructureState("RANGE", False, False, False, False, 119, 90),
    )

    assert result.neutral is True
    assert result.reason == "insufficient BTC context"
