from app.connectors.binance_futures import BinanceFuturesConnector
from app.market.data_bus import MarketDataBus


def test_binance_combined_stream_url_from_ws_base():
    connector = BinanceFuturesConnector(
        "wss://fstream.binance.com/ws",
        [{"exchange": "BINANCE", "symbol": "ETHUSDT", "enabled": True, "intervals": ["1m", "5m"]}],
        MarketDataBus(),
    )
    assert connector._stream_url() == "wss://fstream.binance.com/stream?streams=ethusdt@kline_1m/ethusdt@kline_5m"
