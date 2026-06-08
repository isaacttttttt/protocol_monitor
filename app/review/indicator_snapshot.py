from __future__ import annotations

from copy import deepcopy
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import pstdev
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


def build_indicator_snapshot(system_config: dict[str, Any]) -> dict[str, Any]:
    report_config = system_config.get("report", {})
    crypto_symbols = report_config.get("crypto_symbols") or DEFAULT_CRYPTO_SYMBOLS
    equity_symbols = report_config.get("equity_symbols") or DEFAULT_EQUITY_SYMBOLS

    snapshot: dict[str, Any] = {
        "schema_version": 2,
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "indicator_snapshot_only",
        "indicator_quality": {
            "real_cvd": "unavailable; cvd_proxy uses candle direction x volume",
            "cluster_delta": "unavailable from current public data connectors",
            "liquidation_heatmap": "unavailable from current public data connectors",
            "options_flow": "unavailable from current public data connectors",
            "gamma_exposure": "unavailable from current public data connectors",
            "volume_profile": "approximated from candle typical price and candle volume",
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

    equity_context = _build_equity_context()
    snapshot["contexts"]["equity"] = equity_context["context"]
    spy_daily = equity_context.get("spy_daily") or []
    for symbol in equity_symbols:
        snapshot["symbols"]["equity"].append(
            _safe_build(lambda symbol=symbol: _build_equity_snapshot(str(symbol), spy_daily), str(symbol), "equity")
        )

    return _clean(snapshot)


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


def _build_equity_context() -> dict[str, Any]:
    context: dict[str, Any] = {}
    spy_daily: list[dict[str, Any]] = []
    for symbol in EQUITY_CONTEXT_SYMBOLS:
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
        "vwap": _vwap(candles),
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
        },
        "liquidity": _liquidity_proxy(candles),
    }
    if include_profile:
        pack["volume_profile"] = _volume_profile(candles)
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
