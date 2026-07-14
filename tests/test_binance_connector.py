from app.connectors.binance_futures import BinanceFuturesConnector
from app.market.data_bus import MarketDataBus
from app.market.models import Kline


def test_binance_combined_stream_url_from_ws_base():
    connector = BinanceFuturesConnector(
        "wss://fstream.binance.com/ws",
        [{"exchange": "BINANCE", "symbol": "ETHUSDT", "enabled": True, "intervals": ["1m", "5m"]}],
        MarketDataBus(),
    )
    assert connector._stream_url() == "wss://fstream.binance.com/stream?streams=ethusdt@kline_1m/ethusdt@kline_5m"


def test_binance_kline_keeps_taker_buy_volume():
    payload = {
        "k": {
            "s": "ETHUSDT",
            "i": "15m",
            "t": 1_700_000_000_000,
            "T": 1_700_000_899_999,
            "o": "100",
            "h": "105",
            "l": "99",
            "c": "103",
            "v": "1000",
            "q": "102000",
            "V": "620",
            "x": True,
        }
    }

    candle = Kline.from_binance_payload(payload)

    assert float(candle.taker_buy_volume) == 620
