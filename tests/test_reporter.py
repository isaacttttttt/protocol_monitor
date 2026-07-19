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
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {"feishu": DummyNotifier()}

    await reporter.send(1)

    assert [message.title for message in sent] == ["ETH title"]
    assert [message.level for message in sent] == ["OPPORTUNITY"]
    assert [item["title"] for item in saved_notifications] == ["ETH title"]


@pytest.mark.asyncio
async def test_reporter_sends_one_summary_when_no_symbol_has_a_trade(monkeypatch):
    sent = []

    class DummyKlineRepository:
        async def count_klines(self):
            return 42

    class DummySignalRepository:
        async def get_recent_signals(self, hours):
            return []

        async def get_strategy_states(self):
            return {}

        async def save_notification(self, values):
            return None

    class DummyNotifier:
        async def send(self, message):
            sent.append(message)
            return NotificationResult(True)

    async def fake_stream(*args, **kwargs):
        for symbol, reason in (("ETHUSDT", "4H与DAY方向冲突"), ("BTCUSDT", "TP1仅有1.1R")):
            yield LlmProtocolReportPart(
                f"{symbol} title",
                f"## 标的：{symbol}\n当前指令：不做",
                symbol,
                "crypto",
                False,
                decision={
                    "status": "NO_TRADE",
                    "summary": "本轮不做",
                    "rejection_reasons": [reason],
                },
            )

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", fake_stream)
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {"enabled": True, "channels": {"feishu": {"enabled": True}}},
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {"feishu": DummyNotifier()}

    await reporter.send(1)

    assert len(sent) == 1
    assert sent[0].title == "SPM 1H 本轮无新高周期机会"
    assert "ETHUSDT：4H与DAY方向冲突" in sent[0].body
    assert "BTCUSDT：TP1仅有1.1R" in sent[0].body


@pytest.mark.asyncio
async def test_reporter_sends_data_error_summary_even_when_trade_exists(monkeypatch):
    sent = []

    class DummyKlineRepository:
        async def count_klines(self):
            return 42

    class DummySignalRepository:
        async def get_recent_signals(self, hours):
            return []

        async def get_strategy_states(self):
            return {}

        async def save_notification(self, values):
            return None

    class DummyNotifier:
        async def send(self, message):
            sent.append(message)
            return NotificationResult(True)

    async def fake_stream(*args, **kwargs):
        yield LlmProtocolReportPart(
            "ETH trade",
            "trade body",
            "ETHUSDT",
            "crypto",
            True,
            decision={"status": "TRADE"},
            opportunity_id="ETH-opportunity",
        )
        yield LlmProtocolReportPart(
            "MU error",
            "error body",
            "MU",
            "equity",
            False,
            decision={
                "status": "DATA_ERROR",
                "summary": "LLM 调用失败",
                "rejection_reasons": ["TimeoutError: request timed out"],
            },
        )

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", fake_stream)
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {"enabled": True, "channels": {"feishu": {"enabled": True}}},
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {"feishu": DummyNotifier()}

    await reporter.send(1)

    assert [message.title for message in sent] == ["ETH trade", "SPM 1H 运行异常"]
    assert "MU：TimeoutError: request timed out" in sent[1].body


@pytest.mark.asyncio
async def test_reporter_uses_one_error_summary_when_no_trade_and_some_symbols_fail(monkeypatch):
    sent = []

    class DummyKlineRepository:
        async def count_klines(self):
            return 42

    class DummySignalRepository:
        async def get_recent_signals(self, hours):
            return []

        async def get_strategy_states(self):
            return {}

        async def save_notification(self, values):
            return None

    class DummyNotifier:
        async def send(self, message):
            sent.append(message)
            return NotificationResult(True)

    async def fake_stream(*args, **kwargs):
        yield LlmProtocolReportPart(
            "ETH no trade",
            "no trade body",
            "ETHUSDT",
            "crypto",
            False,
            decision={"status": "NO_TRADE", "rejection_reasons": ["RR 不足"]},
        )
        yield LlmProtocolReportPart(
            "MU error",
            "error body",
            "MU",
            "equity",
            False,
            decision={"status": "DATA_ERROR", "rejection_reasons": ["行情不可用"]},
        )

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", fake_stream)
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {"enabled": True, "channels": {"feishu": {"enabled": True}}},
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {"feishu": DummyNotifier()}

    await reporter.send(1)

    assert len(sent) == 1
    assert sent[0].title == "SPM 1H 运行异常"
    assert "MU：行情不可用" in sent[0].body
    assert "其余已完成分析：1 个" in sent[0].body


@pytest.mark.asyncio
async def test_reporter_isolates_notifier_exception_and_persists_failure(monkeypatch):
    attempts = []
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

    class RaisingNotifier:
        async def send(self, message):
            attempts.append(message.channel)
            raise TimeoutError("webhook timeout")

    class SuccessfulNotifier:
        async def send(self, message):
            attempts.append(message.channel)
            return NotificationResult(True)

    async def fake_stream(*args, **kwargs):
        yield LlmProtocolReportPart(
            "ETH trade",
            "trade body",
            "ETHUSDT",
            "crypto",
            True,
            decision={"status": "TRADE"},
            opportunity_id="ETH-opportunity",
        )

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", fake_stream)
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {
                "enabled": True,
                "channels": {
                    "feishu": {"enabled": True},
                    "telegram": {"enabled": True},
                },
            },
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {
        "feishu": RaisingNotifier(),
        "telegram": SuccessfulNotifier(),
    }

    await reporter.send(1)

    assert attempts == ["feishu", "telegram"]
    assert [item["status"] for item in saved_notifications] == ["FAILED", "SENT"]
    assert saved_notifications[0]["error_message"] == "TimeoutError: webhook timeout"


@pytest.mark.asyncio
async def test_reporter_continues_when_notification_persistence_fails(monkeypatch):
    attempts = []

    class DummyKlineRepository:
        async def count_klines(self):
            return 42

    class FlakySignalRepository:
        def __init__(self):
            self.save_calls = 0

        async def get_recent_signals(self, hours):
            return []

        async def get_strategy_states(self):
            return {}

        async def save_notification(self, values):
            self.save_calls += 1
            if self.save_calls == 1:
                raise RuntimeError("database unavailable")

    class DummyNotifier:
        async def send(self, message):
            attempts.append(message.channel)
            return NotificationResult(True)

    async def fake_stream(*args, **kwargs):
        yield LlmProtocolReportPart(
            "ETH trade",
            "trade body",
            "ETHUSDT",
            "crypto",
            True,
            decision={"status": "TRADE"},
            opportunity_id="ETH-opportunity",
        )

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", fake_stream)
    signal_repository = FlakySignalRepository()
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {
                "enabled": True,
                "channels": {
                    "feishu": {"enabled": True},
                    "telegram": {"enabled": True},
                },
            },
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        signal_repository,
        None,
    )
    reporter.notifiers = {
        "feishu": DummyNotifier(),
        "telegram": DummyNotifier(),
    }

    await reporter.send(1)

    assert attempts == ["feishu", "telegram"]
    assert signal_repository.save_calls == 2


@pytest.mark.asyncio
async def test_reporter_notifies_when_stream_aborts(monkeypatch):
    sent = []

    class DummyKlineRepository:
        async def count_klines(self):
            return 42

    class DummySignalRepository:
        async def get_recent_signals(self, hours):
            return []

        async def get_strategy_states(self):
            return {}

        async def save_notification(self, values):
            return None

    class DummyNotifier:
        async def send(self, message):
            sent.append(message)
            return NotificationResult(True)

    async def broken_stream(*args, **kwargs):
        if False:
            yield None
        raise RuntimeError("snapshot archive unavailable")

    monkeypatch.setattr("app.review.reporter.stream_llm_protocol_report_parts", broken_stream)
    reporter = PeriodicReporter(
        Settings(feishu_webhook_url="https://example.test/webhook"),
        {
            "notification": {"enabled": True, "channels": {"feishu": {"enabled": True}}},
            "report": {"include_protocol_analysis": True, "use_llm_analysis": True},
        },
        DummyKlineRepository(),
        DummySignalRepository(),
        None,
    )
    reporter.notifiers = {"feishu": DummyNotifier()}

    await reporter.send(1)

    assert len(sent) == 1
    assert sent[0].title == "SPM 1H 运行异常"
    assert "SYSTEM：RuntimeError: snapshot archive unavailable" in sent[0].body
