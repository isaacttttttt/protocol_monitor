from __future__ import annotations

from copy import deepcopy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any
from uuid import uuid4

from app.config.settings import Settings
from app.review.protocol_analysis import (
    _aggregate_candles,
    _brief_error,
    _candle_state,
    _format_data_time,
    _load_crypto_market_data,
    _short_num,
    _yahoo_chart,
)

DEFAULT_CRYPTO_SYMBOLS = ["ETHUSDT", "BTCUSDT"]
DEFAULT_EQUITY_SYMBOLS = ["CRCL", "WDC", "ARM", "INTU", "INFQ"]
EQUITY_CONTEXT_SYMBOLS = ["SPY", "QQQ", "IWM", "XLK", "SMH"]


def build_indicator_snapshot(system_config: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    watchlist = resolve_watchlist(system_config, settings)
    crypto_symbols = watchlist["crypto_symbols"]
    equity_symbols = watchlist["equity_symbols"]
    equity_context_symbols = watchlist["equity_context_symbols"]

    snapshot: dict[str, Any] = {
        "schema_version": 3,
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "indicator_snapshot_only",
        "watchlist": watchlist,
        "indicator_quality": {
            "real_cvd": "unavailable; cvd_proxy uses candle direction x volume",
            "cluster_delta": "unavailable from current public data connectors",
            "liquidation_heatmap": "unavailable from current public data connectors",
            "options_flow": "unavailable from current public data connectors",
            "gamma_exposure": "unavailable from current public data connectors",
            "volume_profile": "approximated from candle typical price and candle volume",
            "delta_flow": "proxy only; uses close location value, candle body direction and volume",
            "smart_money": "proxy only; FVG/order-block/displacement/liquidity pools inferred from OHLCV",
        },
        "protocol_indicator_coverage": {
            "crypto": [
                "SMC structure",
                "ATR",
                "MACD",
                "squeeze",
                "VWAP",
                "anchored VWAP",
                "volume profile POC/HVN/LVN",
                "CVD proxy",
                "delta flow proxy",
                "CVD divergence",
                "absorption / effort-result proxy",
                "FVG / order block / displacement proxy",
                "equal highs/lows liquidity pools",
                "delta volume profile",
                "NVI",
                "funding",
                "open interest",
                "basis proxy",
                "liquidity sweep proxy",
            ],
            "equity": [
                "SMC structure",
                "ATR",
                "MACD",
                "squeeze",
                "VWAP",
                "anchored VWAP",
                "volume profile POC/HVN/LVN",
                "CVD proxy",
                "delta flow proxy",
                "CVD divergence",
                "absorption / effort-result proxy",
                "FVG / order block / displacement proxy",
                "equal highs/lows liquidity pools",
                "delta volume profile",
                "OBV",
                "A/D line",
                "NVI",
                "relative volume",
                "relative strength vs SPY",
                "index and sector context",
                "liquidity sweep proxy",
            ],
        },
        "contexts": {},
        "symbols": {"crypto": [], "equity": []},
    }

    for symbol in crypto_symbols:
        snapshot["symbols"]["crypto"].append(_safe_build(lambda: _build_crypto_snapshot(str(symbol)), str(symbol), "crypto"))

    equity_context = _build_equity_context(equity_context_symbols)
    snapshot["contexts"]["equity"] = equity_context["context"]
    spy_daily = equity_context.get("spy_daily") or []
    for symbol in equity_symbols:
        snapshot["symbols"]["equity"].append(
            _safe_build(lambda symbol=symbol: _build_equity_snapshot(str(symbol), spy_daily), str(symbol), "equity")
        )

    return _clean(snapshot)


def resolve_watchlist(system_config: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    report_config = system_config.get("report", {})
    crypto_symbols, crypto_source = _configured_symbols(
        settings.watchlist_crypto_symbols if settings else "",
        report_config.get("crypto_symbols"),
        DEFAULT_CRYPTO_SYMBOLS,
    )
    equity_symbols, equity_source = _configured_symbols(
        settings.watchlist_equity_symbols if settings else "",
        report_config.get("equity_symbols"),
        DEFAULT_EQUITY_SYMBOLS,
    )
    equity_context_symbols, context_source = _configured_symbols(
        settings.equity_context_symbols if settings else "",
        report_config.get("equity_context_symbols"),
        EQUITY_CONTEXT_SYMBOLS,
    )
    return {
        "crypto_symbols": crypto_symbols,
        "crypto_symbols_source": crypto_source,
        "equity_symbols": equity_symbols,
        "equity_symbols_source": equity_source,
        "equity_context_symbols": equity_context_symbols,
        "equity_context_symbols_source": context_source,
    }


async def archive_indicator_snapshot(
    snapshot: dict[str, Any],
    settings: Settings,
    archive_repository: Any | None = None,
) -> None:
    if archive_repository is not None:
        await archive_repository.save_snapshot(snapshot)

    if not settings.indicator_archive_path:
        return
    path = Path(settings.indicator_archive_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as file:
        file.write(json.dumps(snapshot, ensure_ascii=False, separators=(",", ":")) + "\n")


def summarize_snapshot_for_report(snapshot: dict[str, Any]) -> list[str]:
    lines = [
        f"指标快照：run_id={snapshot.get('run_id')}",
        f"生成时间：{_format_data_time(str(snapshot.get('generated_at', '')))}",
    ]
    for market in ("crypto", "equity"):
        for item in snapshot.get("symbols", {}).get(market, []):
            if item.get("status") != "ok":
                lines.append(f"- {item.get('symbol')}：数据失败，error={item.get('error')}")
                continue
            price = item.get("price")
            change = item.get("change_pct")
            change_text = "N/A" if change is None else f"{change:+.2f}%"
            quality = ",".join(item.get("unavailable", [])[:3])
            lines.append(f"- {item.get('symbol')}：price={price:.2f} change={change_text} source={item.get('source')} missing={quality or 'none'}")
    return lines


def compact_snapshot_for_llm(snapshot: dict[str, Any]) -> dict[str, Any]:
    compact = deepcopy(snapshot)
    compact["llm_payload_note"] = (
        "This is a compact copy for DeepSeek. Full indicator snapshots are archived locally/database; "
        "volume profile bins and other bulky raw details are omitted here."
    )
    _drop_heavy_fields(compact)
    return compact


def _safe_build(builder: Any, symbol: str, market: str) -> dict[str, Any]:
    try:
        return builder()
    except Exception as exc:
        return {
            "symbol": symbol,
            "market": market,
            "status": "error",
            "error": _brief_error(exc),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


def _drop_heavy_fields(value: Any) -> None:
    if isinstance(value, dict):
        value.pop("bins", None)
        for item in value.values():
            _drop_heavy_fields(item)
    elif isinstance(value, list):
        for item in value:
            _drop_heavy_fields(item)


def _build_crypto_snapshot(symbol: str) -> dict[str, Any]:
    data = _load_crypto_market_data(symbol)
    timeframes = {
        "5m": _indicator_pack(data.k5, "5m"),
        "15m": _indicator_pack(data.k15, "15m"),
        "4h": _indicator_pack(data.k4, "4h"),
        "1d": _indicator_pack(data.k1d, "1d"),
    }
    basis = None
    if data.mark_price is not None and data.last_price:
        basis = ((float(data.mark_price) / float(data.last_price)) - 1) * 100
    return {
        "symbol": symbol,
        "market": "crypto",
        "status": "ok",
        "source": data.source,
        "market_type": data.market,
        "price": data.last_price,
        "change_pct": data.change_pct,
        "high_24h": data.high_24h,
        "low_24h": data.low_24h,
        "updated_at": data.k15[-1]["time"],
        "updated_at_local": _format_data_time(data.k15[-1]["time"]),
        "derivatives": {
            "mark_price": data.mark_price,
            "funding_rate": data.funding_rate,
            "funding_rate_pct": data.funding_rate * 100 if data.funding_rate is not None else None,
            "open_interest": data.open_interest,
            "open_interest_text": _short_num(float(data.open_interest)) if data.open_interest is not None else None,
            "basis_pct": basis,
        },
        "timeframes": timeframes,
        "fallback_notes": list(data.fallback_notes),
        "unavailable": [
            "real_cvd",
            "cluster_delta",
            "liquidation_heatmap",
            "long_short_ratio",
        ],
    }


def _build_equity_snapshot(symbol: str, spy_daily: list[dict[str, Any]]) -> dict[str, Any]:
    daily = _yahoo_chart(symbol, "1d", "1y")
    hourly = _yahoo_chart(symbol, "60m", "3mo")
    m15 = _yahoo_chart(symbol, "15m", "1mo")
    try:
        weekly = _yahoo_chart(symbol, "1wk", "5y")
    except Exception:
        weekly = _aggregate_candles(daily, 5)

    latest_intraday = m15[-1]
    last_daily = daily[-1]
    previous_daily = daily[-2] if len(daily) > 1 else last_daily
    price = float(latest_intraday["close"])
    change_pct = ((price / previous_daily["close"]) - 1) * 100 if previous_daily["close"] else None
    gap_pct = ((latest_intraday["open"] / previous_daily["close"]) - 1) * 100 if previous_daily["close"] else None
    rs_vs_spy = _relative_strength_vs_spy(daily, spy_daily, 20)

    return {
        "symbol": symbol,
        "market": "equity",
        "status": "ok",
        "source": "Yahoo Finance chart",
        "price": price,
        "change_pct": change_pct,
        "last_daily_close": last_daily["close"],
        "gap_pct": gap_pct,
        "updated_at": latest_intraday["time"],
        "updated_at_local": _format_data_time(latest_intraday["time"]),
        "opening_range_30m": _opening_range(m15),
        "relative_strength_vs_spy_20d_pct": rs_vs_spy,
        "timeframes": {
            "15m": _indicator_pack(m15, "15m"),
            "60m": _indicator_pack(hourly, "60m"),
            "1d": _indicator_pack(daily, "1d"),
            "1wk": _indicator_pack(weekly, "1wk"),
        },
        "unavailable": [
            "real_cvd",
            "options_flow",
            "gamma_exposure",
            "dark_pool_prints",
        ],
    }


def _build_equity_context(symbols: list[str]) -> dict[str, Any]:
    context: dict[str, Any] = {}
    spy_daily: list[dict[str, Any]] = []
    for symbol in symbols:
        try:
            daily = _yahoo_chart(symbol, "1d", "1y")
            hourly = _yahoo_chart(symbol, "60m", "3mo")
            if symbol == "SPY":
                spy_daily = daily
            context[symbol] = {
                "status": "ok",
                "price": daily[-1]["close"],
                "change_pct": _return_pct(daily[-2]["close"], daily[-1]["close"]) if len(daily) > 1 else None,
                "daily": _indicator_pack(daily, "1d", include_profile=False),
                "60m": _indicator_pack(hourly, "60m", include_profile=False),
            }
        except Exception as exc:
            context[symbol] = {"status": "error", "error": _brief_error(exc)}
    return {"context": context, "spy_daily": spy_daily}


def _configured_symbols(env_value: str, yaml_value: Any, default: list[str]) -> tuple[list[str], str]:
    env_symbols = _parse_symbol_list(env_value)
    if env_symbols:
        return env_symbols, "env"
    yaml_symbols = _normalize_symbol_list(yaml_value)
    if yaml_symbols:
        return yaml_symbols, "yaml"
    return list(default), "default"


def _parse_symbol_list(value: str) -> list[str]:
    if not value:
        return []
    normalized = value.replace(";", ",").replace("\n", ",").replace("\t", ",")
    parts: list[str] = []
    for chunk in normalized.split(","):
        parts.extend(chunk.split())
    return _normalize_symbol_list(parts)


def _normalize_symbol_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        return _parse_symbol_list(value)
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        symbol = str(item).strip().upper()
        if not symbol or symbol in seen:
            continue
        seen.add(symbol)
        result.append(symbol)
    return result


def _indicator_pack(candles: list[dict[str, Any]], timeframe: str, include_profile: bool = True) -> dict[str, Any]:
    if not candles:
        return {"timeframe": timeframe, "status": "empty"}
    state = _candle_state(candles)
    closes = [float(candle["close"]) for candle in candles]
    volumes = [float(candle.get("volume") or 0.0) for candle in candles]
    last = candles[-1]
    atr = float(state["atr"])
    volume_sma20 = _sma(volumes, 20)
    relative_volume = volumes[-1] / volume_sma20 if volume_sma20 else None
    vwap = _vwap(candles)
    smart_money = _smart_money_pack(candles, atr)
    delta_flow = _delta_flow_pack(candles)
    volume_profile = _volume_profile(candles) if include_profile else None
    pack = {
        "timeframe": timeframe,
        "bar_count": len(candles),
        "last_bar": _last_bar(last),
        "returns_pct": {
            "1_bar": _lookback_return(closes, 1),
            "3_bar": _lookback_return(closes, 3),
            "10_bar": _lookback_return(closes, 10),
            "20_bar": _lookback_return(closes, 20),
        },
        "structure": state["structure"],
        "atr14": atr,
        "atr14_pct": atr / closes[-1] * 100 if closes[-1] else None,
        "macd": state["macd"],
        "rsi14": _rsi(closes),
        "squeeze": _squeeze(candles),
        "vwap": vwap,
        "anchored_vwap": {
            "from_recent_low": _anchored_vwap(candles, "low"),
            "from_recent_high": _anchored_vwap(candles, "high"),
        },
        "volume": {
            "last": volumes[-1],
            "sma20": volume_sma20,
            "relative_volume": relative_volume,
        },
        "flow": {
            "cvd_proxy": state["cvd"],
            "obv": _obv(candles),
            "ad_line": _ad_line(candles),
            "nvi": _nvi(candles),
            "delta_flow": delta_flow,
        },
        "liquidity": _liquidity_proxy(candles),
        "smart_money": smart_money,
    }
    if volume_profile is not None:
        pack["volume_profile"] = volume_profile
        pack["volume_delta_profile"] = _volume_delta_profile(candles)
    pack["confluence"] = _confluence_pack(candles, state, atr, vwap, volume_profile, delta_flow, smart_money)
    return pack


def _last_bar(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": candle["time"],
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": float(candle.get("volume") or 0.0),
    }


def _sma(values: list[float], period: int) -> float | None:
    if not values:
        return None
    window = values[-period:]
    return sum(window) / len(window)


def _return_pct(start: float, end: float) -> float | None:
    return ((end / start) - 1) * 100 if start else None


def _lookback_return(values: list[float], lookback: int) -> float | None:
    if len(values) <= lookback:
        return None
    return _return_pct(values[-lookback - 1], values[-1])


def _rsi(closes: list[float], period: int = 14) -> float | None:
    if len(closes) <= period:
        return None
    gains = []
    losses = []
    for previous, current in zip(closes[-period - 1 : -1], closes[-period:]):
        change = current - previous
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))
    average_gain = sum(gains) / period
    average_loss = sum(losses) / period
    if average_loss == 0:
        return 100.0
    rs = average_gain / average_loss
    return 100 - (100 / (1 + rs))


def _vwap(candles: list[dict[str, Any]]) -> float | None:
    total_pv = 0.0
    total_volume = 0.0
    for candle in candles:
        volume = float(candle.get("volume") or 0.0)
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        total_pv += typical * volume
        total_volume += volume
    return total_pv / total_volume if total_volume else None


def _anchored_vwap(candles: list[dict[str, Any]], anchor: str, lookback: int = 60) -> float | None:
    window = candles[-lookback:] if len(candles) > lookback else candles
    if not window:
        return None
    if anchor == "low":
        anchor_index = min(range(len(window)), key=lambda index: float(window[index]["low"]))
    else:
        anchor_index = max(range(len(window)), key=lambda index: float(window[index]["high"]))
    return _vwap(window[anchor_index:])


def _obv(candles: list[dict[str, Any]]) -> dict[str, float | str | None]:
    value = 0.0
    series = [value]
    for previous, current in zip(candles, candles[1:]):
        volume = float(current.get("volume") or 0.0)
        if current["close"] > previous["close"]:
            value += volume
        elif current["close"] < previous["close"]:
            value -= volume
        series.append(value)
    slope = series[-1] - series[-6] if len(series) >= 6 else None
    return {"value": value, "slope_5": slope, "trend": _trend_label(slope)}


def _ad_line(candles: list[dict[str, Any]]) -> dict[str, float | str | None]:
    value = 0.0
    series = []
    for candle in candles:
        high = float(candle["high"])
        low = float(candle["low"])
        close = float(candle["close"])
        volume = float(candle.get("volume") or 0.0)
        multiplier = ((close - low) - (high - close)) / (high - low) if high != low else 0.0
        value += multiplier * volume
        series.append(value)
    slope = series[-1] - series[-6] if len(series) >= 6 else None
    return {"value": value, "slope_5": slope, "trend": _trend_label(slope)}


def _nvi(candles: list[dict[str, Any]]) -> dict[str, float | str | None]:
    value = 1000.0
    series = [value]
    for previous, current in zip(candles, candles[1:]):
        previous_volume = float(previous.get("volume") or 0.0)
        current_volume = float(current.get("volume") or 0.0)
        previous_close = float(previous["close"])
        current_close = float(current["close"])
        if current_volume < previous_volume and previous_close:
            value *= 1 + ((current_close / previous_close) - 1)
        series.append(value)
    slope = series[-1] - series[-11] if len(series) >= 11 else None
    return {"value": value, "slope_10": slope, "trend": _trend_label(slope)}


def _delta_flow_pack(candles: list[dict[str, Any]], lookback: int = 50) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    if not window:
        return {"status": "empty"}
    rows = [_delta_row(candle) for candle in window]
    deltas = [row["hybrid_delta"] for row in rows]
    cvd = _cumulative(deltas)
    last_row = rows[-1]
    last_delta = deltas[-1]
    delta_sma5 = _sma(deltas, 5)
    delta_sma20 = _sma(deltas, 20)
    delta_zscore = _zscore(last_delta, deltas[-20:])
    slope_5 = _slope(cvd, 5)
    slope_20 = _slope(cvd, 20)
    previous_slope_5 = _previous_slope(cvd, 5)
    acceleration = slope_5 - previous_slope_5 if slope_5 is not None and previous_slope_5 is not None else None
    return {
        "method": "hybrid proxy: 60% close-location delta + 40% candle-body delta",
        "last": {
            "close_location_value": last_row["clv"],
            "body_ratio": last_row["body_ratio"],
            "buy_volume_proxy": last_row["buy_volume_proxy"],
            "sell_volume_proxy": last_row["sell_volume_proxy"],
            "clv_delta": last_row["clv_delta"],
            "body_delta": last_row["body_delta"],
            "signed_volume_delta": last_row["signed_volume_delta"],
            "hybrid_delta": last_delta,
            "imbalance_ratio": _safe_div(last_delta, last_row["volume"]),
        },
        "delta_sma5": delta_sma5,
        "delta_sma20": delta_sma20,
        "delta_zscore20": delta_zscore,
        "cumulative_delta_20": sum(deltas[-20:]) if deltas else 0.0,
        "cumulative_delta_50": cvd[-1] if cvd else 0.0,
        "cvd_slope_5": slope_5,
        "cvd_slope_20": slope_20,
        "cvd_acceleration": acceleration,
        "cvd_trend": _trend_label(slope_5),
        "stacked_delta": _stacked_delta(deltas),
        "divergence": _cvd_divergence(window, cvd),
        "absorption": _absorption_pack(window, rows, delta_zscore),
    }


def _delta_row(candle: dict[str, Any]) -> dict[str, float]:
    open_ = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    volume = float(candle.get("volume") or 0.0)
    full_range = max(high - low, 1e-12)
    clv = _clamp(((close - low) - (high - close)) / full_range, -1.0, 1.0)
    body_ratio = _clamp((close - open_) / full_range, -1.0, 1.0)
    signed_volume_delta = volume if close > open_ else -volume if close < open_ else 0.0
    clv_delta = clv * volume
    body_delta = body_ratio * volume
    hybrid_delta = clv_delta * 0.6 + body_delta * 0.4
    buy_volume = volume * _clamp((clv + 1) / 2, 0.0, 1.0)
    return {
        "volume": volume,
        "clv": clv,
        "body_ratio": body_ratio,
        "clv_delta": clv_delta,
        "body_delta": body_delta,
        "signed_volume_delta": signed_volume_delta,
        "hybrid_delta": hybrid_delta,
        "buy_volume_proxy": buy_volume,
        "sell_volume_proxy": volume - buy_volume,
    }


def _absorption_pack(candles: list[dict[str, Any]], delta_rows: list[dict[str, float]], delta_zscore: float | None) -> dict[str, Any]:
    if not candles:
        return {"status": "empty"}
    last = candles[-1]
    last_delta = delta_rows[-1]["hybrid_delta"]
    volumes = [float(candle.get("volume") or 0.0) for candle in candles]
    ranges = [max(float(candle["high"]) - float(candle["low"]), 1e-12) for candle in candles]
    returns = [abs(_return_pct(float(previous["close"]), float(current["close"])) or 0.0) for previous, current in zip(candles, candles[1:])]
    volume_rel = _safe_div(volumes[-1], _sma(volumes, 20) or 0.0)
    range_rel = _safe_div(ranges[-1], median(ranges[-20:]) if ranges else 0.0)
    close_position = _close_position(last)
    wick = _wick_ratios(last)
    median_abs_return = median(returns[-20:]) if returns else 0.0
    last_abs_return = returns[-1] if returns else 0.0
    buy_absorption = bool((delta_zscore or 0.0) > 1.0 and close_position < 0.45 and wick["upper_wick_ratio"] > 0.35)
    sell_absorption = bool((delta_zscore or 0.0) < -1.0 and close_position > 0.55 and wick["lower_wick_ratio"] > 0.35)
    return {
        "volume_rel20": volume_rel,
        "range_rel20": range_rel,
        "close_position": close_position,
        "upper_wick_ratio": wick["upper_wick_ratio"],
        "lower_wick_ratio": wick["lower_wick_ratio"],
        "effort_no_result": bool((volume_rel or 0.0) > 1.5 and last_abs_return <= median_abs_return),
        "stopping_volume": bool((volume_rel or 0.0) > 1.8 and (range_rel or 0.0) < 0.75),
        "climax_volume": bool((volume_rel or 0.0) > 2.0 and (range_rel or 0.0) > 1.35),
        "buy_absorption_proxy": buy_absorption,
        "sell_absorption_proxy": sell_absorption,
        "absorption_label": "BUY_ABSORBED" if buy_absorption else "SELL_ABSORBED" if sell_absorption else "NONE",
        "last_delta": last_delta,
    }


def _cvd_divergence(candles: list[dict[str, Any]], cvd: list[float], lookback: int = 20) -> dict[str, Any]:
    if len(candles) <= 3 or len(cvd) <= 3:
        return {"status": "insufficient_data"}
    span = min(lookback, len(candles) - 1)
    previous_candles = candles[-span - 1 : -1]
    previous_cvd = cvd[-span - 1 : -1]
    last = candles[-1]
    price_higher_high = float(last["high"]) > max(float(candle["high"]) for candle in previous_candles)
    price_lower_low = float(last["low"]) < min(float(candle["low"]) for candle in previous_candles)
    cvd_lower_high = cvd[-1] < max(previous_cvd) if previous_cvd else False
    cvd_higher_low = cvd[-1] > min(previous_cvd) if previous_cvd else False
    return {
        "lookback": span,
        "bearish_regular": bool(price_higher_high and cvd_lower_high),
        "bullish_regular": bool(price_lower_low and cvd_higher_low),
        "price_higher_high": price_higher_high,
        "price_lower_low": price_lower_low,
        "cvd_lower_high": cvd_lower_high,
        "cvd_higher_low": cvd_higher_low,
        "current_cvd": cvd[-1],
    }


def _smart_money_pack(candles: list[dict[str, Any]], atr: float) -> dict[str, Any]:
    return {
        "displacement": _displacement_pack(candles),
        "fair_value_gaps": _fvg_pack(candles, atr),
        "order_blocks": _order_block_pack(candles),
        "liquidity_pools": _liquidity_pools_pack(candles, atr),
        "premium_discount": _premium_discount_pack(candles),
    }


def _displacement_pack(candles: list[dict[str, Any]], lookback: int = 40) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    if not window:
        return {"status": "empty"}
    ranges = [max(float(candle["high"]) - float(candle["low"]), 1e-12) for candle in window]
    volumes = [float(candle.get("volume") or 0.0) for candle in window]
    avg_range = sum(ranges) / len(ranges)
    volume_sma = _sma(volumes, 20) or 0.0
    events = []
    for candle in window:
        direction = _displacement_direction(candle, avg_range, volume_sma)
        if direction == "NONE":
            continue
        full_range = max(float(candle["high"]) - float(candle["low"]), 1e-12)
        body = abs(float(candle["close"]) - float(candle["open"]))
        events.append(
            {
                "time": candle["time"],
                "direction": direction,
                "body_to_range": body / full_range,
                "range_rel": full_range / avg_range if avg_range else None,
                "volume_rel20": _safe_div(float(candle.get("volume") or 0.0), volume_sma),
            }
        )
    return {
        "count": len(events),
        "last": events[-1] if events else None,
        "recent": events[-5:],
    }


def _fvg_pack(candles: list[dict[str, Any]], atr: float, lookback: int = 80) -> dict[str, Any]:
    if len(candles) < 3:
        return {"active": [], "recent": []}
    start = max(2, len(candles) - lookback)
    gaps = []
    for index in range(start, len(candles)):
        left = candles[index - 2]
        current = candles[index]
        left_high = float(left["high"])
        left_low = float(left["low"])
        current_high = float(current["high"])
        current_low = float(current["low"])
        if current_low > left_high:
            gap = _fvg_record("bullish", candles, index, left_high, current_low, atr)
            gaps.append(gap)
        if current_high < left_low:
            gap = _fvg_record("bearish", candles, index, current_high, left_low, atr)
            gaps.append(gap)
    active = [gap for gap in gaps if not gap["fully_mitigated"]]
    return {"active": active[-5:], "recent": gaps[-5:]}


def _fvg_record(direction: str, candles: list[dict[str, Any]], index: int, gap_low: float, gap_high: float, atr: float) -> dict[str, Any]:
    later = candles[index + 1 :]
    if direction == "bullish":
        touched = any(float(candle["low"]) <= gap_high for candle in later)
        fully_mitigated = any(float(candle["low"]) <= gap_low for candle in later)
    else:
        touched = any(float(candle["high"]) >= gap_low for candle in later)
        fully_mitigated = any(float(candle["high"]) >= gap_high for candle in later)
    width = gap_high - gap_low
    return {
        "time": candles[index]["time"],
        "direction": direction,
        "gap_low": gap_low,
        "gap_high": gap_high,
        "mid": (gap_low + gap_high) / 2,
        "width": width,
        "width_atr": _safe_div(width, atr),
        "touched": touched,
        "fully_mitigated": fully_mitigated,
    }


def _order_block_pack(candles: list[dict[str, Any]], lookback: int = 80) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    if len(window) < 5:
        return {"bullish_recent": [], "bearish_recent": []}
    ranges = [max(float(candle["high"]) - float(candle["low"]), 1e-12) for candle in window]
    volumes = [float(candle.get("volume") or 0.0) for candle in window]
    avg_range = sum(ranges) / len(ranges)
    volume_sma = _sma(volumes, 20) or 0.0
    bullish = []
    bearish = []
    for index, candle in enumerate(window):
        direction = _displacement_direction(candle, avg_range, volume_sma)
        if direction == "BULLISH":
            block = _find_prior_opposite_candle(window, index, want_bearish=True)
            if block:
                bullish.append(_order_block_record(block, window, index, "bullish"))
        elif direction == "BEARISH":
            block = _find_prior_opposite_candle(window, index, want_bearish=False)
            if block:
                bearish.append(_order_block_record(block, window, index, "bearish"))
    return {
        "bullish_recent": bullish[-3:],
        "bearish_recent": bearish[-3:],
        "last_bullish": bullish[-1] if bullish else None,
        "last_bearish": bearish[-1] if bearish else None,
    }


def _find_prior_opposite_candle(candles: list[dict[str, Any]], index: int, want_bearish: bool) -> dict[str, Any] | None:
    for candidate in reversed(candles[max(0, index - 8) : index]):
        is_bearish = float(candidate["close"]) < float(candidate["open"])
        if is_bearish == want_bearish:
            return candidate
    return None


def _order_block_record(block: dict[str, Any], candles: list[dict[str, Any]], displacement_index: int, direction: str) -> dict[str, Any]:
    zone_low = float(block["low"])
    zone_high = float(block["high"])
    later = candles[displacement_index + 1 :]
    mitigated = any(float(candle["low"]) <= zone_high and float(candle["high"]) >= zone_low for candle in later)
    return {
        "time": block["time"],
        "direction": direction,
        "zone_low": zone_low,
        "zone_high": zone_high,
        "mid": (zone_low + zone_high) / 2,
        "mitigated": mitigated,
    }


def _liquidity_pools_pack(candles: list[dict[str, Any]], atr: float, lookback: int = 80) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    if len(window) < 5:
        return {"status": "insufficient_data"}
    close = float(window[-1]["close"])
    tolerance = max(atr * 0.12, close * 0.001)
    high_pools = _level_pools([float(candle["high"]) for candle in window], tolerance, close, minimum_touches=2)
    low_pools = _level_pools([float(candle["low"]) for candle in window], tolerance, close, minimum_touches=2)
    highs_above = [pool for pool in high_pools if pool["level"] > close]
    lows_below = [pool for pool in low_pools if pool["level"] < close]
    return {
        "tolerance": tolerance,
        "nearest_equal_high_above": min(highs_above, key=lambda item: item["distance_to_price"]) if highs_above else None,
        "nearest_equal_low_below": min(lows_below, key=lambda item: item["distance_to_price"]) if lows_below else None,
        "equal_highs": high_pools[-5:],
        "equal_lows": low_pools[-5:],
        "range_20_high": max(float(candle["high"]) for candle in window[-20:]),
        "range_20_low": min(float(candle["low"]) for candle in window[-20:]),
        "range_50_high": max(float(candle["high"]) for candle in window[-50:]),
        "range_50_low": min(float(candle["low"]) for candle in window[-50:]),
    }


def _premium_discount_pack(candles: list[dict[str, Any]], lookback: int = 60) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    high = max(float(candle["high"]) for candle in window)
    low = min(float(candle["low"]) for candle in window)
    close = float(window[-1]["close"])
    equilibrium = (high + low) / 2
    position = _safe_div(close - low, high - low)
    label = "PREMIUM" if position is not None and position > 0.66 else "DISCOUNT" if position is not None and position < 0.33 else "EQUILIBRIUM"
    return {
        "lookback": len(window),
        "range_high": high,
        "range_low": low,
        "equilibrium": equilibrium,
        "position_0_to_1": position,
        "label": label,
    }


def _volume_delta_profile(candles: list[dict[str, Any]], bins: int = 12, lookback: int = 120) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    lows = [float(candle["low"]) for candle in window]
    highs = [float(candle["high"]) for candle in window]
    low = min(lows)
    high = max(highs)
    if math.isclose(low, high):
        return {"delta_poc": low, "positive_delta_poc": low, "negative_delta_poc": low, "bins": []}
    step = (high - low) / bins
    buckets = [
        {"low": low + step * index, "high": low + step * (index + 1), "volume": 0.0, "delta": 0.0}
        for index in range(bins)
    ]
    for candle in window:
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        index = min(bins - 1, max(0, int((typical - low) / step)))
        row = _delta_row(candle)
        buckets[index]["volume"] += row["volume"]
        buckets[index]["delta"] += row["hybrid_delta"]
    abs_sorted = sorted(buckets, key=lambda bucket: abs(bucket["delta"]), reverse=True)
    positive = [bucket for bucket in buckets if bucket["delta"] > 0]
    negative = [bucket for bucket in buckets if bucket["delta"] < 0]
    return {
        "lookback_bars": len(window),
        "delta_poc": _bucket_mid(abs_sorted[0]) if abs_sorted else None,
        "positive_delta_poc": _bucket_mid(max(positive, key=lambda bucket: bucket["delta"])) if positive else None,
        "negative_delta_poc": _bucket_mid(min(negative, key=lambda bucket: bucket["delta"])) if negative else None,
        "net_delta": sum(bucket["delta"] for bucket in buckets),
        "dominant_delta_zones": [
            {"mid": _bucket_mid(bucket), "delta": bucket["delta"], "volume": bucket["volume"]}
            for bucket in abs_sorted[:3]
        ],
        "bins": buckets,
    }


def _confluence_pack(
    candles: list[dict[str, Any]],
    state: dict[str, Any],
    atr: float,
    vwap: float | None,
    volume_profile: dict[str, Any] | None,
    delta_flow: dict[str, Any],
    smart_money: dict[str, Any],
) -> dict[str, Any]:
    close = float(candles[-1]["close"])
    poc = volume_profile.get("poc") if volume_profile else None
    flags: list[str] = []
    divergence = delta_flow.get("divergence", {})
    absorption = delta_flow.get("absorption", {})
    displacement = smart_money.get("displacement", {})
    premium_discount = smart_money.get("premium_discount", {})
    if divergence.get("bearish_regular"):
        flags.append("bearish_cvd_divergence_proxy")
    if divergence.get("bullish_regular"):
        flags.append("bullish_cvd_divergence_proxy")
    if absorption.get("buy_absorption_proxy"):
        flags.append("buy_absorption_proxy")
    if absorption.get("sell_absorption_proxy"):
        flags.append("sell_absorption_proxy")
    if absorption.get("stopping_volume"):
        flags.append("stopping_volume_proxy")
    if displacement.get("last"):
        flags.append(f"last_displacement_{displacement['last']['direction'].lower()}")
    if premium_discount.get("label") in {"PREMIUM", "DISCOUNT"}:
        flags.append(f"price_in_{str(premium_discount['label']).lower()}")
    structure_trend = state["structure"]["trend"]
    flow_trend = delta_flow.get("cvd_trend")
    return {
        "price_vs_vwap_pct": _return_pct(vwap, close) if vwap else None,
        "price_vs_volume_poc_pct": _return_pct(float(poc), close) if poc is not None else None,
        "price_vs_atr_from_poc": _safe_div(close - float(poc), atr) if poc is not None else None,
        "structure_flow_alignment": (
            "ALIGNED_UP" if structure_trend == "UP" and flow_trend == "UP" else
            "ALIGNED_DOWN" if structure_trend == "DOWN" and flow_trend == "DOWN" else
            "DIVERGENT"
        ),
        "ai_attention_flags": flags,
    }


def _cumulative(values: list[float]) -> list[float]:
    total = 0.0
    result = []
    for value in values:
        total += value
        result.append(total)
    return result


def _slope(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    return (values[-1] - values[-period - 1]) / period


def _previous_slope(values: list[float], period: int) -> float | None:
    if len(values) <= period * 2:
        return None
    return (values[-period - 1] - values[-period * 2 - 1]) / period


def _zscore(value: float, values: list[float]) -> float | None:
    if len(values) < 3:
        return None
    mean = sum(values) / len(values)
    std = pstdev(values)
    if std == 0:
        return 0.0
    return (value - mean) / std


def _stacked_delta(deltas: list[float]) -> dict[str, Any]:
    if not deltas:
        return {"direction": "NONE", "count": 0}
    direction = "BUY" if deltas[-1] > 0 else "SELL" if deltas[-1] < 0 else "NONE"
    count = 0
    for value in reversed(deltas):
        current = "BUY" if value > 0 else "SELL" if value < 0 else "NONE"
        if current != direction:
            break
        count += 1
    return {"direction": direction, "count": count}


def _displacement_direction(candle: dict[str, Any], avg_range: float, volume_sma: float) -> str:
    open_ = float(candle["open"])
    close = float(candle["close"])
    full_range = max(float(candle["high"]) - float(candle["low"]), 1e-12)
    body_to_range = abs(close - open_) / full_range
    close_position = _close_position(candle)
    range_rel = full_range / avg_range if avg_range else 0.0
    volume_rel = _safe_div(float(candle.get("volume") or 0.0), volume_sma) or 0.0
    if body_to_range >= 0.55 and range_rel >= 1.15 and volume_rel >= 1.05:
        if close > open_ and close_position >= 0.7:
            return "BULLISH"
        if close < open_ and close_position <= 0.3:
            return "BEARISH"
    return "NONE"


def _level_pools(levels: list[float], tolerance: float, current_price: float, minimum_touches: int = 2) -> list[dict[str, Any]]:
    pools: list[dict[str, Any]] = []
    for level in sorted(levels):
        for pool in pools:
            if abs(level - pool["level"]) <= tolerance:
                pool["values"].append(level)
                pool["level"] = sum(pool["values"]) / len(pool["values"])
                pool["touches"] = len(pool["values"])
                break
        else:
            pools.append({"level": level, "values": [level], "touches": 1})
    result = []
    for pool in pools:
        if pool["touches"] >= minimum_touches:
            result.append(
                {
                    "level": pool["level"],
                    "touches": pool["touches"],
                    "distance_to_price": abs(pool["level"] - current_price),
                }
            )
    return result


def _close_position(candle: dict[str, Any]) -> float:
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    return _safe_div(close - low, high - low) or 0.5


def _wick_ratios(candle: dict[str, Any]) -> dict[str, float]:
    open_ = float(candle["open"])
    high = float(candle["high"])
    low = float(candle["low"])
    close = float(candle["close"])
    full_range = max(high - low, 1e-12)
    return {
        "upper_wick_ratio": (high - max(open_, close)) / full_range,
        "lower_wick_ratio": (min(open_, close) - low) / full_range,
    }


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return numerator / denominator


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _trend_label(slope: float | None) -> str:
    if slope is None:
        return "UNKNOWN"
    if slope > 0:
        return "UP"
    if slope < 0:
        return "DOWN"
    return "FLAT"


def _squeeze(candles: list[dict[str, Any]], period: int = 20) -> dict[str, Any]:
    if len(candles) < period + 1:
        return {"status": "insufficient_data"}
    closes = [float(candle["close"]) for candle in candles[-period:]]
    highs = [float(candle["high"]) for candle in candles[-period:]]
    lows = [float(candle["low"]) for candle in candles[-period:]]
    sma = sum(closes) / period
    std = pstdev(closes)
    bollinger_upper = sma + 2 * std
    bollinger_lower = sma - 2 * std
    true_ranges = [high - low for high, low in zip(highs, lows)]
    atr_proxy = sum(true_ranges) / len(true_ranges)
    keltner_upper = sma + 1.5 * atr_proxy
    keltner_lower = sma - 1.5 * atr_proxy
    bollinger_width = bollinger_upper - bollinger_lower
    keltner_width = keltner_upper - keltner_lower
    momentum = closes[-1] - sma
    return {
        "squeeze_on": bollinger_width < keltner_width,
        "bollinger_width_pct": bollinger_width / sma * 100 if sma else None,
        "keltner_width_pct": keltner_width / sma * 100 if sma else None,
        "momentum": momentum,
        "momentum_state": _trend_label(momentum),
    }


def _liquidity_proxy(candles: list[dict[str, Any]], lookback: int = 20) -> dict[str, Any]:
    if len(candles) <= lookback:
        return {"status": "insufficient_data"}
    previous = candles[-lookback - 1 : -1]
    last = candles[-1]
    previous_high = max(float(candle["high"]) for candle in previous)
    previous_low = min(float(candle["low"]) for candle in previous)
    high = float(last["high"])
    low = float(last["low"])
    close = float(last["close"])
    open_ = float(last["open"])
    full_range = max(high - low, 1e-12)
    upper_wick = high - max(open_, close)
    lower_wick = min(open_, close) - low
    return {
        "swept_recent_high_and_closed_back_inside": high > previous_high and close < previous_high,
        "swept_recent_low_and_closed_back_inside": low < previous_low and close > previous_low,
        "previous_range_high": previous_high,
        "previous_range_low": previous_low,
        "upper_wick_ratio": upper_wick / full_range,
        "lower_wick_ratio": lower_wick / full_range,
    }


def _volume_profile(candles: list[dict[str, Any]], bins: int = 12, lookback: int = 120) -> dict[str, Any]:
    window = candles[-lookback:] if len(candles) > lookback else candles
    lows = [float(candle["low"]) for candle in window]
    highs = [float(candle["high"]) for candle in window]
    low = min(lows)
    high = max(highs)
    if math.isclose(low, high):
        return {"poc": low, "hvn": [low], "lvn": [low], "bins": []}
    step = (high - low) / bins
    buckets = [{"low": low + step * index, "high": low + step * (index + 1), "volume": 0.0} for index in range(bins)]
    for candle in window:
        typical = (float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3
        index = min(bins - 1, max(0, int((typical - low) / step)))
        buckets[index]["volume"] += float(candle.get("volume") or 0.0)
    sorted_by_volume = sorted(buckets, key=lambda bucket: bucket["volume"], reverse=True)
    poc_bucket = sorted_by_volume[0]
    lvn_buckets = sorted(buckets, key=lambda bucket: bucket["volume"])[:3]
    return {
        "lookback_bars": len(window),
        "poc": _bucket_mid(poc_bucket),
        "hvn": [_bucket_mid(bucket) for bucket in sorted_by_volume[:3]],
        "lvn": [_bucket_mid(bucket) for bucket in lvn_buckets],
        "value_area_proxy": {
            "low": min(bucket["low"] for bucket in sorted_by_volume[: max(1, bins // 2)]),
            "high": max(bucket["high"] for bucket in sorted_by_volume[: max(1, bins // 2)]),
        },
        "bins": buckets,
    }


def _bucket_mid(bucket: dict[str, float]) -> float:
    return (bucket["low"] + bucket["high"]) / 2


def _opening_range(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candles:
        return None
    latest_day = str(candles[-1]["time"])[:10]
    session = [candle for candle in candles if str(candle["time"])[:10] == latest_day]
    if not session:
        return None
    first_bars = session[:2]
    return {
        "date": latest_day,
        "high": max(float(candle["high"]) for candle in first_bars),
        "low": min(float(candle["low"]) for candle in first_bars),
        "bar_count": len(first_bars),
    }


def _relative_strength_vs_spy(candles: list[dict[str, Any]], spy_daily: list[dict[str, Any]], lookback: int) -> float | None:
    symbol_return = _lookback_return([float(candle["close"]) for candle in candles], lookback)
    spy_return = _lookback_return([float(candle["close"]) for candle in spy_daily], lookback) if spy_daily else None
    if symbol_return is None or spy_return is None:
        return None
    return symbol_return - spy_return


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_clean(item) for item in value]
    if isinstance(value, float):
        if not math.isfinite(value):
            return None
        return round(value, 6)
    return value
