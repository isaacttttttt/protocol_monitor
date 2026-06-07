from app.review.reporter import format_periodic_report


def test_format_periodic_report_without_signals():
    title, body = format_periodic_report(2, 100, 3, [], protocol_analyses=[])
    assert title == "SPM 2H 周期监控报告"
    assert "信号数量：0" in body
    assert "继续监控" in body


def test_format_periodic_report_with_signal_counts():
    _, body = format_periodic_report(
        8,
        100,
        3,
        [
            {"level": "L2", "symbol": "ETHUSDT", "strategy_name": "A", "trigger_price": 1600, "trigger_reason": "watch"},
            {"level": "L3", "symbol": "ETHUSDT", "strategy_name": "B", "trigger_price": 1580, "trigger_reason": "trigger"},
            {"level": "L3", "symbol": "BTCUSDT", "strategy_name": "C", "trigger_price": 60000, "trigger_reason": "trigger"},
        ],
        protocol_analyses=[],
    )
    assert "'L2': 1" in body
    assert "'L3': 2" in body
