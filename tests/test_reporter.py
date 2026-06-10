import pytest

from app.config.settings import Settings
from app.notifications.base import NotificationResult
from app.review.llm_protocol_report import LlmProtocolReportPart
from app.review.reporter import PeriodicReporter, format_periodic_report


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


@pytest.mark.asyncio
async def test_reporter_send_pushes_stream_parts(monkeypatch):
    sent = []
    saved_notifications = []

    class DummyKlineRepository:
        async def count_klines(self):
            return 42

    class DummySignalRepository:
        async def get_recent_signals(self, hours):
            return []

        async def get_strategy_states(self):
            return {}

        async def save_notification(self, values):
            saved_notifications.append(values)

    class DummyNotifier:
        async def send(self, message):
            sent.append(message)
            return NotificationResult(True)

    async def fake_stream(*args, **kwargs):
        yield LlmProtocolReportPart("ETH title", "## 标的：ETHUSDT\n交易机会：是", "ETHUSDT", "crypto", True)
        yield LlmProtocolReportPart("BTC title", "## 标的：BTCUSDT\n交易机会：否", "BTCUSDT", "crypto", False)

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", fake_stream)
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {"enabled": True, "channels": {"feishu": {"enabled": True}}},
            "report": {"include_protocol_analysis": True, "use_deepseek_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {"feishu": DummyNotifier()}

    await reporter.send(1)

    assert [message.title for message in sent] == ["ETH title", "BTC title"]
    assert [message.level for message in sent] == ["OPPORTUNITY", "REPORT"]
    assert [item["title"] for item in saved_notifications] == ["ETH title", "BTC title"]
