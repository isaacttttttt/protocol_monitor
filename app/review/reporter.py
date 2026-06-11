from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.config.settings import Settings
from app.notifications.base import NotificationMessage
from app.notifications.feishu import FeishuNotifier, split_report_for_feishu
from app.notifications.telegram import TelegramNotifier
from app.review.llm_protocol_report import LlmProtocolReportPart, build_llm_protocol_report, stream_llm_protocol_report_parts
from app.review.protocol_analysis import ProtocolAnalysis, format_protocol_section
from app.storage.repositories import IndicatorArchiveRepository, KlineRepository, SignalRepository


def format_periodic_report(
    hours: int,
    kline_count: int,
    strategy_count: int,
    recent_signals: list[dict[str, Any]],
    protocol_analyses: list[ProtocolAnalysis] | None = None,
    analysis_report: str | None = None,
) -> tuple[str, str]:
    level_counts: dict[str, int] = {}
    for signal in recent_signals:
        level = str(signal.get("level", "UNKNOWN"))
        level_counts[level] = level_counts.get(level, 0) + 1

    title = f"SPM {hours}H 周期监控报告"
    lines = [
        f"窗口：最近 {hours} 小时",
        f"K线记录：{kline_count}",
        f"策略状态：{strategy_count}",
        f"信号数量：{len(recent_signals)}",
        f"信号分级：{level_counts or '无'}",
    ]
    if analysis_report is not None:
        lines.extend(["", analysis_report])
    else:
        analyses = [] if protocol_analyses is None else protocol_analyses
        lines.extend(["", *format_protocol_section(analyses)])

    if recent_signals:
        lines.append("")
        lines.append("最近信号：")
        for signal in recent_signals[:10]:
            price = _display(signal.get("trigger_price"))
            created_at = signal.get("created_at")
            lines.append(
                f"- {signal.get('created_at', created_at)} {signal.get('symbol')} {signal.get('level')} "
                f"{signal.get('strategy_name')} price={price} reason={signal.get('trigger_reason')}"
            )
    else:
        lines.append("")
        lines.append("最近没有新信号；继续监控。")
    return title, "\n".join(lines)


def _display(value: Any) -> Any:
    if isinstance(value, Decimal):
        return float(value)
    return value


class PeriodicReporter:
    def __init__(
        self,
        settings: Settings,
        system_config: dict[str, Any],
        kline_repository: KlineRepository,
        signal_repository: SignalRepository,
        indicator_archive_repository: IndicatorArchiveRepository | None = None,
    ) -> None:
        self.settings = settings
        self.system_config = system_config
        self.kline_repository = kline_repository
        self.signal_repository = signal_repository
        self.indicator_archive_repository = indicator_archive_repository
        self.notifiers = {
            "telegram": TelegramNotifier(settings),
            "feishu": FeishuNotifier(settings),
        }

    async def build(self, hours: int) -> tuple[str, str]:
        recent_signals, strategy_count, kline_count = await self._report_context(hours)
        report_config = self.system_config.get("report", {})
        if report_config.get("include_protocol_analysis", True) and report_config.get("use_llm_analysis", True):
            return await build_llm_protocol_report(
                self.settings,
                self.system_config,
                hours,
                kline_count,
                strategy_count,
                recent_signals,
                self.indicator_archive_repository,
            )
        analyses = []
        if report_config.get("include_protocol_analysis", True):
            from app.review.protocol_analysis import build_protocol_analyses

            analyses = build_protocol_analyses(
                crypto_symbols=report_config.get("crypto_symbols"),
                equity_symbols=report_config.get("equity_symbols"),
            )
        return format_periodic_report(hours, kline_count, strategy_count, recent_signals, analyses)

    async def send(self, hours: int) -> None:
        report_config = self.system_config.get("report", {})
        if report_config.get("include_protocol_analysis", True) and report_config.get("use_llm_analysis", True):
            await self._send_llm_stream(hours)
            return

        title, body = await self.build(hours)
        print(f"{title}\n\n{body}")
        notif_cfg = self.system_config.get("notification", {})
        if not notif_cfg.get("enabled", True):
            logger.info("notification disabled; report printed only")
            return
        signal_id = f"SPM-REPORT-{_utcnow().strftime('%Y%m%d%H%M%S')}"
        for channel, config in notif_cfg.get("channels", {}).items():
            if not config.get("enabled", False):
                continue
            messages = self._notification_messages(channel, signal_id, title, body)
            for index, message in enumerate(messages, start=1):
                result = await self.notifiers[channel].send(message)
                await self.signal_repository.save_notification(
                    {
                        "signal_id": message.signal_id,
                        "channel": channel,
                        "target": channel,
                        "title": message.title,
                        "body": message.body,
                        "status": "SENT" if result.ok else "FAILED",
                        "error_message": result.error,
                        "sent_at": _utcnow() if result.ok else None,
                    }
                )
                if not result.ok:
                    logger.warning("{} report notification failed part {}/{}: {}", channel, index, len(messages), result.error)

    async def _report_context(self, hours: int) -> tuple[list[dict[str, Any]], int, int]:
        recent_signals = await self.signal_repository.get_recent_signals(hours)
        strategy_states = await self.signal_repository.get_strategy_states()
        kline_count = await self.kline_repository.count_klines()
        return recent_signals, len(strategy_states), kline_count

    async def _send_llm_stream(self, hours: int) -> None:
        recent_signals, strategy_count, kline_count = await self._report_context(hours)
        notif_cfg = self.system_config.get("notification", {})
        notifications_enabled = notif_cfg.get("enabled", True)
        if not notifications_enabled:
            logger.info("notification disabled; streaming report printed only")

        signal_id = f"SPM-REPORT-{_utcnow().strftime('%Y%m%d%H%M%S')}"
        part_count = 0
        async for part in stream_llm_protocol_report_parts(
            self.settings,
            self.system_config,
            hours,
            kline_count,
            strategy_count,
            recent_signals,
            self.indicator_archive_repository,
        ):
            part_count += 1
            print(f"{part.title}\n\n{part.body}\n")
            if notifications_enabled:
                await self._send_report_part(part, notif_cfg, signal_id, part_count)

        if part_count == 0:
            logger.warning("LLM streaming report produced no parts")

    async def _send_report_part(
        self,
        part: LlmProtocolReportPart,
        notif_cfg: dict[str, Any],
        signal_id: str,
        index: int,
    ) -> None:
        level = "OPPORTUNITY" if part.has_trade_opportunity else "REPORT"
        part_signal_id = f"{signal_id}-{index:02d}-{part.symbol}"
        for channel, config in notif_cfg.get("channels", {}).items():
            if not config.get("enabled", False):
                continue
            message = NotificationMessage(
                channel=channel,
                title=part.title,
                body=part.body,
                level=level,
                signal_id=part_signal_id,
                symbol=part.symbol,
                created_at=_utcnow(),
            )
            result = await self.notifiers[channel].send(message)
            await self.signal_repository.save_notification(
                {
                    "signal_id": message.signal_id,
                    "channel": channel,
                    "target": channel,
                    "title": message.title,
                    "body": message.body,
                    "status": "SENT" if result.ok else "FAILED",
                    "error_message": result.error,
                    "sent_at": _utcnow() if result.ok else None,
                }
            )
            if not result.ok:
                logger.warning("{} streaming report notification failed for {}: {}", channel, part.symbol, result.error)

    def _notification_messages(self, channel: str, signal_id: str, title: str, body: str) -> list[NotificationMessage]:
        if channel == "feishu":
            parts = split_report_for_feishu(title, body, self.settings.feishu_keyword)
        else:
            parts = [(title, body)]
        return [
            NotificationMessage(
                channel=channel,
                title=part_title,
                body=part_body,
                level="REPORT",
                signal_id=f"{signal_id}-{index:02d}" if len(parts) > 1 else signal_id,
                symbol="SPM",
                created_at=_utcnow(),
            )
            for index, (part_title, part_body) in enumerate(parts, start=1)
        ]


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
