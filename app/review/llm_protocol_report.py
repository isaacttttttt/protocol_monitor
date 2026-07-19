from __future__ import annotations

import json
import hashlib
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.llm.openai_compatible import OpenAICompatibleClient
from app.review.llm_decision import (
    DecisionValidationError,
    LLMDecision,
    validate_decision,
)
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
    decision: dict[str, Any] | None = None
    opportunity_id: str | None = None


def _strategy_trader_system_prompt(scope: str) -> str:
    return (
        f"你是 SmartMoney Protocol Monitor 的{scope}。"
        "你的定位是资深策略交易员，长期专注美股标的、ETF、加密货币与跨市场风险传导。"
        "你必须按用户提供的完整协议完成最终交易判断。"
        "代码只负责计算指标和校验输出，不代替你判断协议是否形成机会。"
        "严格只使用用户提供的协议文本与指标快照，不编造缺失数据；缺真实 CVD、Cluster、清算、期权流或 Gamma 数据时要降级置信度。"
        "本轮运行模式只执行完整协议中的高时间框架部分：1H=执行，4H=结构，DAY=主环境。"
        "本运行模式明确以 DAY 为最高环境；协议原文中的 Micro、5M/15M、1W/周线或其他范围外周期条款在本轮不执行；"
        "这些周期的数据缺失不得被当作本轮缺失数据、否决理由或 DATA_ERROR。"
        "本轮高周期 JSON 合同覆盖原协议中的旧 Markdown、Micro 双账本和旧输出格式，但判断逻辑、风控原则与结构依据仍必须沿用完整协议。"
        "输出只能是协议规定的单个 JSON 对象，不得输出 Markdown 或额外说明。"
        "TRADE 必须给出可执行的时机、条件、Entry、SL、TP、R/R、失效和三条数字依据；"
        "NO_TRADE 必须明确说明本轮不做的硬原因，不得使用模糊观察性措辞。"
        "输出是监控和交易计划，不代表自动下单。"
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
    protocol_error: Exception | None = None
    protocols: dict[str, str] = {}
    try:
        protocols = {
            "crypto": _read_protocol(settings.crypto_protocol_path),
            "equity": _read_protocol(settings.equity_protocol_path),
        }
    except Exception as exc:
        protocol_error = exc

    final_snapshot: dict[str, Any] | None = None
    monitor_window = _monitor_window(hours, kline_count, strategy_count, recent_signals)
    min_rr = float(system_config.get("risk", {}).get("min_rr_to_tp1", 1.5))
    # The report runs the Macro/high-timeframe side of the complete protocols.
    # Do not apply the Micro 0.5R cap or a generic score gate here.
    max_position_r = float(
        system_config.get("risk", {}).get("max_macro_position_r", 1.5)
    )
    repair_attempts = int(
        system_config.get("report", {}).get("llm_decision", {}).get("repair_attempts", 1)
    )
    try:
        for event in iter_indicator_snapshot_events(system_config, settings):
            event.snapshot["monitor_window"] = monitor_window
            final_snapshot = event.snapshot
            if event.item.get("status") != "ok":
                decision = _non_trade_decision(
                    "DATA_ERROR",
                    f"{event.symbol} 行情数据不可用",
                    [str(event.item.get("error") or "行情数据加载失败")],
                )
                body = _render_decision_body(event.symbol, event.market, event.item, decision)
                yield LlmProtocolReportPart(
                    title=_symbol_title(hours, event.symbol, event.market, False),
                    body=body,
                    symbol=event.symbol,
                    market=event.market,
                    has_trade_opportunity=False,
                    decision=decision,
                )
                continue

            payload = compact_symbol_snapshot_for_llm(
                event.snapshot,
                event.market,
                event.item,
                monitor_window["recent_signals"],
            )
            decision: dict[str, Any]
            if not client.is_configured:
                missing = ", ".join(client.missing_config_keys) or "LLM configuration"
                decision = _non_trade_decision(
                    "DATA_ERROR",
                    "LLM 未配置，无法按协议完成判断",
                    [missing],
                )
            elif protocol_error is not None:
                decision = _non_trade_decision(
                    "DATA_ERROR",
                    "协议文件不可用，无法完成判断",
                    [str(protocol_error)],
                )
            else:
                messages = _symbol_messages(
                    hours,
                    event.symbol,
                    event.market,
                    payload,
                    protocols[event.market],
                )
                try:
                    raw = ""
                    raw = await client.chat(messages)
                    decision = _validate_symbol_decision(
                        raw,
                        event.item,
                        min_rr,
                        max_position_r,
                    )
                except DecisionValidationError as exc:
                    decision = await _repair_or_reject_decision(
                        client,
                        messages,
                        raw,
                        exc,
                        event.item,
                        min_rr,
                        max_position_r,
                        repair_attempts,
                    )
                except Exception as exc:
                    decision = _non_trade_decision(
                        "DATA_ERROR",
                        "LLM 调用失败，未生成协议判断",
                        [f"{exc.__class__.__name__}: {exc}"],
                    )

            has_trade_opportunity = decision["status"] == "TRADE"
            body = _render_decision_body(event.symbol, event.market, event.item, decision)
            opportunity_id = (
                _opportunity_id(event.symbol, decision, event.item)
                if has_trade_opportunity
                else None
            )
            yield LlmProtocolReportPart(
                title=_symbol_title(hours, event.symbol, event.market, has_trade_opportunity),
                body=body,
                symbol=event.symbol,
                market=event.market,
                has_trade_opportunity=has_trade_opportunity,
                decision=decision,
                opportunity_id=opportunity_id,
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
请按照以下协议，对指标快照中的全部标的生成 {hours}H 高时间框架监控报告。

硬性输出格式：
1. 标题使用「SPM {hours}H 监控报告」。
2. 先给「总览」：市场状态、风险开关和明确的做/不做结论。
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
    schema_text = json.dumps(_decision_json_schema(), ensure_ascii=False, indent=2)
    user = f"""
请只分析 {symbol}（{market_label}）。任务每 {hours} 小时运行一次，但这只是监控频率；
这不是“每次运行对应一根 1H K 线”的要求；交易机会只从 1H/4H/DAY 的高周期结构中判断。
1H 只负责执行层，4H 负责结构层，DAY 负责主环境层。

最终评分与交易判断由你按照完整协议完成；但本运行模式明确以 DAY 为最高环境。
原协议中要求 5M/15M、1W/周线、Micro 双账本输出或旧 Markdown 的条款，
在本轮不是缺失数据，也不是否决理由；本轮只按上述 1H/4H/DAY 高周期模式输出。

【本标的协议】
{protocol}

【单标的指标快照 JSON】
{json.dumps(snapshot, ensure_ascii=False, indent=2)}

【本轮 JSON 合同（格式优先级高于协议原文的旧输出格式）】
只能输出一个 JSON 对象，键集合必须与下面 Schema 完全一致，不得增加或省略键：
{schema_text}

合同执行规则：
1. status 只接受 TRADE、NO_TRADE、DATA_ERROR；TRADE 的 timeframe 只接受 1H、4H、DAY。
2. TRADE 的 entry 是实际保守执行价：NOW=快照 current_price；LONG 的 LIMIT/STOP=entry_zone_high；
   SHORT 的 LIMIT/STOP=entry_zone_low。entry 必须位于 entry_zone_low/high 内。
3. 代码会按该实际执行价复算方向价格顺序和 TP1 R/R；实际 R/R >= 1.5，
   risk_reward 必须填 null（由代码计算），不得估算或伪造。
4. position_r 遵循完整协议的高周期仓位规则，范围为 0 到 1.5R；不要套用 Micro 的 0.5R 上限。
5. score 是完整协议的模型评分（0-100），代码只校验范围，不用统一的最低分替代协议判断。
6. TRADE 必须填写所有非空交易字段、三条互不重复且各含数字的 evidence；rejection_reasons 必须为 []。
7. NO_TRADE/DATA_ERROR 除 status、summary、rejection_reasons 外，所有交易字段必须为 null，
   evidence 必须为 []，并给出 1-3 条确定的硬原因；不得输出“等待确认/观察/继续关注”等模糊结论。
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _decision_json_schema() -> dict[str, Any]:
    """Return the exact wire schema, including every key required by the renderer."""

    schema = LLMDecision.model_json_schema()
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    schema["required"] = list(LLMDecision.model_fields)
    return schema


def _validate_symbol_decision(
    raw: str,
    item: dict[str, Any],
    min_rr: float,
    max_position_r: float,
) -> dict[str, Any]:
    return validate_decision(
        raw,
        current_price=float(item["price"]),
        min_rr=min_rr,
        max_position_r=max_position_r,
    )


async def _repair_or_reject_decision(
    client: OpenAICompatibleClient,
    messages: list[dict[str, str]],
    raw: str,
    validation_error: DecisionValidationError,
    item: dict[str, Any],
    min_rr: float,
    max_position_r: float,
    repair_attempts: int,
) -> dict[str, Any]:
    errors = list(validation_error.errors)
    previous = raw
    for _ in range(max(0, repair_attempts)):
        try:
            previous = await client.chat(_repair_messages(messages, previous, errors))
            return _validate_symbol_decision(
                previous,
                item,
                min_rr,
                max_position_r,
            )
        except DecisionValidationError as exc:
            errors = list(exc.errors)
        except Exception as exc:
            return _non_trade_decision(
                "DATA_ERROR",
                "LLM 修正请求失败，未生成合格交易合同",
                [f"{exc.__class__.__name__}: {exc}"],
            )
    return _non_trade_decision(
        "DATA_ERROR",
        "LLM 输出未通过交易合同校验",
        errors[:3] or ["未知合同错误"],
    )


def _repair_messages(
    messages: list[dict[str, str]],
    previous: str,
    errors: list[str],
) -> list[dict[str, str]]:
    issues = "\n".join(f"- {error}" for error in errors)
    return [
        *messages,
        {"role": "assistant", "content": previous},
        {
            "role": "user",
            "content": (
                "上一个 JSON 未通过程序校验。请保持按原协议独立判断，只修正合同错误，"
                "并重新输出完整 JSON；不要解释。\n校验错误：\n"
                f"{issues}"
            ),
        },
    ]


def _non_trade_decision(
    status: str,
    summary: str,
    reasons: list[str],
) -> dict[str, Any]:
    return {
        "status": status,
        "timeframe": None,
        "direction": None,
        "execution_mode": None,
        "execution_condition": None,
        "entry": None,
        "entry_zone_low": None,
        "entry_zone_high": None,
        "stop_loss": None,
        "tp1": None,
        "tp2": None,
        "time_stop": None,
        "position_r": None,
        "protocol_setup": None,
        "score": None,
        "evidence": [],
        "invalidation": None,
        "summary": summary,
        "rejection_reasons": [str(reason) for reason in reasons if str(reason).strip()],
        "risk_reward": None,
    }


def _render_decision_body(
    symbol: str,
    market: str,
    item: dict[str, Any],
    decision: dict[str, Any],
) -> str:
    status = str(decision["status"])
    is_trade = status == "TRADE"
    market_label = _market_label(market)
    price = _format_price(item.get("price"))
    unavailable = ", ".join(str(value) for value in item.get("unavailable", []))
    data_quality = (
        "已收盘 1H/4H/DAY 指标完整"
        if item.get("status") == "ok"
        else "行情数据不可用"
    )
    if unavailable:
        data_quality = f"{data_quality}；不可用：{unavailable}"

    lines = [
        f"## 标的：{symbol}（{market_label}）",
        "",
        "### 1. 标的基础信息",
        f"- 标的：{symbol}",
        f"- 市场：{market_label}",
        f"- 时间：{item.get('updated_at_local') or item.get('updated_at') or 'N/A'}",
        f"- 当前价格：{price}",
        f"- 数据源：{item.get('source') or 'N/A'}",
        f"- 数据质量：{data_quality}",
        "",
        "### 2. 策略分析结论",
        f"- 机会等级：{status}",
        f"- 交易机会：{'是' if is_trade else '否'}",
        f"- 机会类型：{decision.get('timeframe') or 'None'}",
        f"- 策略结论：{'做' if is_trade else '不做'}；{decision.get('summary') or '无结论'}",
        f"- 协议命中：{decision.get('protocol_setup') or '未形成合格机会'}",
    ]
    evidence = (
        list(decision.get("evidence") or [])
        if is_trade
        else list(decision.get("rejection_reasons") or [])
    )
    for index, value in enumerate(evidence[:3], start=1):
        lines.append(f"- 核心证据{index}：{value}")

    lines.extend(["", "### 3. 推荐执行策略"])
    if not is_trade:
        lines.extend(
            [
                "- 当前指令：不做",
                f"- 否决原因：{'；'.join(evidence) or '没有通过协议硬门槛'}",
                f"- 一句话：{decision.get('summary') or '本轮不做'}",
            ]
        )
        return "\n".join(lines)

    entry = _format_price(decision["entry"])
    zone_low = _format_price(decision["entry_zone_low"])
    zone_high = _format_price(decision["entry_zone_high"])
    tp2 = decision.get("tp2")
    targets = f"TP1 {_format_price(decision['tp1'])}"
    if tp2 is not None:
        targets += f"；TP2 {_format_price(tp2)}"
    lines.extend(
        [
            f"- 当前指令：做；{decision['execution_condition']}",
            f"- 周期：{decision['timeframe']}",
            f"- 方向：{decision['direction']}",
            f"- 执行方式：{decision['execution_mode']}",
            f"- Entry/触发：{entry}；有效区间 {zone_low}-{zone_high}",
            f"- SL/失效：{_format_price(decision['stop_loss'])}；{decision['invalidation']}",
            f"- TP/RR：{targets}；TP1 R/R {float(decision['risk_reward']):.2f}R",
            f"- 时间止损：{decision['time_stop']}",
            f"- 仓位：{_format_number(decision['position_r'])}R",
            f"- 预警：{decision['invalidation']}",
            f"- 一句话：{decision['summary']}",
        ]
    )
    return "\n".join(lines)


def _opportunity_id(
    symbol: str,
    decision: dict[str, Any],
    item: dict[str, Any],
) -> str:
    timeframe_key = {"1H": "1h", "4H": "4h", "DAY": "1d"}[str(decision["timeframe"])]
    source_time = (
        item.get("timeframes", {})
        .get(timeframe_key, {})
        .get("last_bar", {})
        .get("time", item.get("updated_at", "unknown"))
    )
    identity = "|".join(
        [
            symbol.upper(),
            str(decision["timeframe"]),
            str(decision["direction"]),
            str(source_time),
            str(decision["execution_mode"]),
            _canonical_contract_number(decision["entry"]),
            _canonical_contract_number(decision["entry_zone_low"]),
            _canonical_contract_number(decision["entry_zone_high"]),
            _canonical_contract_number(decision["stop_loss"]),
            _canonical_contract_number(decision["tp1"]),
            _canonical_contract_number(decision.get("tp2")),
            _canonical_contract_number(decision["position_r"]),
        ]
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()[:16]
    return f"SPM-HTF-{symbol.upper()}-{decision['timeframe']}-{decision['direction']}-{digest}"


def _canonical_contract_number(value: Any) -> str:
    """Normalize equivalent numeric spellings for stable opportunity IDs."""

    if value is None:
        return "null"
    try:
        number = Decimal(str(value))
    except Exception:
        return str(value)
    if not number.is_finite():
        return str(value)
    if number == 0:
        return "0"
    return format(number.normalize(), "f")


def _format_price(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return "N/A"
    if abs(number) >= 1:
        return f"{number:.4f}".rstrip("0").rstrip(".")
    return f"{number:.8f}".rstrip("0").rstrip(".")


def _format_number(value: Any) -> str:
    try:
        return f"{float(value):.4f}".rstrip("0").rstrip(".")
    except (TypeError, ValueError):
        return "N/A"


def _monitor_window(hours: int, kline_count: int, strategy_count: int, recent_signals: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "hours": hours,
        "kline_records_in_db": kline_count,
        "strategy_state_count": strategy_count,
        "legacy_signal_count_ignored": len(recent_signals),
        "recent_signals": [],
        "strategy_scope": ["1H", "4H", "DAY"],
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
