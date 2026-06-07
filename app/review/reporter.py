from datetime import datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.config.settings import Settings
from app.notifications.base import NotificationMessage
from app.notifications.feishu import FeishuNotifier
from app.notifications.telegram import TelegramNotifier
from app.review.protocol_analysis import ProtocolAnalysis, format_protocol_section
from app.storage.repositories import KlineRepository, SignalRepository


def format_periodic_report(
    hours: int,
    kline_count: int,
    strategy_count: int,
    recent_signals: list[dict[str, Any]],
    protocol_analyses: list[ProtocolAnalysis] | None = None,
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
    ) -> None:
        self.settings = settings
        self.system_config = system_config
        self.kline_repository = kline_repository
        self.signal_repository = signal_repository
        self.notifiers = {
            "telegram": TelegramNotifier(settings),
            "feishu": FeishuNotifier(settings),
        }

    async def build(self, hours: int) -> tuple[str, str]:
        from app.review.protocol_analysis import build_protocol_analyses

        recent_signals = await self.signal_repository.get_recent_signals(hours)
        strategy_states = await self.signal_repository.get_strategy_states()
        kline_count = await self.kline_repository.count_klines()
        report_config = self.system_config.get("report", {})
        analyses = []
        if report_config.get("include_protocol_analysis", True):
            analyses = build_protocol_analyses(
                crypto_symbols=report_config.get("crypto_symbols"),
                equity_symbols=report_config.get("equity_symbols"),
            )
        return format_periodic_report(hours, kline_count, len(strategy_states), recent_signals, analyses)

    async def send(self, hours: int) -> None:
        title, body = await self.build(hours)
        print(f"{title}\n\n{body}")
        notif_cfg = self.system_config.get("notification", {})
        if not notif_cfg.get("enabled", True):
            logger.info("notification disabled; report printed only")
            return
        signal_id = f"SPM-REPORT-{datetime.utcnow().strftime('%Y%m%d%H%M%S')}"
        for channel, config in notif_cfg.get("channels", {}).items():
            if not config.get("enabled", False):
                continue
            message = NotificationMessage(
                channel=channel,
                title=title,
                body=body,
                level="REPORT",
                signal_id=signal_id,
                symbol="SPM",
                created_at=datetime.utcnow(),
            )
            result = await self.notifiers[channel].send(message)
            await self.signal_repository.save_notification(
                {
                    "signal_id": signal_id,
                    "channel": channel,
                    "target": channel,
                    "title": title,
                    "body": body,
                    "status": "SENT" if result.ok else "FAILED",
                    "error_message": result.error,
                    "sent_at": datetime.utcnow() if result.ok else None,
                }
            )
            if not result.ok:
                logger.warning("{} report notification failed: {}", channel, result.error)
