from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

from app.config.settings import Settings
from app.llm.deepseek import DeepSeekClient
from app.review.indicator_snapshot import (
    archive_indicator_snapshot,
    build_indicator_snapshot,
    summarize_snapshot_for_report,
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
    snapshot = build_indicator_snapshot(system_config)
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
        body = await client.chat(_messages(hours, snapshot, crypto_protocol, equity_protocol))
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
3. 每个标的都必须包含：
   - 数据时间 / 数据源 / 数据质量
   - 当前状态
   - 是否命中：命中或未命中哪些协议模式；缺数据必须写未命中或降级
   - 建议1
   - 建议2
   - Micro：方向、触发条件、失效条件、48H 时间止损
   - Macro：方向、关键收复/跌破位、是否进入观察池
   - 关键指标证据：结构、ATR、MACD、VWAP/AVWAP、VP POC/HVN/LVN、CVD proxy/OBV/A-D/NVI、量能、Funding/OI 或相对强弱
   - 最终交易指令：当前指令、多头预警、空头预警、Macro 预警、一句话结论
4. 对 BTC 只能作为 ETH 风险过滤器，除非协议文本要求独立分析。
5. 对美股必须考虑 External / Index / Sector / Asset Execution 四层。
6. 语言要像给交易员的执行简报，不要写教学说明。

【Crypto 协议 v16】
{crypto_protocol}

【Equity 协议 v17】
{equity_protocol}

【指标快照 JSON】
{json.dumps(snapshot, ensure_ascii=False, indent=2)}
""".strip()
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


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
