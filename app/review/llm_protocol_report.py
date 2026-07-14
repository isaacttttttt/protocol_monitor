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
from app.llm.openai_compatible import OpenAICompatibleClient
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


def _strategy_trader_system_prompt(scope: str) -> str:
    return (
        f"你是 SmartMoney Protocol Monitor 的{scope}。"
        "你的定位是资深策略交易员，长期专注美股标的、ETF、加密货币与跨市场风险传导。"
        "你擅长识别趋势切换、流动性扫荡、动能衰竭、风险偏好迁移，并把复杂指标压缩成可执行下注计划。"
        "你的工作方式是概率化推演：先判断市场状态，再评估触发条件、赔率、失效点和仓位折扣。"
        "严格只使用用户提供的协议文本与指标快照，不编造缺失数据；缺真实 CVD、Cluster、清算、期权流或 Gamma 数据时要降级置信度。"
        "代码提供的是基础指标、因子、相对比较和候选形态证据；最终评分与交易判断由你完成。"
        "For US-equity M-E3 decisions, deterministic_orb_retest is an execution gate: "
        "only status=TRIGGERED may be labeled TRADE; WAIT/FILTERED states must remain ARMED/WATCH/NONE."
        "不得把 setup_candidates、BOS proxy、Flow proxy 或单一因子直接当成已触发交易。"
        "严格风控不等于默认禁止；证据不足但路径清晰时给 WATCH 或 ARMED，只有触发失效、R/R 不足或高周期硬冲突时才禁止。"
        "输出是监控和交易计划，不代表自动下单；语言要像给交易员的盘前/盘中执行卡片，短、准、可行动。"
    )


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

    client = OpenAICompatibleClient(settings)
    title = f"SPM {hours}H {client.display_name} 协议监控报告"
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
    client = OpenAICompatibleClient(settings)
    title = f"SPM {hours}H {client.display_name} 协议监控报告"
    if not client.is_configured:
        snapshot = build_indicator_snapshot(system_config, settings)
        snapshot["monitor_window"] = _monitor_window(hours, kline_count, strategy_count, recent_signals)
        await archive_indicator_snapshot(snapshot, settings, archive_repository)
        yield LlmProtocolReportPart(title=title, body=_missing_key_body(settings, snapshot, client), symbol="SPM", market="overview")
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

    client = OpenAICompatibleClient(settings)
    title = f"SPM {hours}H {client.display_name} 协议监控报告"
    if not client.is_configured:
        return title, _missing_key_body(settings, snapshot, client)

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
    system = _strategy_trader_system_prompt("多标的策略交易员")
    user = f"""
请按照以下协议，对指标快照中的全部标的生成 {hours}H 监控报告。

硬性输出格式：
1. 标题使用「SPM {hours}H 监控报告」。
2. 先给「总览」：市场状态、风险开关、今天最需要等的触发。
3. 每个标的必须使用独立二级标题「## 标的：SYMBOL（市场）」；每个标的只写以下三块：
   - ### 1. 标的基础信息
   - ### 2. 策略分析结论
   - ### 3. 推荐执行策略
4. 对 BTC 只能作为 ETH 风险过滤器，除非协议文本要求独立分析。
5. 对美股必须考虑 External / Index / Sector / Asset Execution 四层。
6. 每个标的控制在 500 中文字以内；不要写教学说明。

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
    system = _strategy_trader_system_prompt("单标的策略交易员")
    user = f"""
请只分析这一个标的：{symbol}（{market_label}），生成 {hours}H 单标的协议报告。

输出必须严格使用以下三段格式，不要合并其他标的，不要增加第四段：

## 标的：{symbol}（{market_label}）

### 1. 标的基础信息
- 标的：{symbol}
- 市场：{market_label}
- 时间：
- 当前价格：
- 数据源：
- 数据质量：

### 2. 策略分析结论
- 机会等级：TRADE / ARMED / WATCH / NONE / DATA_ERROR
- 交易机会：是/否
- 机会类型：Micro / Macro / Both / None
- Micro 结论：独立给出状态、方向、协议模式与最关键缺口
- Macro 结论：独立给出 S0-S5、方向、协议模式与最关键缺口
- Micro/Macro 冲突：一致或冲突；冲突时说明仓位折扣
- 策略结论：一句话说明当前最重要判断
- 协议命中：只写命中的 1-2 个协议模式；没有则写未命中
- 核心证据1：
- 核心证据2：
- 核心证据3：

### 3. 推荐执行策略
- 当前指令：
- 方向：
- Entry/触发：
- SL/失效：
- TP/RR：
- 时间止损：
- 仓位：
- 预警：
- 一句话：

执行校准：
0. 最终评分与交易判断由你完成；代码字段只提供观察、因子和候选形态证据。
1. TRADE = 当前已满足协议触发，并且有 Entry/SL/TP/RR；可推送为交易机会。
2. ARMED = 还差 1 个明确确认条件；必须写清楚还差什么、触发后如何执行。
3. WATCH = 有可观察路径但不足以开仓；不要硬凑 Entry。
4. NONE = 没有清晰路径；只给分析、关键位和下一轮观察条件。
5. DATA_ERROR = 数据失败；不做交易判断。
6. CVD/Delta 若为 proxy 或 taker delta，允许作为二级证据使用，但必须写明置信度折扣。
7. 对 BTC：默认作为 ETH/加密市场风险过滤器；只有快照本身显示独立机会时才给 TRADE/ARMED。
8. 对美股：必须考虑 External / Index / Sector / Asset Execution 四层；缺外部/板块数据时降级，不自动禁止所有 Micro 机会。
9. 必须分别判断 Micro 与 Macro；不得用 15M/60M 反弹替代日线/周线 Macro 修复。
10. setup_candidates 只表示条件接近或值得检查；不得把候选形态当成已触发。
11. 对 SOXL 等日重置杠杆 ETF，必须考虑标的波动、路径依赖和相对 SOXX/SMH 的跟踪偏差。
12. 对 MU 等半导体个股，必须同时对账 SOXX/SMH、同行广度和目标自身结构。

长度控制：
1. 全文 350-650 中文字；每个字段最多 1 句。
2. 核心证据只写结论 + 最关键数字，不写推导长段。
3. 推荐执行策略只保留最优先的一条路径；交易机会为否时，Entry/SL/TP 写“等待/不适用/触发后再定”，不要展开多空两套方案。
4. 当前指令必须最短、最清楚，优先写“现在做什么 / 等什么 / 什么情况作废”。
5. 不写教学解释，不复述完整指标清单，不输出原始 JSON。

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
            "",
            "### 1. 标的基础信息",
            f"- 标的：{symbol}",
            f"- 市场：{_market_label(market)}",
            "- 时间：不可用",
            "- 当前价格：不可用",
            "- 数据源：不可用",
            f"- 数据质量：数据失败；error={item.get('error')}",
            "",
            "### 2. 策略分析结论",
            "- 机会等级：DATA_ERROR",
            "- 交易机会：否",
            "- 机会类型：None",
            "- 策略结论：数据不足，暂不做协议判断。",
            "- 协议命中：未命中，等待下一轮数据恢复。",
            "- 核心证据1：行情数据失败。",
            "- 核心证据2：指标快照不可用。",
            "- 核心证据3：风控要求禁止基于缺失数据开仓。",
            "",
            "### 3. 推荐执行策略",
            "- 当前指令：数据不足，禁止开仓。",
            "- 方向：None",
            "- Entry/触发：等待数据恢复后重新计算。",
            "- SL/失效：不适用。",
            "- TP/RR：不适用。",
            "- 时间止损：不适用。",
            "- 仓位：0R。",
            "- 预警：数据源恢复后重新跑协议。",
            "- 一句话：先修数据，再谈交易。",
        ]
    )


def _symbol_llm_error_body(exc: Exception, symbol: str, market: str, snapshot: dict[str, Any]) -> str:
    lines = [
        f"## 标的：{symbol}（{_market_label(market)}）",
        "",
        "### 1. 标的基础信息",
        f"- 标的：{symbol}",
        f"- 市场：{_market_label(market)}",
        "- 时间：见本轮指标快照摘要",
        "- 当前价格：见本轮指标快照摘要",
        "- 数据源：见本轮指标快照摘要",
        "- 数据质量：指标已计算，大模型调用失败。",
        "",
        "### 2. 策略分析结论",
        "- 机会等级：DATA_ERROR",
        "- 交易机会：否",
        "- 机会类型：None",
        "- 策略结论：大模型单标的调用失败，本轮不做交易机会判断。",
        "- 协议命中：未判断。",
        f"- 核心证据1：错误：{exc}",
        "- 核心证据2：指标计算已完成。",
        "- 核心证据3：缺少 LLM 判断，禁止生成交易计划。",
        "",
        "### 3. 推荐执行策略",
        "- 当前指令：不交易，等待下一轮 LLM 恢复。",
        "- 方向：None",
        "- Entry/触发：不适用。",
        "- SL/失效：不适用。",
        "- TP/RR：不适用。",
        "- 时间止损：不适用。",
        "- 仓位：0R。",
        "- 预警：检查 LLM API 配置或服务状态。",
        "- 一句话：模型失败时不下注。",
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


def _missing_key_body(settings: Settings, snapshot: dict[str, Any], client: OpenAICompatibleClient) -> str:
    missing = client.missing_config_keys or ["LLM_API_KEY"]
    lines = [
        f"{client.display_name} API 配置未完成，已完成外部数据抓取、指标计算与指标归档，但没有调用大模型生成协议判断。",
        "",
        "需要配置以下环境变量：",
        *[f"- `{key}`" for key in missing],
        "",
        "推荐新通用配置：",
        f"- `LLM_CONFIG={settings.llm_config or 'fineres'}`",
        f"- `LLM_CONFIG_DIR={settings.llm_config_dir or 'configs/llms'}`",
        "- `LLM_API_KEY=sk_...`",
        "- Provider URL、模型和请求参数写在 `configs/llms/<LLM_CONFIG>.yaml`。",
        "",
        "本轮指标快照摘要：",
        *summarize_snapshot_for_report(snapshot),
    ]
    return "\n".join(lines)


def _llm_error_body(exc: Exception, snapshot: dict[str, Any]) -> str:
    lines = [
        "大模型调用失败，已完成外部数据抓取、指标计算与指标归档。",
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
