from datetime import datetime, timezone

import pytest

from app.review.protocol_analysis import (
    ProtocolAnalysis,
    _aggregate_candles,
    _aggregate_utc_4h_candles,
    _binance_klines,
    _brief_error,
    _closed_yahoo_candles,
    _eth_final_instruction,
    _eth_protocol,
    _equity_final_instruction,
    _format_data_time,
    _load_yahoo_crypto,
    _okx_candles,
    _okx_symbol,
    _yahoo_chart,
    _yahoo_crypto_symbol,
    format_protocol_section,
)


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


def test_yahoo_crypto_4h_aggregation_uses_fixed_utc_buckets():
    candles = [
        _yahoo_row(f"2026-01-01T{hour:02d}:00:00+00:00", 100 + hour)
        for hour in range(1, 9)
    ]

    aggregated = _aggregate_utc_4h_candles(candles)

    assert aggregated == [
        {
            "time": "2026-01-01T07:00:00+00:00",
            "open": 104,
            "high": 107,
            "low": 104,
            "close": 107,
            "volume": 4.0,
        }
    ]


def test_yahoo_crypto_4h_bucket_does_not_move_when_history_start_changes():
    aligned_history = [
        _yahoo_row(f"2026-01-01T{hour:02d}:00:00+00:00", 100 + hour)
        for hour in range(9)
    ]
    shifted_history = aligned_history[1:]

    aligned = _aggregate_utc_4h_candles(aligned_history)
    shifted = _aggregate_utc_4h_candles(shifted_history)

    assert aligned[-1] == shifted[-1]
    assert shifted[-1]["time"] == "2026-01-01T07:00:00+00:00"


def test_brief_error_compacts_common_network_errors():
    assert _brief_error(RuntimeError("HTTP 451: restricted")) == "HTTP 451"
    assert _brief_error(RuntimeError("<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>")) == "connection refused"


def test_yahoo_chart_missing_timestamp_has_clear_error(monkeypatch):
    def fake_get_json(url, params):
        return {"chart": {"result": [{}]}}

    monkeypatch.setattr("app.review.protocol_analysis._get_json", fake_get_json)

    with pytest.raises(ValueError, match="no Yahoo chart data for INQU"):
        _yahoo_chart("INQU", "1d", "1y")


def test_binance_klines_excludes_forming_bar_by_close_time(monkeypatch):
    as_of = datetime(2026, 1, 1, 0, 15, tzinfo=timezone.utc)
    cutoff_ms = int(as_of.timestamp() * 1000)
    closed = _binance_row(0, cutoff_ms - 1, "100")
    forming = _binance_row(cutoff_ms, cutoff_ms + 899_999, "999")
    monkeypatch.setattr("app.review.protocol_analysis._binance_get", lambda path, params: [closed, forming])

    candles = _binance_klines("ETHUSDT", "15m", 2, as_of=as_of)

    assert [candle["close"] for candle in candles] == [100.0]


def test_okx_candles_uses_exchange_confirmation_flag(monkeypatch):
    monkeypatch.setattr(
        "app.review.protocol_analysis._okx_get",
        lambda path, params: {
            "data": [
                ["1704068100000", "100", "101", "99", "100", "10", "0", "0", "1"],
                ["1704069000000", "100", "999", "99", "999", "10", "0", "0", "0"],
            ]
        },
    )

    candles = _okx_candles("ETH-USDT-SWAP", "15m", 2)

    assert [candle["close"] for candle in candles] == [100.0]


def test_yahoo_closed_bar_policy_keeps_current_quote_separate():
    rows = [
        _yahoo_row("2026-01-01T00:00:00+00:00", 100),
        _yahoo_row("2026-01-01T00:15:00+00:00", 999),
    ]
    as_of = datetime(2026, 1, 1, 0, 20, tzinfo=timezone.utc)

    closed = _closed_yahoo_candles(rows, "15m", as_of=as_of)

    assert [candle["close"] for candle in closed] == [100]
    assert rows[-1]["close"] == 999


def test_yahoo_crypto_uses_forming_bar_for_quote_but_not_indicators(monkeypatch):
    closed = _yahoo_row("2025-01-01T00:00:00+00:00", 100)
    forming = _yahoo_row("2099-01-01T00:00:00+00:00", 999)

    def fake_chart(symbol, interval, range_, **kwargs):
        if interval == "15m" and kwargs.get("closed_only") is False:
            return [closed, forming]
        if interval == "60m":
            return [dict(closed, time=f"2025-01-01T0{index}:00:00+00:00") for index in range(4)]
        return [closed]

    monkeypatch.setattr("app.review.protocol_analysis._yahoo_chart", fake_chart)

    data = _load_yahoo_crypto("ETHUSDT")

    assert data.last_price == 999
    assert data.k15[-1]["close"] == 100


def test_yahoo_crypto_high_timeframe_mode_does_not_request_micro_data(monkeypatch):
    calls = []
    hourly = [
        _yahoo_row(f"2025-01-01T0{index}:00:00+00:00", 100 + index)
        for index in range(4)
    ]
    forming = _yahoo_row("2099-01-01T04:00:00+00:00", 999)

    def fake_chart(symbol, interval, range_, **kwargs):
        calls.append(interval)
        if interval == "60m":
            return [*hourly, forming]
        if interval == "1d":
            return [hourly[0]]
        raise AssertionError(f"unexpected micro interval request: {interval}")

    monkeypatch.setattr("app.review.protocol_analysis._yahoo_chart", fake_chart)

    data = _load_yahoo_crypto("ETHUSDT", include_micro=False)

    assert calls == ["60m", "1d"]
    assert data.k5 == []
    assert data.k15 == []
    assert [candle["close"] for candle in data.k1] == [100, 101, 102, 103]
    assert data.k4[-1]["close"] == 103
    assert data.last_price == 999


def _binance_row(open_time: int, close_time: int, close: str) -> list[object]:
    return [open_time, "100", "101", "99", close, "10", close_time, "1000", 1, "6", "600", "0"]


def _yahoo_row(time: str, close: float) -> dict[str, float | str]:
    return {"time": time, "open": close, "high": close, "low": close, "close": close, "volume": 1.0}


def test_format_data_time_uses_local_timezone():
    formatted = _format_data_time("2026-06-05T20:00:00+00:00")

    assert formatted.startswith("2026-06-06 04:00:00 Asia/Shanghai")


def test_protocol_section_includes_final_instruction():
    item = ProtocolAnalysis(
        symbol="CRCL",
        market="US Equity",
        price=80.27,
        change_pct=-1.2,
        current_status="破位风险状态。",
        hit_status="命中：L4。",
        suggestion_1="建议1：等待站回。",
        suggestion_2="建议2：等待反抽失败。",
        key_levels="80 / 83 / 90",
        final_instruction=[
            "当前指令：现价 80.27：禁止直接开仓。",
            "多头预警：81.00-82.00 站回。",
            "空头预警：83.00-84.00 反抽失败。",
            "Macro 预警：90.00-93.00 收回。",
            "一句话结论：Micro 等触发，Macro 先剔除。",
        ],
        evidence=["mock"],
        updated_at="2026-01-01T00:00:00Z",
    )

    output = "\n".join(format_protocol_section([item]))

    assert "最终交易指令：" in output
    assert "当前指令：现价 80.27：禁止直接开仓。" in output
    assert "一句话结论：Micro 等触发，Macro 先剔除。" in output


def test_equity_final_instruction_for_breakdown():
    daily = {
        "atr": 7.5,
        "structure": {"bos_down": True, "bos_up": False, "last_swing_high": 140.0, "last_swing_low": 90.0},
        "cvd": {"delta": -100, "makes_new_low": True},
    }
    hourly = {"structure": {"bos_down": False, "bos_up": False}}
    m15 = {"atr": 1.0, "structure": {"last_swing_high": 81.0, "last_swing_low": 79.3}}
    last = {"high": 82.5, "low": 78.4}

    instruction = _equity_final_instruction("CRCL", 80.27, last, daily, hourly, m15)

    assert instruction[0] == "当前指令：现价 80.27：禁止直接开仓；破位后只等站回确认或反抽失败。"
    assert "多头预警：" in instruction[1]
    assert "空头预警：" in instruction[2]
    assert instruction[-1] == "一句话结论：CRCL 当前不是抄底盘，而是破位后的流动性战场。Micro 等触发，Macro 先剔除。"


def test_eth_instruction_adapts_after_second_target_zone():
    c15 = {
        "atr": 20.0,
        "macd": {"hist": 2.0},
        "cvd": {"delta": -100.0, "makes_new_high": False},
        "structure": {"trend": "RANGE"},
    }
    c4 = {"structure": {"trend": "RANGE"}}
    c1d = {"macd": {"hist": 1.0}}
    k5 = [{"low": 1668.0, "close": 1670.0} for _ in range(6)]

    current_status, hit_status, suggestion_1, suggestion_2, key_levels = _eth_protocol(1692.0, k5, c15, c4, c1d)
    instruction = _eth_final_instruction(1692.0, c15)

    assert "1665-1746 延伸确认段" in current_status
    assert "CVD 未确认" in current_status
    assert "反弹目标区上破" in hit_status
    assert "1746" in suggestion_1
    assert "1645-1665。跌回" not in instruction[1]
    assert "1982.00 / 2044.00" in instruction[1]
    assert "1544 / 1583 / 1605 / 1645-1665 / 1746 / 1982-2044" == key_levels
