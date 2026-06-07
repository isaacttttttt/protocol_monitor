from app.review.protocol_analysis import _aggregate_candles, _brief_error, _okx_symbol, _yahoo_crypto_symbol


def test_crypto_symbol_mappings():
    assert _okx_symbol("ETHUSDT") == "ETH-USDT-SWAP"
    assert _yahoo_crypto_symbol("BTCUSDT") == "BTC-USD"


def test_aggregate_candles_groups_ohlcv():
    candles = [
        {"time": "t1", "open": 10, "high": 12, "low": 9, "close": 11, "volume": 100},
        {"time": "t2", "open": 11, "high": 13, "low": 10, "close": 12, "volume": 200},
        {"time": "t3", "open": 12, "high": 14, "low": 11, "close": 13, "volume": 300},
        {"time": "t4", "open": 13, "high": 15, "low": 12, "close": 14, "volume": 400},
    ]

    assert _aggregate_candles(candles, 4) == [
        {"time": "t4", "open": 10, "high": 15, "low": 9, "close": 14, "volume": 1000}
    ]


def test_brief_error_compacts_common_network_errors():
    assert _brief_error(RuntimeError("HTTP 451: restricted")) == "HTTP 451"
    assert _brief_error(RuntimeError("<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>")) == "connection refused"
