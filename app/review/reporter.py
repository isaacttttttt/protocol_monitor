from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from loguru import logger

from app.config.settings import Settings
from app.notifications.base import NotificationMessage, NotificationResult
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
                result = await self._deliver_notification(channel, message)
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
        notification_attempts = 0
        non_trade_parts: list[LlmProtocolReportPart] = []
        data_error_parts: list[LlmProtocolReportPart] = []
        stream = stream_llm_protocol_report_parts(
            self.settings,
            self.system_config,
            hours,
            kline_count,
            strategy_count,
            recent_signals,
            self.indicator_archive_repository,
        )
        while True:
            try:
                part = await anext(stream)
            except StopAsyncIteration:
                break
            except Exception as exc:
                logger.exception("LLM streaming report aborted before all symbols completed")
                part = _stream_error_part(hours, exc)
                part_count += 1
                data_error_parts.append(part)
                print(f"{part.title}\n\n{part.body}\n")
                break
            part_count += 1
            print(f"{part.title}\n\n{part.body}\n")
            if part.has_trade_opportunity:
                if notifications_enabled:
                    notification_attempts += await self._send_report_part(
                        part,
                        notif_cfg,
                        signal_id,
                        part_count,
                    )
            elif str((part.decision or {}).get("status")) == "DATA_ERROR":
                data_error_parts.append(part)
            else:
                non_trade_parts.append(part)

        if part_count == 0:
            logger.warning("LLM streaming report produced no parts")
            if notifications_enabled:
                summary = _data_error_summary(hours, [], [], no_parts=True)
                print(f"{summary.title}\n\n{summary.body}\n")
                await self._send_report_part(summary, notif_cfg, signal_id, 1)
        elif notifications_enabled and data_error_parts:
            summary = _data_error_summary(
                hours,
                data_error_parts,
                non_trade_parts if notification_attempts == 0 else [],
            )
            print(f"{summary.title}\n\n{summary.body}\n")
            await self._send_report_part(summary, notif_cfg, signal_id, part_count + 1)
        elif notifications_enabled and notification_attempts == 0:
            summary = _no_opportunity_summary(hours, non_trade_parts)
            print(f"{summary.title}\n\n{summary.body}\n")
            await self._send_report_part(summary, notif_cfg, signal_id, part_count + 1)

    async def _send_report_part(
        self,
        part: LlmProtocolReportPart,
        notif_cfg: dict[str, Any],
        signal_id: str,
        index: int,
    ) -> int:
        level = "OPPORTUNITY" if part.has_trade_opportunity else "REPORT"
        part_signal_id = part.opportunity_id or f"{signal_id}-{index:02d}-{part.symbol}"
        attempted = 0
        for channel, config in notif_cfg.get("channels", {}).items():
            if not config.get("enabled", False):
                continue
            was_sent = getattr(self.signal_repository, "was_notification_sent", None)
            if part.opportunity_id and was_sent:
                try:
                    if await was_sent(part_signal_id, channel):
                        logger.info(
                            "duplicate opportunity notification skipped: signal_id={} channel={}",
                            part_signal_id,
                            channel,
                        )
                        continue
                except Exception:
                    logger.exception(
                        "notification dedupe lookup failed; delivery will continue: signal_id={} channel={}",
                        part_signal_id,
                        channel,
                    )
            message = NotificationMessage(
                channel=channel,
                title=part.title,
                body=part.body,
                level=level,
                signal_id=part_signal_id,
                symbol=part.symbol,
                created_at=_utcnow(),
            )
            attempted += 1
            result = await self._deliver_notification(channel, message)
            if not result.ok:
                logger.warning("{} streaming report notification failed for {}: {}", channel, part.symbol, result.error)
        return attempted

    async def _deliver_notification(
        self,
        channel: str,
        message: NotificationMessage,
    ) -> NotificationResult:
        notifier = self.notifiers.get(channel)
        if notifier is None:
            result = NotificationResult(False, f"notifier is not configured for channel={channel}")
        else:
            try:
                result = await notifier.send(message)
            except Exception as exc:
                error = f"{exc.__class__.__name__}: {exc}"
                logger.exception(
                    "notification delivery raised an exception: channel={} signal_id={}",
                    channel,
                    message.signal_id,
                )
                result = NotificationResult(False, error)

        try:
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
        except Exception:
            logger.exception(
                "notification result persistence failed; remaining deliveries will continue: "
                "channel={} signal_id={}",
                channel,
                message.signal_id,
            )
        return result

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


def _no_opportunity_summary(
    hours: int,
    parts: list[LlmProtocolReportPart],
) -> LlmProtocolReportPart:
    lines = [
        "## 本轮结论",
        "- 高周期机会：0",
        "- 当前指令：不做",
    ]
    for part in parts:
        decision = part.decision or {}
        reasons = decision.get("rejection_reasons") or []
        reason = str(reasons[0]) if reasons else str(decision.get("summary") or "未通过协议硬门槛")
        lines.append(f"- {part.symbol}：{reason}")
    if not parts:
        lines.append("- 原因：没有新机会；已发送的同一高周期计划不重复推送。")
    return LlmProtocolReportPart(
        title=f"SPM {hours}H 本轮无新高周期机会",
        body="\n".join(lines),
        symbol="SPM",
        market="overview",
        has_trade_opportunity=False,
        decision={
            "status": "NO_TRADE",
            "summary": "本轮没有通过校验的新高周期机会",
            "rejection_reasons": [line for line in lines[3:]],
        },
    )


def _data_error_summary(
    hours: int,
    error_parts: list[LlmProtocolReportPart],
    non_trade_parts: list[LlmProtocolReportPart],
    *,
    no_parts: bool = False,
) -> LlmProtocolReportPart:
    lines = [
        "## 运行异常",
        f"- 分析失败标的：{len(error_parts)}",
        "- 当前指令：异常标的禁止开仓",
    ]
    if no_parts:
        lines.append("- 系统：本轮未生成任何标的分析结果，请检查行情、协议和 LLM 服务日志。")
    for part in error_parts:
        decision = part.decision or {}
        reasons = decision.get("rejection_reasons") or []
        reason = str(reasons[0]) if reasons else str(decision.get("summary") or "未知运行错误")
        lines.append(f"- {part.symbol}：{reason}")
    if non_trade_parts:
        lines.append(f"- 其余已完成分析：{len(non_trade_parts)} 个，均无合格交易机会。")
    return LlmProtocolReportPart(
        title=f"SPM {hours}H 运行异常",
        body="\n".join(lines),
        symbol="SPM",
        market="overview",
        has_trade_opportunity=False,
        decision={
            "status": "DATA_ERROR",
            "summary": "本轮存在未完成协议判断的标的",
            "rejection_reasons": [line for line in lines[3:]],
        },
    )


def _stream_error_part(hours: int, exc: Exception) -> LlmProtocolReportPart:
    error = f"{exc.__class__.__name__}: {exc}"
    return LlmProtocolReportPart(
        title=f"SPM {hours}H 分析流程中断",
        body=f"## 运行异常\n- 分析流程：{error}\n- 当前指令：禁止根据本轮不完整结果开仓",
        symbol="SYSTEM",
        market="overview",
        has_trade_opportunity=False,
        decision={
            "status": "DATA_ERROR",
            "summary": "分析流程在完成全部标的前中断",
            "rejection_reasons": [error],
        },
    )
