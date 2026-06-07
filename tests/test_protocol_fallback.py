import pytest

from app.review.protocol_analysis import (
    ProtocolAnalysis,
    _aggregate_candles,
    _brief_error,
    _equity_final_instruction,
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


def test_brief_error_compacts_common_network_errors():
    assert _brief_error(RuntimeError("HTTP 451: restricted")) == "HTTP 451"
    assert _brief_error(RuntimeError("<urlopen error [WinError 10061] 由于目标计算机积极拒绝，无法连接。>")) == "connection refused"


def test_yahoo_chart_missing_timestamp_has_clear_error(monkeypatch):
    def fake_get_json(url, params):
        return {"chart": {"result": [{}]}}

    monkeypatch.setattr("app.review.protocol_analysis._get_json", fake_get_json)

    with pytest.raises(ValueError, match="no Yahoo chart data for INQU"):
        _yahoo_chart("INQU", "1d", "1y")


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
