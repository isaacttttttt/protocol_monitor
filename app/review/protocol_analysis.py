from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any

from app.config.settings import get_settings

BINANCE_FAPI = "https://fapi.binance.com"
OKX_REST = "https://www.okx.com"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"
DEFAULT_CRYPTO_SYMBOLS = ["ETHUSDT", "BTCUSDT"]
DEFAULT_EQUITY_SYMBOLS = ["CRCL", "WDC", "ARM", "INTU", "INFQ"]
LOCAL_TZ = timezone(timedelta(hours=8))


@dataclass(frozen=True)
class ProtocolAnalysis:
    symbol: str
    market: str
    price: float
    change_pct: float | None
    current_status: str
    hit_status: str
    suggestion_1: str
    suggestion_2: str
    key_levels: str
    final_instruction: list[str]
    evidence: list[str]
    updated_at: str


@dataclass(frozen=True)
class CryptoMarketData:
    source: str
    market: str
    k15: list[dict[str, Any]]
    k5: list[dict[str, Any]]
    k4: list[dict[str, Any]]
    k1d: list[dict[str, Any]]
    last_price: float
    change_pct: float | None
    high_24h: float | None
    low_24h: float | None
    mark_price: float | None = None
    funding_rate: float | None = None
    open_interest: float | None = None
    fallback_notes: tuple[str, ...] = ()


def build_protocol_analyses(crypto_symbols: list[str] | None = None, equity_symbols: list[str] | None = None) -> list[ProtocolAnalysis]:
    crypto_symbols = crypto_symbols or DEFAULT_CRYPTO_SYMBOLS
    equity_symbols = equity_symbols or DEFAULT_EQUITY_SYMBOLS
    analyses: list[ProtocolAnalysis] = []

    btc_state: dict[str, Any] | None = None
    for symbol in crypto_symbols:
        try:
            analysis, state = analyze_crypto(symbol)
            analyses.append(analysis)
            if symbol == "BTCUSDT":
                btc_state = state
        except Exception as exc:
            analyses.append(_error_analysis(symbol, "Crypto", exc))

    if btc_state:
        analyses = [_apply_btc_filter(item, btc_state) if item.symbol == "ETHUSDT" else item for item in analyses]

    for symbol in equity_symbols:
        try:
            analyses.append(analyze_equity(symbol))
        except Exception as exc:
            analyses.append(_error_analysis(symbol, "US Equity", exc))

    return analyses


def analyze_crypto(symbol: str) -> tuple[ProtocolAnalysis, dict[str, Any]]:
    data = _load_crypto_market_data(symbol)
    k15 = data.k15
    k5 = data.k5
    k4 = data.k4
    k1d = data.k1d

    price = data.last_price
    c15 = _candle_state(k15)
    c4 = _candle_state(k4)
    c1d = _candle_state(k1d)
    strong_bullish = c15["structure"]["bos_up"] and c15["macd"]["hist"] > 0 and c15["cvd"]["delta"] > 0
    strong_bearish = c15["structure"]["bos_down"] and c15["macd"]["hist"] < 0 and c15["cvd"]["delta"] < 0
    state = {
        "strong_bullish": strong_bullish,
        "strong_bearish": strong_bearish,
        "price": price,
        "swing_high": c15["structure"]["last_swing_high"],
        "swing_low": c15["structure"]["last_swing_low"],
        "macd_hist": c15["macd"]["hist"],
        "cvd_delta": c15["cvd"]["delta"],
    }

    if symbol == "ETHUSDT":
        current_status, hit_status, suggestion_1, suggestion_2, key_levels = _eth_protocol(price, k5, c15, c4, c1d)
        final_instruction = _eth_final_instruction(price, c15)
    else:
        if strong_bullish:
            current_status = "BTC 15m 强反弹过滤器开启，短线风险偏好偏多。"
            hit_status = "命中：BTC_FILTER_STRONG_BULLISH"
        elif strong_bearish:
            current_status = "BTC 15m 强空过滤器开启，短线风险偏好偏空。"
            hit_status = "命中：BTC_FILTER_STRONG_BEARISH"
        else:
            current_status = "BTC 15m 未触发强多/强空，作为中性过滤器处理。"
            hit_status = "未命中 L3，只命中 BTC Filter 观察。"
        suggestion_1 = f"建议1：若 15m 继续站在 {c15['structure']['last_swing_high']:.2f} 上方，维持对 ETH 做空信号的降级/拦截。"
        suggestion_2 = f"建议2：若跌回 {c15['structure']['last_swing_low']:.2f} 下方且 MACD/CVD 同步转弱，再切换为风险收缩过滤。"
        key_levels = f"15m swing high {c15['structure']['last_swing_high']:.2f}；15m swing low {c15['structure']['last_swing_low']:.2f}"
        final_instruction = _btc_final_instruction(price, c15, c1d)

    evidence = [
        f"15m close {k15[-1]['close']:.2f}, ATR14 {c15['atr']:.2f}, MACD hist {c15['macd']['hist']:.2f}, CVD delta {_short_num(c15['cvd']['delta'])}",
        f"4h trend {c4['structure']['trend']}, 1d trend {c1d['structure']['trend']}",
        _crypto_24h_evidence(data),
        _crypto_derivatives_evidence(data),
    ]
    if data.fallback_notes:
        evidence.append("fallback " + "；".join(data.fallback_notes))

    return (
        ProtocolAnalysis(
            symbol=symbol,
            market=data.market,
            price=price,
            change_pct=data.change_pct,
            current_status=current_status,
            hit_status=hit_status,
            suggestion_1=suggestion_1,
            suggestion_2=suggestion_2,
            key_levels=key_levels,
            final_instruction=final_instruction,
            evidence=evidence,
            updated_at=k15[-1]["time"],
        ),
        state,
    )


def analyze_equity(symbol: str) -> ProtocolAnalysis:
    daily = _yahoo_chart(symbol, "1d", "1y")
    hourly = _yahoo_chart(symbol, "60m", "3mo")
    m15 = _yahoo_chart(symbol, "15m", "1mo")
    d = _candle_state(daily)
    h = _candle_state(hourly)
    q = _candle_state(m15)
    last = daily[-1]
    previous = daily[-2] if len(daily) > 1 else last
    change_pct = ((last["close"] / previous["close"]) - 1) * 100 if previous["close"] else None

    current_status, hit_status = _equity_status(d, h, q)
    swing_low = d["structure"]["last_swing_low"]
    swing_high = d["structure"]["last_swing_high"]
    intraday_reclaim = q["structure"]["last_swing_high"]
    intraday_fail = q["structure"]["last_swing_low"]
    suggestion_1 = (
        f"建议1：只在 15m/60m 收回 {intraday_reclaim:.2f} 且回踩不破 {intraday_fail:.2f} 后，考虑扫低反杀 L3。"
    )
    suggestion_2 = (
        f"建议2：若反抽 {swing_low:.2f}-{swing_high:.2f} 区间失败，优先按反抽失败/风险释放处理，不追高。"
    )
    key_levels = (
        f"日线 swing high {swing_high:.2f}；日线 swing low {swing_low:.2f}；"
        f"15m reclaim {intraday_reclaim:.2f}；15m fail {intraday_fail:.2f}"
    )
    evidence = [
        f"last close {last['close']:.2f}, day high/low {last['high']:.2f}/{last['low']:.2f}, volume {_short_num(last['volume'])}",
        f"1d ATR14 {d['atr']:.2f}, MACD hist {d['macd']['hist']:.2f}, CVD trend {d['cvd']['trend']}",
        f"60m trend {h['structure']['trend']}, 15m trend {q['structure']['trend']}",
    ]
    final_instruction = _equity_final_instruction(symbol, float(last["close"]), last, d, h, q)
    return ProtocolAnalysis(
        symbol=symbol,
        market="US Equity",
        price=float(last["close"]),
        change_pct=change_pct,
        current_status=current_status,
        hit_status=hit_status,
        suggestion_1=suggestion_1,
        suggestion_2=suggestion_2,
        key_levels=key_levels,
        final_instruction=final_instruction,
        evidence=evidence,
        updated_at=m15[-1]["time"],
    )


def format_protocol_section(analyses: list[ProtocolAnalysis]) -> list[str]:
    lines: list[str] = ["协议标的报告："]
    for item in analyses:
        change = "N/A" if item.change_pct is None else f"{item.change_pct:+.2f}%"
        lines.extend(
            [
                "",
                f"【{item.symbol}｜{item.market}】",
                f"价格：{item.price:.2f}（变动 {change}）",
                f"当前状态：{item.current_status}",
                f"是否命中：{item.hit_status}",
                item.suggestion_1,
                item.suggestion_2,
                f"关键位：{item.key_levels}",
                f"数据时间：{_format_data_time(item.updated_at)}",
                "最终交易指令：",
                *item.final_instruction,
                "证据：" + "；".join(item.evidence),
            ]
        )
    return lines


def _eth_protocol(price: float, k5: list[dict[str, float]], c15: dict[str, Any], c4: dict[str, Any], c1d: dict[str, Any]) -> tuple[str, str, str, str, str]:
    last_two_lows_ok = len(k5) >= 2 and min(k["low"] for k in k5[-2:]) >= 1600
    no_30m_fail = len(k5) >= 6 and all(k["close"] >= 1583 for k in k5[-6:])
    stand_1605_l3 = price > 1605 and last_two_lows_ok and no_30m_fail and c15["cvd"]["delta"] > 0
    cm2_l2 = 1583 <= price <= 1605
    cm3_l2 = any(k["low"] < 1544 for k in k5[-80:])
    flow = _eth_flow_label(c15)

    if price >= 1746:
        current_status = f"ETH 已越过 1746 延伸目标，短线进入高位延伸/兑现区，{flow}。"
        hit_status = "命中：1605 站稳后的延伸段；C-M2 做空失效；Macro 仍需 1982-2044 修复。"
        suggestion_1 = "建议1：不追多；只有回踩 1746/1665 不破且 MACD/CVD 重新转强，才看 1982-2044。"
        suggestion_2 = "建议2：若跌回 1746 且反抽不回，优先看 1665/1645 回测；跌破 1605 才重新评估 C-M2 空头。"
    elif price >= 1665:
        current_status = f"ETH 已突破 1645-1665 第二反弹目标区，进入 1665-1746 延伸确认段，{flow}。"
        hit_status = "命中：反弹目标区上破；C-M2 做空失效；暂无 Macro 修复。"
        suggestion_1 = "建议1：禁止追多；若回踩 1665 不破且 15m 动能转强，再看 1746。"
        suggestion_2 = "建议2：若跌回 1665 且反抽失败，先看 1645/1605 回测，不把回落直接当作 Macro 空头。"
    elif price >= 1645:
        current_status = f"ETH 正在 1645-1665 第二压力/反弹目标区内交易，{flow}。"
        hit_status = "命中：C-M2 目标/压力区；此处不再是低位追多点。"
        suggestion_1 = "建议1：若 15m 放量站上 1665 且回踩不破，再看 1746。"
        suggestion_2 = "建议2：若 1645-1665 反抽失败并跌回 1645，下方先看 1605/1583。"
    elif stand_1605_l3:
        current_status = f"ETH 从 1583-1605 第一压力区向上确认，短线反弹成立但 Macro 仍未修复，{flow}。"
        hit_status = "命中：ETH_STAND_ABOVE_1605 L3；同时 C-M2 做空失效。"
        suggestion_1 = "建议1：若 15m 连续守住 1605 且 5m low 守住 1600，短线反弹看 1645-1665。"
        suggestion_2 = "建议2：若跌回 1605 后反抽失败，先看 1583；跌回 1583 才重新考虑 C-M2 空头。"
    elif price > 1605 or c15["cvd"]["makes_new_high"]:
        current_status = f"ETH 位于 1605 上方第一反弹段，C-M2 反抽失败空暂不成立，{flow}。"
        hit_status = "命中：C-M2 L4 风险/失效；1605 站稳仍需动能确认。"
        suggestion_1 = "建议1：若 15m 连续收在 1605 上方且动能修复，才看 1645-1665。"
        suggestion_2 = "建议2：若跌回 1605/1583 且 BTC 不强、CVD 不创新高，再考虑反抽失败空。"
    elif cm2_l2:
        current_status = f"ETH 位于 1583-1605 第一反抽压力区，处于 C-M2 与 1605 站稳双观察，{flow}。"
        hit_status = "命中：C-M2 L2 / 1605 L2；暂无 L3。"
        suggestion_1 = "建议1：只有站回 1605 并回踩不破，才切换到短线反弹观察。"
        suggestion_2 = "建议2：若 1583 跌回并反抽不回，优先按 C-M2 反抽失败空处理。"
    elif cm3_l2 and price > 1570:
        current_status = f"ETH 已出现扫低后回收，但当前离 sweep low 较远，追多 R/R 需要复核，{flow}。"
        hit_status = "命中：C-M3 L2；暂无 L3。"
        suggestion_1 = "建议1：只有重新收回 1583/1605 且 BTC 不强空，才考虑扫低反杀升级。"
        suggestion_2 = "建议2：若跌回 sweep low 下方，C-M3 失效。"
    else:
        current_status = f"ETH 未命中核心 L3 条件，维持区间观察，{flow}。"
        hit_status = "未命中 L3。"
        suggestion_1 = "建议1：等待 1605 站回或 1544 扫低回收。"
        suggestion_2 = "建议2：跌破 1544 后反抽不回，优先防守。"

    key_levels = "1544 / 1583 / 1605 / 1645-1665 / 1746 / 1982-2044"
    if c4["structure"]["trend"] == "DOWN" or c1d["macd"]["hist"] < 0:
        current_status += " 4h/1d 仍未给出宏观反转。"
    return current_status, hit_status, suggestion_1, suggestion_2, key_levels


def _eth_final_instruction(price: float, c15: dict[str, Any]) -> list[str]:
    if price >= 1746:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止追多；1746 延伸目标已进入兑现区，只等回踩或继续放量确认。"
    elif price >= 1665:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止追多；1645-1665 目标区已兑现，等待 1665 回踩确认。"
    elif price >= 1645:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止直接追多；价格正在第二压力区，等站上 1665 或反抽失败。"
    elif price > 1605:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止追多；已进入 1605 上方确认区，等待回踩或失效信号。"
    elif 1583 <= price <= 1605:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止直接开仓；等待 1605 站回或 1583 跌回后的二次确认。"
    elif price < 1544:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止抄底；只观察扫低后是否重新收回 1570/1583。"
    else:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止直接开仓；Micro 等 C-M2/C-M3 触发。"
    atr = max(float(c15["atr"]), 1.0)
    levels = [1544, 1583, 1605, 1645, 1665, 1746, 1982, 2044]
    upside_targets = _format_targets([level for level in levels if level > price], fallback=price + atr)
    downside_targets = _format_targets([level for level in reversed(levels) if level < price], fallback=price - atr)
    pullback_level = 1665 if price >= 1665 else 1645 if price >= 1645 else 1605
    fail_level = 1645 if price >= 1665 else 1605 if price >= 1645 else 1583
    short_zone = "1746/1665" if price >= 1746 else "1665/1645" if price >= 1665 else "1645-1665" if price >= 1645 else "1583-1605"
    return [
        current,
        f"多头预警：回踩 {_fmt_price(pullback_level)} 附近不破，且 MACD/CVD 同步转强；上方目标看 {upside_targets}；跌回 {_fmt_price(fail_level)} 下方失效。",
        f"空头预警：{short_zone} 反抽失败，或跌回 {_fmt_price(fail_level)} 后反抽不回；下方目标看 {downside_targets}；重新站回 {_fmt_price(price + atr * 0.8)} 且 CVD 创新高失效。",
        "Macro 预警：1982-2044 收回才重新讨论宏观修复；跌破 1544 下方看 1500，极端看 1457。",
        "一句话结论：ETH 当前是动态反弹段，不再复读 1605；Micro 看回踩确认，Macro 仍未修复。",
    ]


def _btc_final_instruction(price: float, c15: dict[str, Any], c1d: dict[str, Any]) -> list[str]:
    swing_high = float(c15["structure"]["last_swing_high"])
    swing_low = float(c15["structure"]["last_swing_low"])
    atr = max(float(c15["atr"]), price * 0.002)
    macro_high = float(c1d["structure"]["last_swing_high"])
    macro_low = float(c1d["structure"]["last_swing_low"])
    reclaim_band = _price_band(swing_high, atr * 0.15)
    support_band = _range_text(max(swing_low, swing_high - atr * 0.6), swing_high)
    fail_band = _price_band(swing_low, atr * 0.15)
    target_1 = swing_high + atr
    target_2 = swing_high + atr * 2
    target_3 = swing_high + atr * 3
    down_1 = swing_low - atr
    down_2 = swing_low - atr * 2
    down_3 = swing_low - atr * 3
    return [
        f"当前指令：现价 {_fmt_price(price)}：BTC 不给独立开仓指令；只作为 ETH 风险过滤器。",
        f"多头预警：{reclaim_band} 站回；回踩 {support_band} 不破；ETH 空头降级；上方看 {_fmt_price(target_1)} / {_fmt_price(target_2)} / {_fmt_price(target_3)}；跌回 {fail_band} 失效。",
        f"空头预警：跌破 {fail_band} 后反抽不回；或 15m 反抽 {reclaim_band} 失败；ETH 多头降级；下方看 {_fmt_price(down_1)} / {_fmt_price(down_2)} / {_fmt_price(down_3)}。",
        f"Macro 预警：日线收回 {_fmt_price(macro_high)} 才重新讨论高周期偏多；跌破 {_fmt_price(macro_low)} 进入风险收缩。",
        "一句话结论：BTC 当前是 ETH 的风控阀门；不抢方向，只用来确认或否决 ETH Micro 信号。",
    ]


def _equity_final_instruction(
    symbol: str,
    price: float,
    last: dict[str, float],
    daily: dict[str, Any],
    hourly: dict[str, Any],
    m15: dict[str, Any],
) -> list[str]:
    intraday_reclaim = float(m15["structure"]["last_swing_high"])
    intraday_fail = float(m15["structure"]["last_swing_low"])
    day_high = float(last["high"])
    day_low = float(last["low"])
    daily_swing_high = float(daily["structure"]["last_swing_high"])
    daily_swing_low = float(daily["structure"]["last_swing_low"])
    intraday_atr = max(float(m15["atr"]), price * 0.01)
    daily_atr = max(float(daily["atr"]), price * 0.04)

    reclaim_center = max(intraday_reclaim, price)
    reclaim_band = _price_band(reclaim_center, max(intraday_atr * 0.25, price * 0.002))
    pullback_center = max(intraday_fail, min(price, reclaim_center - intraday_atr * 0.35))
    pullback_band = _price_band(pullback_center, max(intraday_atr * 0.2, price * 0.002))
    fail_level = min(intraday_fail, day_low)

    target_1 = max(reclaim_center + intraday_atr, day_high)
    target_2 = max(target_1 + daily_atr * 0.45, target_1 * 1.03)
    target_3 = max(target_2 + daily_atr * 0.65, daily_swing_high if daily_swing_high > target_2 else target_2 * 1.05)

    resistance_1 = max(reclaim_center, day_high)
    resistance_2 = max(resistance_1 + daily_atr * 0.55, daily_swing_low)
    down_1 = fail_level
    down_2 = max(0.01, down_1 - max(daily_atr * 0.55, price * 0.04))
    down_3 = max(0.01, down_1 - max(daily_atr * 1.8, price * 0.12))
    macro_watch = max(daily_swing_low, resistance_2)
    macro_reclaim = max(daily_swing_high, macro_watch + daily_atr * 1.2)

    if daily["structure"]["bos_down"]:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止直接开仓；破位后只等站回确认或反抽失败。"
        conclusion = f"一句话结论：{symbol} 当前不是抄底盘，而是破位后的流动性战场。Micro 等触发，Macro 先剔除。"
    elif daily["structure"]["bos_up"]:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止追高；趋势延续只等回踩确认。"
        conclusion = f"一句话结论：{symbol} 当前是趋势延续观察盘；只做回踩确认，不做情绪追价。"
    elif hourly["structure"]["bos_down"]:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止直接开仓；小时级别走弱，优先等反抽失败或重新站回。"
        conclusion = f"一句话结论：{symbol} 当前偏防守；Micro 可以观察，Macro 不加仓。"
    else:
        current = f"当前指令：现价 {_fmt_price(price)}：禁止直接开仓；区间内等待 L3 触发。"
        conclusion = f"一句话结论：{symbol} 当前是区间观察标的；Micro 等触发，Macro 暂不升级。"

    return [
        current,
        f"多头预警：{reclaim_band} 站回；回踩 {pullback_band} 不破；目标看 {_fmt_price(target_1)} / {_fmt_price(target_2)} / {_fmt_price(target_3)}；跌回 {_fmt_price(fail_level)} 下方失效。",
        f"空头预警：{_price_band(resistance_1, daily_atr * 0.18)} 反抽失败；或 {_price_band(resistance_2, daily_atr * 0.22)} 反抽失败；或跌破 {_fmt_price(fail_level)} 后反抽不回；目标看 {_fmt_price(down_1)} / {_fmt_price(down_2)} / {_fmt_price(down_3)}。",
        f"Macro 预警：{_price_band(macro_watch, daily_atr * 0.2)} 收回：进入观察池；{_price_band(macro_reclaim, daily_atr * 0.25)} 收回：才重新讨论中线多头；跌破 {_fmt_price(fail_level)}：下方看 {_fmt_price(down_2)}，极端看 {_fmt_price(down_3)}。",
        conclusion,
    ]


def _equity_status(daily: dict[str, Any], hourly: dict[str, Any], m15: dict[str, Any]) -> tuple[str, str]:
    if daily["structure"]["bos_down"]:
        if daily["cvd"]["makes_new_low"]:
            return "日线 BOS_DOWN 且资金流 proxy 创新低，处于破位风险状态。", "命中：L4 日线结构破位；暂无 L3。"
        return "日线 BOS_DOWN，结构已经破位但资金流未继续创新低，处于破位后反抽观察。", "命中：L4 日线结构破位；等待 L3 反抽确认。"
    if daily["structure"]["bos_up"] and daily["cvd"]["delta"] > 0:
        return "日线 BOS_UP 且成交量 proxy 支持，处于趋势延续观察。", "命中：L2 趋势延续观察；L3 需回踩确认。"
    if hourly["structure"]["bos_up"] and m15["cvd"]["delta"] > 0:
        return "小时级别尝试修复，短线进入反弹观察。", "命中：L2 反弹观察；暂无 L3。"
    if hourly["structure"]["bos_down"]:
        return "小时级别继续走弱，优先防守。", "命中：L4 小时结构走弱；暂无 L3。"
    return "多周期未形成强方向，处于观察区间。", "未命中 L3，只保留 L2 观察。"


def _apply_btc_filter(item: ProtocolAnalysis, btc_state: dict[str, Any]) -> ProtocolAnalysis:
    if btc_state.get("strong_bullish"):
        final_instruction = list(item.final_instruction)
        if len(final_instruction) > 2:
            final_instruction[2] += " BTC 强多期间，ETH 空头预警降级或过滤。"
        return ProtocolAnalysis(
            **{
                **item.__dict__,
                "current_status": item.current_status + " BTC strong bullish 已开启，ETH 做空信号需要降级或过滤。",
                "hit_status": item.hit_status + "；命中：BTC 强多过滤。",
                "final_instruction": final_instruction,
            }
        )
    if btc_state.get("strong_bearish"):
        final_instruction = list(item.final_instruction)
        if len(final_instruction) > 1:
            final_instruction[1] += " BTC 强空期间，ETH 多头预警降级或过滤。"
        return ProtocolAnalysis(
            **{
                **item.__dict__,
                "current_status": item.current_status + " BTC strong bearish 已开启，ETH 做多信号需要降级或过滤。",
                "hit_status": item.hit_status + "；命中：BTC 强空过滤。",
                "final_instruction": final_instruction,
            }
        )
    return item


def _candle_state(candles: list[dict[str, float]]) -> dict[str, Any]:
    return {
        "atr": _atr(candles),
        "macd": _macd(candles),
        "cvd": _cvd_proxy(candles),
        "structure": _structure(candles),
    }


def _binance_klines(symbol: str, interval: str, limit: int) -> list[dict[str, float]]:
    rows = _binance_get("/fapi/v1/klines", {"symbol": symbol, "interval": interval, "limit": limit})
    return [
        {
            "time": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
            "quote_volume": float(row[7]),
            "trade_count": int(row[8]),
            "taker_buy_volume": float(row[9]),
            "taker_buy_quote_volume": float(row[10]),
        }
        for row in rows
    ]


def _binance_get(path: str, params: dict[str, Any]) -> Any:
    base = get_settings().binance_rest_base or BINANCE_FAPI
    return _get_json(base.rstrip("/") + path, params)


def _load_crypto_market_data(symbol: str) -> CryptoMarketData:
    errors: list[str] = []
    loaders = [
        ("Binance USD-M", _load_binance_crypto),
        ("OKX SWAP", _load_okx_crypto),
        ("Yahoo spot", _load_yahoo_crypto),
    ]
    for name, loader in loaders:
        try:
            data = loader(symbol)
            return replace(data, fallback_notes=tuple(errors)) if errors else data
        except Exception as exc:
            errors.append(f"{name} failed: {_brief_error(exc)}")
    raise RuntimeError("; ".join(errors))


def _load_binance_crypto(symbol: str) -> CryptoMarketData:
    k15 = _binance_klines(symbol, "15m", 120)
    k5 = _binance_klines(symbol, "5m", 80)
    k4 = _binance_klines(symbol, "4h", 120)
    k1d = _binance_klines(symbol, "1d", 120)
    ticker = _binance_get("/fapi/v1/ticker/24hr", {"symbol": symbol})
    mark = _binance_get("/fapi/v1/premiumIndex", {"symbol": symbol})
    oi = _binance_get("/fapi/v1/openInterest", {"symbol": symbol})
    return CryptoMarketData(
        source="Binance USD-M",
        market="Crypto USD-M",
        k15=k15,
        k5=k5,
        k4=k4,
        k1d=k1d,
        last_price=float(ticker["lastPrice"]),
        change_pct=float(ticker["priceChangePercent"]),
        high_24h=float(ticker["highPrice"]),
        low_24h=float(ticker["lowPrice"]),
        mark_price=float(mark["markPrice"]),
        funding_rate=float(mark["lastFundingRate"]),
        open_interest=float(oi["openInterest"]),
    )


def _load_okx_crypto(symbol: str) -> CryptoMarketData:
    inst_id = _okx_symbol(symbol)
    k15 = _okx_candles(inst_id, "15m", 120)
    k5 = _okx_candles(inst_id, "5m", 80)
    k4 = _okx_candles(inst_id, "4H", 120)
    k1d = _okx_candles(inst_id, "1D", 120)
    ticker = _okx_get("/api/v5/market/ticker", {"instId": inst_id})["data"][0]
    funding = _okx_get("/api/v5/public/funding-rate", {"instId": inst_id})["data"][0]
    open_interest = _okx_get("/api/v5/public/open-interest", {"instType": "SWAP", "instId": inst_id})["data"][0]
    last = float(ticker["last"])
    open_24h = float(ticker["open24h"]) if ticker.get("open24h") else 0.0
    oi_value = open_interest.get("oiCcy") or open_interest.get("oi") or 0.0
    return CryptoMarketData(
        source="OKX SWAP",
        market="Crypto SWAP",
        k15=k15,
        k5=k5,
        k4=k4,
        k1d=k1d,
        last_price=last,
        change_pct=((last / open_24h) - 1) * 100 if open_24h else None,
        high_24h=float(ticker["high24h"]) if ticker.get("high24h") else None,
        low_24h=float(ticker["low24h"]) if ticker.get("low24h") else None,
        mark_price=last,
        funding_rate=float(funding["fundingRate"]) if funding.get("fundingRate") else None,
        open_interest=float(oi_value),
    )


def _load_yahoo_crypto(symbol: str) -> CryptoMarketData:
    yahoo_symbol = _yahoo_crypto_symbol(symbol)
    k15 = _yahoo_chart(yahoo_symbol, "15m", "5d")[-120:]
    k5 = _yahoo_chart(yahoo_symbol, "5m", "5d")[-80:]
    hourly = _yahoo_chart(yahoo_symbol, "60m", "3mo")
    k4 = _aggregate_candles(hourly, 4)[-120:]
    k1d = _yahoo_chart(yahoo_symbol, "1d", "1y")[-120:]
    day_window = k15[-96:] if len(k15) >= 96 else k15
    last = k15[-1]
    reference = day_window[0]
    return CryptoMarketData(
        source="Yahoo spot",
        market="Crypto Spot",
        k15=k15,
        k5=k5,
        k4=k4,
        k1d=k1d,
        last_price=float(last["close"]),
        change_pct=((last["close"] / reference["close"]) - 1) * 100 if reference["close"] else None,
        high_24h=max(candle["high"] for candle in day_window),
        low_24h=min(candle["low"] for candle in day_window),
    )


def _okx_get(path: str, params: dict[str, Any]) -> Any:
    base = get_settings().okx_rest_base or OKX_REST
    return _get_json(base.rstrip("/") + path, params)


def _okx_candles(inst_id: str, bar: str, limit: int) -> list[dict[str, Any]]:
    payload = _okx_get("/api/v5/market/candles", {"instId": inst_id, "bar": bar, "limit": limit})
    rows = payload.get("data") or []
    candles = [
        {
            "time": datetime.fromtimestamp(int(row[0]) / 1000, tz=timezone.utc).isoformat(),
            "open": float(row[1]),
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4]),
            "volume": float(row[5]),
        }
        for row in rows
    ]
    candles.sort(key=lambda item: item["time"])
    if not candles:
        raise ValueError(f"no OKX candle data for {inst_id} {bar}")
    return candles


def _okx_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USDT-SWAP"
    raise ValueError(f"unsupported OKX crypto symbol: {symbol}")


def _yahoo_crypto_symbol(symbol: str) -> str:
    if symbol.endswith("USDT"):
        return f"{symbol[:-4]}-USD"
    raise ValueError(f"unsupported Yahoo crypto symbol: {symbol}")


def _yahoo_chart(symbol: str, interval: str, range_: str) -> list[dict[str, float]]:
    base = get_settings().yahoo_chart_base or YAHOO_CHART
    result = _get_json(f"{base.rstrip('/')}/{symbol}", {"interval": interval, "range": range_})["chart"]["result"][0]
    timestamps = result.get("timestamp") or []
    if not timestamps:
        raise ValueError(f"no Yahoo chart data for {symbol}")
    quote = result["indicators"]["quote"][0]
    rows: list[dict[str, float]] = []
    for index, timestamp in enumerate(timestamps):
        close = quote["close"][index]
        if close is None:
            continue
        rows.append(
            {
                "time": datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat(),
                "open": float(quote["open"][index]),
                "high": float(quote["high"][index]),
                "low": float(quote["low"][index]),
                "close": float(close),
                "volume": float(quote["volume"][index] or 0),
            }
        )
    if not rows:
        raise ValueError(f"no Yahoo chart data for {symbol}")
    return rows


def _get_json(url: str, params: dict[str, Any]) -> Any:
    request_url = url + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(request_url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore").strip()
        detail = f": {body[:160]}" if body else ""
        raise RuntimeError(f"HTTP {exc.code}{detail}") from exc


def _crypto_24h_evidence(data: CryptoMarketData) -> str:
    change = "N/A" if data.change_pct is None else f"{data.change_pct:+.2f}%"
    high = "N/A" if data.high_24h is None else f"{data.high_24h:.2f}"
    low = "N/A" if data.low_24h is None else f"{data.low_24h:.2f}"
    return f"24h {change}, high/low {high}/{low}, source {data.source}"


def _crypto_derivatives_evidence(data: CryptoMarketData) -> str:
    if data.mark_price is None and data.funding_rate is None and data.open_interest is None:
        return "derivatives data unavailable on spot fallback"
    mark = "N/A" if data.mark_price is None else f"{data.mark_price:.2f}"
    funding = "N/A" if data.funding_rate is None else f"{data.funding_rate * 100:+.4f}%"
    oi = "N/A" if data.open_interest is None else _short_num(data.open_interest)
    return f"mark {mark}, funding {funding}, OI {oi}"


def _aggregate_candles(candles: list[dict[str, Any]], size: int) -> list[dict[str, Any]]:
    aggregated: list[dict[str, Any]] = []
    for index in range(0, len(candles), size):
        chunk = candles[index : index + size]
        if len(chunk) < size:
            continue
        item = {
            "time": chunk[-1]["time"],
            "open": chunk[0]["open"],
            "high": max(candle["high"] for candle in chunk),
            "low": min(candle["low"] for candle in chunk),
            "close": chunk[-1]["close"],
            "volume": sum(candle["volume"] for candle in chunk),
        }
        if any("quote_volume" in candle for candle in chunk):
            item["quote_volume"] = sum(candle.get("quote_volume", 0.0) for candle in chunk)
        if any("trade_count" in candle for candle in chunk):
            item["trade_count"] = sum(int(candle.get("trade_count", 0)) for candle in chunk)
        if any("taker_buy_volume" in candle for candle in chunk):
            item["taker_buy_volume"] = sum(candle.get("taker_buy_volume", 0.0) for candle in chunk)
        if any("taker_buy_quote_volume" in candle for candle in chunk):
            item["taker_buy_quote_volume"] = sum(candle.get("taker_buy_quote_volume", 0.0) for candle in chunk)
        aggregated.append(item)
    if not aggregated:
        raise ValueError("not enough candles to aggregate")
    return aggregated


def _brief_error(exc: Exception) -> str:
    message = str(exc).replace("\n", " ").strip()
    lowered = message.lower()
    if "http 451" in lowered:
        return "HTTP 451"
    if "timed out" in lowered or "timeout" in lowered:
        return "timeout"
    if "refused" in lowered or "10061" in lowered or "积极拒绝" in message or "无法连接" in message:
        return "connection refused"
    return message[:140] if message else exc.__class__.__name__


def _ema(values: list[float], period: int) -> list[float]:
    if not values:
        return []
    alpha = 2 / (period + 1)
    result = [values[0]]
    for value in values[1:]:
        result.append(value * alpha + result[-1] * (1 - alpha))
    return result


def _macd(candles: list[dict[str, float]]) -> dict[str, float]:
    closes = [candle["close"] for candle in candles]
    if len(closes) < 26:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0}
    macd_series = [fast - slow for fast, slow in zip(_ema(closes, 12), _ema(closes, 26))]
    signal = _ema(macd_series, 9)
    return {"macd": macd_series[-1], "signal": signal[-1], "hist": macd_series[-1] - signal[-1]}


def _atr(candles: list[dict[str, float]], period: int = 14) -> float:
    if len(candles) < 2:
        return 0.0
    true_ranges = []
    for previous, current in zip(candles, candles[1:]):
        true_ranges.append(
            max(
                current["high"] - current["low"],
                abs(current["high"] - previous["close"]),
                abs(current["low"] - previous["close"]),
            )
        )
    window = true_ranges[-period:]
    return sum(window) / len(window) if window else 0.0


def _cvd_proxy(candles: list[dict[str, float]], lookback: int = 20) -> dict[str, Any]:
    deltas = []
    for candle in candles[-lookback:]:
        if candle["close"] > candle["open"]:
            deltas.append(candle["volume"])
        elif candle["close"] < candle["open"]:
            deltas.append(-candle["volume"])
        else:
            deltas.append(0.0)
    total = 0.0
    values = []
    for delta in deltas:
        total += delta
        values.append(total)
    current = values[-1] if values else 0.0
    previous = values[:-1]
    return {
        "cvd": current,
        "delta": deltas[-1] if deltas else 0.0,
        "makes_new_high": bool(previous) and current > max(previous),
        "makes_new_low": bool(previous) and current < min(previous),
        "trend": "UP" if deltas and deltas[-1] > 0 else "DOWN" if deltas and deltas[-1] < 0 else "FLAT",
    }


def _structure(candles: list[dict[str, float]], lookback: int = 20) -> dict[str, Any]:
    if len(candles) < 3:
        return {"last_swing_high": 0.0, "last_swing_low": 0.0, "bos_up": False, "bos_down": False, "trend": "RANGE"}
    lookback = min(lookback, len(candles) - 1)
    window = candles[-lookback - 1 : -1]
    last = candles[-1]
    swing_high = max(candle["high"] for candle in window)
    swing_low = min(candle["low"] for candle in window)
    bos_up = last["close"] > swing_high
    bos_down = last["close"] < swing_low
    return {
        "last_swing_high": swing_high,
        "last_swing_low": swing_low,
        "bos_up": bos_up,
        "bos_down": bos_down,
        "trend": "UP" if bos_up else "DOWN" if bos_down else "RANGE",
    }


def _short_num(value: float) -> str:
    absolute = abs(value)
    if absolute >= 1_000_000_000:
        return f"{value / 1_000_000_000:.2f}B"
    if absolute >= 1_000_000:
        return f"{value / 1_000_000:.2f}M"
    if absolute >= 1_000:
        return f"{value / 1_000:.2f}K"
    return f"{value:.2f}"


def _fmt_price(value: float) -> str:
    return f"{value:.2f}"


def _price_band(center: float, half_width: float) -> str:
    width = max(float(half_width), abs(center) * 0.001)
    return _range_text(center - width, center + width)


def _range_text(first: float, second: float) -> str:
    low = min(first, second)
    high = max(first, second)
    return f"{low:.2f}-{high:.2f}"


def _format_targets(levels: list[float], fallback: float) -> str:
    unique: list[float] = []
    for level in levels:
        if not unique or abs(level - unique[-1]) > 0.01:
            unique.append(level)
    targets = unique[:3] or [fallback]
    return " / ".join(_fmt_price(level) for level in targets)


def _format_data_time(value: str) -> str:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    utc_time = parsed.astimezone(timezone.utc)
    local_time = utc_time.astimezone(LOCAL_TZ)
    age_seconds = max(0.0, (datetime.now(timezone.utc) - utc_time).total_seconds())
    if age_seconds >= 86400:
        age = f"{age_seconds / 86400:.1f} 天前"
    elif age_seconds >= 3600:
        age = f"{age_seconds / 3600:.1f} 小时前"
    else:
        age = f"{age_seconds / 60:.0f} 分钟前"
    return f"{local_time:%Y-%m-%d %H:%M:%S} Asia/Shanghai（{age}）"


def _eth_flow_label(c15: dict[str, Any]) -> str:
    macd_hist = float(c15["macd"]["hist"])
    cvd_delta = float(c15["cvd"]["delta"])
    if macd_hist > 0 and cvd_delta > 0:
        return "短线动能与资金流同步偏多"
    if macd_hist > 0 and cvd_delta <= 0:
        return "价格动能偏多但 CVD 未确认，存在高位分歧"
    if macd_hist <= 0 and cvd_delta > 0:
        return "CVD 有承接但价格动能尚未修复"
    return "MACD/CVD 同步偏弱，反弹质量需要复核"


def _iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _error_analysis(symbol: str, market: str, exc: Exception) -> ProtocolAnalysis:
    return ProtocolAnalysis(
        symbol=symbol,
        market=market,
        price=0.0,
        change_pct=None,
        current_status="数据拉取失败，暂不做协议判断。",
        hit_status="未命中：数据不足。",
        suggestion_1="建议1：下一轮自动化重试。",
        suggestion_2="建议2：若连续失败，检查网络或数据源接口。",
        key_levels="N/A",
        final_instruction=[
            "当前指令：数据不足，禁止开仓。",
            "多头预警：等待下一轮恢复行情后重新计算。",
            "空头预警：等待下一轮恢复行情后重新计算。",
            "Macro 预警：数据源未恢复前不做高周期判断。",
            "一句话结论：先修数据，再谈交易。",
        ],
        evidence=[f"error={exc}"],
        updated_at=_iso_now(),
    )
