from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from typing import Any

from app.config.settings import get_settings

BINANCE_FAPI = "https://fapi.binance.com"
OKX_REST = "https://www.okx.com"
YAHOO_CHART = "https://query1.finance.yahoo.com/v8/finance/chart"


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
    crypto_symbols = crypto_symbols or ["ETHUSDT", "BTCUSDT"]
    equity_symbols = equity_symbols or ["CRCL", "WDC", "ARM"]
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
            evidence=evidence,
            updated_at=_iso_now(),
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
        evidence=evidence,
        updated_at=last["time"],
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
                "证据：" + "；".join(item.evidence),
            ]
        )
    return lines


def _eth_protocol(price: float, k5: list[dict[str, float]], c15: dict[str, Any], c4: dict[str, Any], c1d: dict[str, Any]) -> tuple[str, str, str, str, str]:
    last_two_lows_ok = len(k5) >= 2 and min(k["low"] for k in k5[-2:]) >= 1600
    no_30m_fail = len(k5) >= 6 and all(k["close"] >= 1583 for k in k5[-6:])
    stand_1605_l3 = price > 1605 and last_two_lows_ok and no_30m_fail and c15["cvd"]["delta"] > 0
    cm2_l4 = price > 1605 or c15["cvd"]["makes_new_high"]
    cm2_l2 = 1583 <= price <= 1605
    cm3_l2 = any(k["low"] < 1544 for k in k5[-80:])

    if stand_1605_l3:
        current_status = "ETH 正在从 1583-1605 第一压力区向上确认，短线反弹成立但 Macro 仍未修复。"
        hit_status = "命中：ETH_STAND_ABOVE_1605 L3；同时 C-M2 做空失效。"
    elif cm2_l4:
        current_status = "ETH 正在测试/站上 1605，CVD 偏强，反抽失败空不成立。"
        hit_status = "命中：C-M2 L4 风险/失效；1605 站稳仍需下一根确认。"
    elif cm2_l2:
        current_status = "ETH 位于 1583-1605 第一反抽压力区，处于 C-M2 与 1605 站稳双观察。"
        hit_status = "命中：C-M2 L2 / 1605 L2；暂无 L3。"
    elif cm3_l2 and price > 1570:
        current_status = "ETH 已出现扫低后回收，但当前离 sweep low 较远，追多 R/R 需要复核。"
        hit_status = "命中：C-M3 L2；暂无 L3。"
    else:
        current_status = "ETH 未命中核心 L3 条件，维持区间观察。"
        hit_status = "未命中 L3。"

    suggestion_1 = "建议1：若 15m 连续收在 1605 上方且 5m low 守住 1600，按短线反弹看 1645-1665。"
    suggestion_2 = "建议2：若跌回 1583 下方且 BTC 不强、CVD 不创新高、R/R>=1.5，再考虑 C-M2 反抽失败空。"
    key_levels = "1544 / 1583 / 1605 / 1645-1665 / 1982"
    if c4["structure"]["trend"] == "DOWN" or c1d["macd"]["hist"] < 0:
        current_status += " 4h/1d 仍未给出宏观反转。"
    return current_status, hit_status, suggestion_1, suggestion_2, key_levels


def _equity_status(daily: dict[str, Any], hourly: dict[str, Any], m15: dict[str, Any]) -> tuple[str, str]:
    if daily["structure"]["bos_down"] and daily["cvd"]["makes_new_low"]:
        return "日线 BOS_DOWN 且资金流 proxy 创新低，处于破位风险状态。", "命中：L4 日线结构破位；暂无 L3。"
    if daily["structure"]["bos_up"] and daily["cvd"]["delta"] > 0:
        return "日线 BOS_UP 且成交量 proxy 支持，处于趋势延续观察。", "命中：L2 趋势延续观察；L3 需回踩确认。"
    if hourly["structure"]["bos_up"] and m15["cvd"]["delta"] > 0:
        return "小时级别尝试修复，短线进入反弹观察。", "命中：L2 反弹观察；暂无 L3。"
    if hourly["structure"]["bos_down"]:
        return "小时级别继续走弱，优先防守。", "命中：L4 小时结构走弱；暂无 L3。"
    return "多周期未形成强方向，处于观察区间。", "未命中 L3，只保留 L2 观察。"


def _apply_btc_filter(item: ProtocolAnalysis, btc_state: dict[str, Any]) -> ProtocolAnalysis:
    if btc_state.get("strong_bullish"):
        return ProtocolAnalysis(
            **{
                **item.__dict__,
                "current_status": item.current_status + " BTC strong bullish 已开启，ETH 做空信号需要降级或过滤。",
                "hit_status": item.hit_status + "；命中：BTC 强多过滤。",
            }
        )
    if btc_state.get("strong_bearish"):
        return ProtocolAnalysis(
            **{
                **item.__dict__,
                "current_status": item.current_status + " BTC strong bearish 已开启，ETH 做多信号需要降级或过滤。",
                "hit_status": item.hit_status + "；命中：BTC 强空过滤。",
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
    timestamps = result["timestamp"]
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
        aggregated.append(
            {
                "time": chunk[-1]["time"],
                "open": chunk[0]["open"],
                "high": max(candle["high"] for candle in chunk),
                "low": min(candle["low"] for candle in chunk),
                "close": chunk[-1]["close"],
                "volume": sum(candle["volume"] for candle in chunk),
            }
        )
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
        evidence=[f"error={exc}"],
        updated_at=_iso_now(),
    )
