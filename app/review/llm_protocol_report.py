from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.llm.deepseek import DeepSeekClient
from app.review.indicator_snapshot import (
    archive_indicator_snapshot,
    build_indicator_snapshot,
    compact_symbol_snapshot_for_llm,
    compact_snapshot_for_llm,
    iter_indicator_snapshot_events,
    summarize_snapshot_for_report,
)


@dataclass(frozen=True)
class LlmProtocolReportPart:
    title: str
    body: str
    symbol: str
    market: str
    has_trade_opportunity: bool = False


async def build_llm_protocol_report(
    settings: Settings,
    system_config: dict[str, Any],
    hours: int,
    kline_count: int,
    strategy_count: int,
    recent_signals: list[dict[str, Any]],
    archive_repository: Any | None = None,
) -> tuple[str, str]:
    parts: list[LlmProtocolReportPart] = []
    async for part in stream_llm_protocol_report_parts(
        settings,
        system_config,
        hours,
        kline_count,
        strategy_count,
        recent_signals,
        archive_repository,
    ):
        parts.append(part)

    if len(parts) == 1 and parts[0].symbol == "SPM":
        return parts[0].title, parts[0].body

    title = f"SPM {hours}H DeepSeek 协议监控报告"
    return title, _combine_report_parts(hours, kline_count, strategy_count, recent_signals, parts)


async def stream_llm_protocol_report_parts(
    settings: Settings,
    system_config: dict[str, Any],
    hours: int,
    kline_count: int,
    strategy_count: int,
    recent_signals: list[dict[str, Any]],
    archive_repository: Any | None = None,
) -> AsyncIterator[LlmProtocolReportPart]:
    title = f"SPM {hours}H DeepSeek 协议监控报告"
    client = DeepSeekClient(settings)
    if not client.is_configured:
        snapshot = build_indicator_snapshot(system_config, settings)
        snapshot["monitor_window"] = _monitor_window(hours, kline_count, strategy_count, recent_signals)
        await archive_indicator_snapshot(snapshot, settings, archive_repository)
        yield LlmProtocolReportPart(title=title, body=_missing_key_body(settings, snapshot), symbol="SPM", market="overview")
        return

    try:
        protocols = {
            "crypto": _read_protocol(settings.crypto_protocol_path),
            "equity": _read_protocol(settings.equity_protocol_path),
        }
    except Exception as exc:
        snapshot = build_indicator_snapshot(system_config, settings)
        snapshot["monitor_window"] = _monitor_window(hours, kline_count, strategy_count, recent_signals)
        await archive_indicator_snapshot(snapshot, settings, archive_repository)
        yield LlmProtocolReportPart(title=title, body=_llm_error_body(exc, snapshot), symbol="SPM", market="overview")
        return

    final_snapshot: dict[str, Any] | None = None
    monitor_window = _monitor_window(hours, kline_count, strategy_count, recent_signals)
    try:
        for event in iter_indicator_snapshot_events(system_config, settings):
            event.snapshot["monitor_window"] = monitor_window
            final_snapshot = event.snapshot
            if event.item.get("status") != "ok":
                body = _symbol_data_error_body(event.symbol, event.market, event.item)
                yield LlmProtocolReportPart(
                    title=_symbol_title(hours, event.symbol, event.market, False),
                    body=body,
                    symbol=event.symbol,
                    market=event.market,
                    has_trade_opportunity=False,
                )
                continue

            payload = compact_symbol_snapshot_for_llm(
                event.snapshot,
                event.market,
                event.item,
                monitor_window["recent_signals"],
            )
            try:
                body = await client.chat(
                    _symbol_messages(
                        hours,
                        event.symbol,
                        event.market,
                        payload,
                        protocols[event.market],
                    )
                )
            except Exception as exc:
                body = _symbol_llm_error_body(exc, event.symbol, event.market, event.snapshot)
                has_trade_opportunity = False
            else:
                has_trade_opportunity = _has_trade_opportunity(body)

            yield LlmProtocolReportPart(
                title=_symbol_title(hours, event.symbol, event.market, has_trade_opportunity),
                body=body,
                symbol=event.symbol,
                market=event.market,
                has_trade_opportunity=has_trade_opportunity,
            )
    finally:
        if final_snapshot is not None:
            await archive_indicator_snapshot(_json_safe(final_snapshot), settings, archive_repository)


async def build_legacy_llm_protocol_report(
    settings: Settings,
    system_config: dict[str, Any],
    hours: int,
    kline_count: int,
    strategy_count: int,
    recent_signals: list[dict[str, Any]],
    archive_repository: Any | None = None,
) -> tuple[str, str]:
    snapshot = build_indicator_snapshot(system_config, settings)
    snapshot["monitor_window"] = {
        "hours": hours,
        "kline_records_in_db": kline_count,
        "strategy_state_count": strategy_count,
        "recent_signal_count": len(recent_signals),
        "recent_signals": _serialize_signals(recent_signals[:10]),
    }
    await archive_indicator_snapshot(snapshot, settings, archive_repository)

    title = f"SPM {hours}H DeepSeek 协议监控报告"
    client = DeepSeekClient(settings)
    if not client.is_configured:
        return title, _missing_key_body(settings, snapshot)

    try:
        crypto_protocol = _read_protocol(settings.crypto_protocol_path)
        equity_protocol = _read_protocol(settings.equity_protocol_path)
        body = await client.chat(_messages(hours, compact_snapshot_for_llm(snapshot), crypto_protocol, equity_protocol))
    except Exception as exc:
        return title, _llm_error_body(exc, snapshot)

    return title, body


def _messages(
    hours: int,
    snapshot: dict[str, Any],
    crypto_protocol: str,
    equity_protocol: str,
) -> list[dict[str, str]]:
    system = (
        "你是 SmartMoney Protocol Monitor 的协议执行代理。"
        "你只根据用户提供的协议文本与指标快照进行分析，不允许编造缺失数据。"
        "当真实 CVD、Cluster、Liquidation、Options Flow、Gamma Exposure 缺失时，必须显式降级判断。"
        "你输出的是监控与交易计划，不代表自动下单；禁止写成已经执行交易。"
        "Micro 与 Macro 必须分离，必须给出触发条件、失效条件、目标位和风险降级说明。"
        "输出中文，简洁但不能省略关键证据。"
    )
    user = f"""
请按照以下协议，对指标快照中的全部标的生成 {hours}H 监控报告。

硬性输出格式：
1. 标题使用「SPM {hours}H 监控报告」。
2. 先给「总览」：市场状态、风险开关、今天最需要等的触发。
3. 每个标的必须使用独立二级标题「## 标的：SYMBOL（市场）」；不得把多个标的合并成批量简报。
4. 每个标的都必须包含：
   - 数据时间 / 数据源 / 数据质量
   - 当前状态
   - 是否命中：命中或未命中哪些协议模式；缺数据必须写未命中或降级
   - 建议1
   - 建议2
   - Micro：方向、触发条件、失效条件、48H 时间止损
   - Macro：方向、关键收复/跌破位、是否进入观察池
   - 关键指标证据：结构、ATR、MACD、VWAP/AVWAP、VP POC/HVN/LVN/VA、Volume Delta Profile、Taker Delta 或 OHLCV Delta Flow、CVD slope/acceleration/背离/吸收、OBV/A-D/NVI、FVG/Order Block/Displacement/流动性池、Confluence flags、量能、Funding/OI 或相对强弱
   - 最终交易指令：当前指令、多头预警、空头预警、Macro 预警、一句话结论
5. 对 BTC 只能作为 ETH 风险过滤器，除非协议文本要求独立分析。
6. 对美股必须考虑 External / Index / Sector / Asset Execution 四层。
7. 语言要像给交易员的执行简报，不要写教学说明。

【Crypto 协议 v16】
{crypto_protocol}

【Equity 协议 v17】
{equity_protocol}

【指标快照 JSON】
{json.dumps(snapshot, ensure_ascii=False, indent=2)}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _symbol_messages(
    hours: int,
    symbol: str,
    market: str,
    snapshot: dict[str, Any],
    protocol: str,
) -> list[dict[str, str]]:
    market_label = _market_label(market)
    system = (
        "你是 SmartMoney Protocol Monitor 的单标的协议执行代理。"
        "你只根据用户提供的协议文本与指标快照进行分析，不允许编造缺失数据。"
        "这是按历史复盘校准后的执行口径：严格风控不等于默认禁止，缺失真实 CVD、Cluster、清算、期权或 Gamma 数据时，"
        "应降低置信度、胜率区间和仓位，而不是自动否决所有交易设想。"
        "只有无明确 SL、TP1 R/R 明确低于 1.5R、触发条件已经失效、或高周期/风险过滤出现硬冲突时，才写禁止交易。"
        "若证据不够开仓但有清晰触发路径，必须给 WATCH 或 ARMED，而不是笼统写无机会。"
        "你输出的是监控与交易计划，不代表自动下单；禁止写成已经执行交易。"
        "输出中文，像给交易员的执行简报。"
    )
    user = f"""
请只分析这一个标的：{symbol}（{market_label}），生成 {hours}H 单标的协议报告。

输出必须使用以下格式，不要合并其他标的：

## 标的：{symbol}（{market_label}）
机会等级：TRADE / ARMED / WATCH / NONE / DATA_ERROR
交易机会：是/否
机会类型：Micro / Macro / Both / None
数据时间 / 数据源 / 数据质量：
当前状态：
协议命中：
关键证据：
- 结构：
- 库存/VP/VWAP：
- Flow/Delta：
- 动能/波动：
- 风险过滤：
Micro：
- 方向：
- Entry：
- SL：
- TP1 / TP2 / TP3：
- TP1 R/R：
- 时间止损：
- 条件胜率区间：
- 仓位：
- 触发条件：
- 失效条件：
Macro：
- 状态：
- 方向：
- 观察/建仓条件：
- 核心失效线：
最终交易指令：
- 当前指令：
- 多头预警：
- 空头预警：
- Macro 预警：
- 一句话结论：

执行校准：
1. TRADE = 当前已满足协议触发，并且有 Entry/SL/TP/RR；可推送为交易机会。
2. ARMED = 还差 1 个明确确认条件；必须写清楚还差什么、触发后如何执行。
3. WATCH = 有可观察路径但不足以开仓；不要硬凑 Entry。
4. NONE = 没有清晰路径；只给分析、关键位和下一轮观察条件。
5. DATA_ERROR = 数据失败；不做交易判断。
6. CVD/Delta 若为 proxy 或 taker delta，允许作为二级证据使用，但必须写明置信度折扣。
7. 对 BTC：默认作为 ETH/加密市场风险过滤器；只有快照本身显示独立机会时才给 TRADE/ARMED。
8. 对美股：必须考虑 External / Index / Sector / Asset Execution 四层；缺外部/板块数据时降级，不自动禁止所有 Micro 机会。

【本标的协议】
{protocol}

【单标的指标快照 JSON】
{json.dumps(snapshot, ensure_ascii=False, indent=2)}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _monitor_window(hours: int, kline_count: int, strategy_count: int, recent_signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "hours": hours,
        "kline_records_in_db": kline_count,
        "strategy_state_count": strategy_count,
        "recent_signal_count": len(recent_signals),
        "recent_signals": _serialize_signals(recent_signals[:10]),
    }


def _combine_report_parts(
    hours: int,
    kline_count: int,
    strategy_count: int,
    recent_signals: list[dict[str, Any]],
    parts: list[LlmProtocolReportPart],
) -> str:
    opportunity_count = sum(1 for part in parts if part.has_trade_opportunity)
    lines = [
        "## 总览",
        f"窗口：最近 {hours} 小时",
        f"K线记录：{kline_count}",
        f"策略状态：{strategy_count}",
        f"信号数量：{len(recent_signals)}",
        f"逐标的报告：{len(parts)}",
        f"交易机会报告：{opportunity_count}",
    ]
    for part in parts:
        lines.extend(["", part.body])
    return "\n".join(lines)


def _symbol_title(hours: int, symbol: str, market: str, has_trade_opportunity: bool) -> str:
    suffix = "交易机会" if has_trade_opportunity else "分析"
    return f"SPM {hours}H {symbol} {suffix}报告（{_market_label(market)}）"


def _symbol_data_error_body(symbol: str, market: str, item: dict[str, Any]) -> str:
    return "\n".join(
        [
            f"## 标的：{symbol}（{_market_label(market)}）",
            "机会等级：DATA_ERROR",
            "交易机会：否",
            "机会类型：None",
            f"数据时间 / 数据源 / 数据质量：数据失败；error={item.get('error')}",
            "当前状态：数据不足，暂不做协议判断。",
            "协议命中：未命中，等待下一轮数据恢复。",
            "最终交易指令：",
            "- 当前指令：数据不足，禁止开仓。",
            "- 多头预警：等待数据恢复后重新计算。",
            "- 空头预警：等待数据恢复后重新计算。",
            "- Macro 预警：数据源未恢复前不做高周期判断。",
            "- 一句话结论：先修数据，再谈交易。",
        ]
    )


def _symbol_llm_error_body(exc: Exception, symbol: str, market: str, snapshot: dict[str, Any]) -> str:
    lines = [
        f"## 标的：{symbol}（{_market_label(market)}）",
        "机会等级：DATA_ERROR",
        "交易机会：否",
        "机会类型：None",
        "DeepSeek 单标的调用失败，已完成该标的指标计算，本轮不做交易机会判断。",
        f"错误：{exc}",
        "",
        "本轮指标快照摘要：",
        *summarize_snapshot_for_report(snapshot),
    ]
    return "\n".join(lines)


def _has_trade_opportunity(body: str) -> bool:
    head = body[:1200]
    return bool(
        re.search(r"机会等级\s*[：:]\s*TRADE\b", head, flags=re.IGNORECASE)
        or re.search(r"交易机会\s*[：:]\s*是", head)
    )


def _market_label(market: str) -> str:
    return "Crypto" if market == "crypto" else "US Equity" if market == "equity" else market


def _read_protocol(path_value: str) -> str:
    path = Path(path_value)
    if not path.exists():
        raise FileNotFoundError(f"protocol file not found: {path_value}")
    return path.read_text(encoding="utf-8")


def _missing_key_body(settings: Settings, snapshot: dict[str, Any]) -> str:
    lines = [
        "DeepSeek API Key 未配置，已完成外部数据抓取、指标计算与指标归档，但没有调用大模型生成协议判断。",
        "",
        "需要配置：",
        "- `DEEPSEEK_API_KEY`",
        f"- `DEEPSEEK_BASE_URL={settings.deepseek_base_url}`",
        f"- `DEEPSEEK_MODEL={settings.deepseek_model}`",
        f"- `DEEPSEEK_THINKING={settings.deepseek_thinking}`",
        f"- `DEEPSEEK_REASONING_EFFORT={settings.deepseek_reasoning_effort}`",
        "",
        "本轮指标快照摘要：",
        *summarize_snapshot_for_report(snapshot),
    ]
    return "\n".join(lines)


def _llm_error_body(exc: Exception, snapshot: dict[str, Any]) -> str:
    lines = [
        "DeepSeek 调用失败，已完成外部数据抓取、指标计算与指标归档。",
        f"错误：{exc}",
        "",
        "本轮指标快照摘要：",
        *summarize_snapshot_for_report(snapshot),
    ]
    return "\n".join(lines)


def _serialize_signals(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [_json_safe(signal) for signal in signals]


def _json_safe(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return value
