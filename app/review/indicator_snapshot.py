from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from statistics import median, pstdev
from typing import Any, Iterator
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.config.settings import Settings
from app.review.protocol_analysis import (
    _aggregate_candles,
    _brief_error,
    _candle_state,
    _closed_yahoo_candles,
    _format_data_time,
    _load_crypto_market_data,
    _short_num,
    _yahoo_chart,
)
from app.review.equity_sectors import load_equity_sector_map, required_equity_context_symbols
from app.strategies.equity_orb_retest import OrbRetestConfig, evaluate_orb_retest

DEFAULT_CRYPTO_SYMBOLS = ["ETHUSDT", "BTCUSDT"]
DEFAULT_EQUITY_SYMBOLS = ["SOXL", "MU", "CRCL", "WDC", "ARM", "INTU", "INFQ"]
EQUITY_CONTEXT_SYMBOLS = [
    "SPY",
    "QQQ",
    "IWM",
    "DIA",
    "XLK",
    "SMH",
    "SOXX",
    "^VIX",
    "^TNX",
    "DX-Y.NYB",
]
NEW_YORK_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class IndicatorSnapshotEvent:
    snapshot: dict[str, Any]
    market: str
    symbol: str
    item: dict[str, Any]


def build_indicator_snapshot(system_config: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    watchlist = resolve_watchlist(system_config, settings)
    crypto_symbols = watchlist["crypto_symbols"]
    equity_symbols = watchlist["equity_symbols"]
    equity_context_symbols = watchlist["equity_context_symbols"]
    snapshot = _snapshot_base(watchlist)

    for symbol in crypto_symbols:
        snapshot["symbols"]["crypto"].append(_safe_build(lambda: _build_crypto_snapshot(str(symbol)), str(symbol), "crypto"))

    sector_map = load_equity_sector_map()
    required_context = required_equity_context_symbols(equity_symbols, equity_context_symbols, sector_map)
    equity_context = _build_equity_context(required_context)
    snapshot["contexts"]["equity"] = equity_context["context"]
    spy_daily = equity_context.get("spy_daily") or []
    for symbol in equity_symbols:
        snapshot["symbols"]["equity"].append(
            _safe_build(
                lambda symbol=symbol: _build_equity_snapshot(
                    str(symbol),
                    spy_daily,
                    sector_map.get(str(symbol).upper()),
                    equity_context.get("daily_candles", {}),
                    system_config.get("report", {}).get("orb_retest", {}),
                ),
                str(symbol),
                "equity",
            )
        )

    return _clean(snapshot)


def iter_indicator_snapshot_events(system_config: dict[str, Any], settings: Settings | None = None) -> Iterator[IndicatorSnapshotEvent]:
    watchlist = resolve_watchlist(system_config, settings)
    crypto_symbols = watchlist["crypto_symbols"]
    equity_symbols = watchlist["equity_symbols"]
    equity_context_symbols = watchlist["equity_context_symbols"]
    snapshot = _snapshot_base(watchlist)

    crypto_context: dict[str, dict[str, Any]] = {}
    if "BTCUSDT" in crypto_symbols:
        crypto_context["BTCUSDT"] = _clean(_safe_build(lambda: _build_crypto_snapshot("BTCUSDT"), "BTCUSDT", "crypto"))
        snapshot["contexts"]["crypto"] = {"BTCUSDT": deepcopy(crypto_context["BTCUSDT"])}

    for symbol in crypto_symbols:
        item = crypto_context.get(symbol)
        if item is None:
            item = _clean(_safe_build(lambda symbol=symbol: _build_crypto_snapshot(str(symbol)), str(symbol), "crypto"))
        snapshot["symbols"]["crypto"].append(item)
        yield IndicatorSnapshotEvent(snapshot=snapshot, market="crypto", symbol=str(symbol), item=item)

    sector_map = load_equity_sector_map()
    required_context = required_equity_context_symbols(equity_symbols, equity_context_symbols, sector_map)
    equity_context = _build_equity_context(required_context)
    snapshot["contexts"]["equity"] = _clean(equity_context["context"])
    spy_daily = equity_context.get("spy_daily") or []
    for symbol in equity_symbols:
        item = _clean(
            _safe_build(
                lambda symbol=symbol: _build_equity_snapshot(
                    str(symbol),
                    spy_daily,
                    sector_map.get(str(symbol).upper()),
                    equity_context.get("daily_candles", {}),
                    system_config.get("report", {}).get("orb_retest", {}),
                ),
                str(symbol),
                "equity",
            )
        )
        snapshot["symbols"]["equity"].append(item)
        yield IndicatorSnapshotEvent(snapshot=snapshot, market="equity", symbol=str(symbol), item=item)

def compact_symbol_snapshot_for_llm(
    snapshot: dict[str, Any],
    market: str,
    item: dict[str, Any],
    recent_signals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    compact = deepcopy(snapshot)
    compact["mode"] = "single_symbol_protocol_analysis"
    compact["symbols"] = {"crypto": [], "equity": []}
    compact["symbols"][market] = [deepcopy(item)]
    compact["llm_payload_note"] = (
        "This payload contains one target symbol plus required market context. Full multi-symbol snapshots are "
        "archived locally/database; bulky volume profile bins and raw details are omitted here."
    )
    if recent_signals is not None:
        monitor_window = compact.setdefault("monitor_window", {})
        symbol = str(item.get("symbol", ""))
        monitor_window["recent_signals_for_symbol"] = [
            signal for signal in recent_signals if str(signal.get("symbol", "")).upper() == symbol.upper()
        ]
    if market == "equity":
        _filter_equity_context_for_target(compact, item)
    _drop_heavy_fields(compact)
    return _clean(compact)


def _snapshot_base(watchlist: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": 3,
        "run_id": datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + uuid4().hex[:8],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mode": "indicator_snapshot_only",
        "factor_contract": {
            "code_role": "observations_factors_and_candidate_setups_only",
            "llm_role": "final_protocol_scores_micro_macro_judgment_and_trade_decision",
            "warning": "Candidate setup fields are evidence, not confirmed triggers.",
        },
        "watchlist": watchlist,
        "indicator_quality": {
            "real_cvd": "full footprint CVD unavailable; Binance crypto uses taker buy/sell delta when available, other sources use OHLCV proxy",
            "binance_taker_delta": "available for Binance USD-M crypto candles; taker_buy_volume - taker_sell_volume",
            "cluster_delta": "unavailable from current public data connectors",
            "liquidation_heatmap": "unavailable from current public data connectors",
            "options_flow": "unavailable from current public data connectors",
            "gamma_exposure": "unavailable from current public data connectors",
            "volume_profile": "approximated from candle typical price and candle volume",
            "delta_flow": "taker buy/sell when available; otherwise close location value, candle body direction and volume",
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
        "This is a compact copy for the configured LLM. Full indicator snapshots are archived locally/database; "
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


def _filter_equity_context_for_target(snapshot: dict[str, Any], item: dict[str, Any]) -> None:
    context = snapshot.get("contexts", {}).get("equity")
    if not isinstance(context, dict):
        return
    classification = item.get("classification") or {}
    keep = {
        "SPY",
        "QQQ",
        "IWM",
        "DIA",
        "^VIX",
        "^TNX",
        "DX-Y.NYB",
        str(classification.get("primary_benchmark") or "").upper(),
        *[str(symbol).upper() for symbol in classification.get("secondary_benchmarks", [])],
    }
    snapshot["contexts"]["equity"] = {symbol: value for symbol, value in context.items() if symbol in keep}


def _build_crypto_snapshot(symbol: str) -> dict[str, Any]:
    data = _load_crypto_market_data(symbol)
    timeframes = {
        "5m": _indicator_pack(data.k5, "5m", market="crypto"),
        "15m": _indicator_pack(data.k15, "15m", market="crypto"),
        "4h": _indicator_pack(data.k4, "4h", market="crypto"),
        "1d": _indicator_pack(data.k1d, "1d", market="crypto"),
    }
    basis = None
    if data.mark_price is not None and data.last_price:
        basis = ((float(data.mark_price) / float(data.last_price)) - 1) * 100
    derivatives = {
        "mark_price": data.mark_price,
        "funding_rate": data.funding_rate,
        "funding_rate_pct": data.funding_rate * 100 if data.funding_rate is not None else None,
        "open_interest": data.open_interest,
        "open_interest_text": _short_num(float(data.open_interest)) if data.open_interest is not None else None,
        "basis_pct": basis,
    }
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
        "derivatives": derivatives,
        "timeframes": timeframes,
        "protocol_setup_candidates": _crypto_protocol_candidates(timeframes, derivatives),
        "fallback_notes": list(data.fallback_notes),
        "unavailable": [
            "real_cvd",
            "cluster_delta",
            "liquidation_heatmap",
            "long_short_ratio",
        ],
    }


def _crypto_protocol_candidates(
    timeframes: dict[str, dict[str, Any]],
    derivatives: dict[str, Any],
) -> dict[str, Any]:
    m15 = timeframes["15m"]
    h4 = timeframes["4h"]
    daily = timeframes["1d"]
    return {
        "contract": "Observed evidence for protocol matching. LLM must decide score, state, direction and trigger.",
        "micro": {
            "C-M1_lvn_expansion": {
                "structure_15m": m15.get("structure"),
                "structure_4h": h4.get("structure"),
                "volume_profile_15m": m15.get("volume_profile"),
                "flow_15m": _compact_flow_evidence(m15),
                "volatility_transition_15m": m15.get("setup_candidates", {}).get("volatility_transition"),
            },
            "C-M2_pullback_or_retest_failure": {
                "structure_4h": h4.get("structure"),
                "price_vs_vwap_15m_pct": m15.get("confluence", {}).get("price_vs_vwap_pct"),
                "price_vs_poc_15m_pct": m15.get("confluence", {}).get("price_vs_volume_poc_pct"),
                "flow_divergence_15m": m15.get("flow", {}).get("delta_flow", {}).get("divergence"),
            },
            "C-M3_liquidity_sweep": {
                "liquidity_15m": m15.get("liquidity"),
                "absorption_15m": m15.get("flow", {}).get("delta_flow", {}).get("absorption"),
                "liquidity_pools_15m": m15.get("smart_money", {}).get("liquidity_pools"),
            },
            "C-M4_funding_oi_squeeze": {
                "funding_rate_pct": derivatives.get("funding_rate_pct"),
                "open_interest": derivatives.get("open_interest"),
                "basis_pct": derivatives.get("basis_pct"),
                "oi_history_available": False,
                "flow_15m": _compact_flow_evidence(m15),
            },
        },
        "macro": {
            "C-W1_btc_trend_follow": {
                "daily_structure": daily.get("structure"),
                "structure_4h": h4.get("structure"),
                "daily_ema_alignment": daily.get("factors", {}).get("trend", {}).get("ema_alignment"),
                "funding_rate_pct": derivatives.get("funding_rate_pct"),
            },
            "C-W2_alt_beta_rotation": {
                "daily_structure": daily.get("structure"),
                "eth_btc_data_available": False,
                "btc_dominance_data_available": False,
            },
            "C-W3_daily_absorption_reversal": {
                "daily_premium_discount": daily.get("smart_money", {}).get("premium_discount"),
                "daily_flow_divergence": daily.get("flow", {}).get("delta_flow", {}).get("divergence"),
                "daily_absorption": daily.get("flow", {}).get("delta_flow", {}).get("absorption"),
                "structure_4h": h4.get("structure"),
            },
            "C-W4_macro_range": {
                "daily_premium_discount": daily.get("smart_money", {}).get("premium_discount"),
                "daily_liquidity_pools": daily.get("smart_money", {}).get("liquidity_pools"),
                "daily_volume_profile": daily.get("volume_profile"),
            },
        },
    }


def _compact_flow_evidence(pack: dict[str, Any]) -> dict[str, Any]:
    flow = pack.get("flow", {}).get("delta_flow", {})
    last = flow.get("last", {})
    return {
        "quality": flow.get("quality"),
        "cvd_trend": flow.get("cvd_trend"),
        "cvd_slope_5": flow.get("cvd_slope_5"),
        "cvd_slope_20": flow.get("cvd_slope_20"),
        "delta_zscore20": flow.get("delta_zscore20"),
        "stacked_delta": flow.get("stacked_delta"),
        "last_hybrid_delta": last.get("hybrid_delta"),
        "last_buy_ratio": last.get("buy_ratio"),
    }


def _build_equity_snapshot(
    symbol: str,
    spy_daily: list[dict[str, Any]],
    sector_profile: dict[str, Any] | None = None,
    context_daily: dict[str, list[dict[str, Any]]] | None = None,
    orb_config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    daily = _yahoo_chart(symbol, "1d", "1y")
    hourly = _yahoo_chart(symbol, "60m", "3mo")
    m15_rows = _yahoo_chart(symbol, "15m", "1mo", include_prepost=True, closed_only=False)
    m15 = _closed_yahoo_candles(m15_rows, "15m")
    if not m15:
        raise ValueError(f"no closed Yahoo chart data for {symbol} 15m")
    try:
        weekly = _yahoo_chart(symbol, "1wk", "5y")
    except Exception:
        weekly = _aggregate_candles(daily, 5)

    latest_intraday = m15_rows[-1]
    last_daily = daily[-1]
    session_date = _latest_local_date(m15_rows)
    previous_close = _previous_daily_close(daily, session_date)
    price = float(latest_intraday["close"])
    change_pct = ((price / previous_close) - 1) * 100 if previous_close else None
    session_open = _regular_session_open(m15, session_date)
    gap_pct = ((session_open / previous_close) - 1) * 100 if session_open is not None and previous_close else None
    rs_vs_spy = _relative_strength_vs_spy(daily, spy_daily, 20)
    profile = sector_profile or {
        "asset_type": "equity",
        "sector": "Unclassified",
        "industry": "Unclassified",
        "primary_benchmark": "SPY",
        "secondary_benchmarks": [],
        "peers": [],
        "leverage_multiple": 1,
    }
    available_context = context_daily or {}
    benchmark_symbols = [
        "SPY",
        "QQQ",
        str(profile.get("primary_benchmark") or "SPY").upper(),
        *[str(item).upper() for item in profile.get("secondary_benchmarks", [])],
        *[str(item).upper() for item in profile.get("peers", [])],
    ]
    benchmark_candles = {
        benchmark: available_context[benchmark]
        for benchmark in dict.fromkeys(benchmark_symbols)
        if benchmark in available_context
    }
    relative_factors = _relative_market_factors(daily, benchmark_candles)
    opening_range = _opening_range(m15)
    premarket = _premarket_summary(m15)
    session_vwap = _session_vwap(m15, session_date)
    timeframes = {
        "15m": _indicator_pack(m15, "15m", market="equity"),
        "60m": _indicator_pack(hourly, "60m", market="equity"),
        "1d": _indicator_pack(daily, "1d", market="equity"),
        "1wk": _indicator_pack(weekly, "1wk", market="equity"),
    }
    sector_context = {
        "primary_benchmark": profile.get("primary_benchmark"),
        "secondary_benchmarks": profile.get("secondary_benchmarks", []),
        "peers": profile.get("peers", []),
        "relative_factors": relative_factors,
        "peer_breadth": _peer_breadth(available_context, profile.get("peers", [])),
        "leveraged_etf_risk": _leveraged_etf_risk(daily, profile, available_context),
    }
    primary_relative = relative_factors.get("benchmarks", {}).get(
        str(profile.get("primary_benchmark") or ""),
        {},
    )
    deterministic_orb = evaluate_orb_retest(
        m15,
        previous_close,
        sector_relative_5_pct=primary_relative.get("relative_return_5_pct"),
        config=OrbRetestConfig(**(orb_config or {})),
    )

    return {
        "symbol": symbol,
        "market": "equity",
        "status": "ok",
        "source": "Yahoo Finance chart",
        "price": price,
        "change_pct": change_pct,
        "last_daily_close": last_daily["close"],
        "gap_pct": gap_pct,
        "regular_session_open": session_open,
        "premarket": premarket,
        "regular_session_vwap": session_vwap,
        "deterministic_orb_retest": deterministic_orb,
        "updated_at": latest_intraday["time"],
        "updated_at_local": _format_data_time(latest_intraday["time"]),
        "opening_range_30m": opening_range,
        "relative_strength_vs_spy_20d_pct": rs_vs_spy,
        "classification": profile,
        "sector_context": sector_context,
        "timeframes": timeframes,
        "protocol_setup_candidates": _equity_protocol_candidates(
            price,
            opening_range,
            timeframes,
            sector_context,
            premarket=premarket,
            session_vwap=session_vwap,
        ),
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
    daily_candles: dict[str, list[dict[str, Any]]] = {}
    for symbol in symbols:
        try:
            daily = _yahoo_chart(symbol, "1d", "1y")
            hourly = _yahoo_chart(symbol, "60m", "3mo")
            if symbol == "SPY":
                spy_daily = daily
            daily_candles[symbol] = daily
            context[symbol] = {
                "status": "ok",
                "context_role": _equity_context_role(symbol),
                "price": daily[-1]["close"],
                "change_pct": _return_pct(daily[-2]["close"], daily[-1]["close"]) if len(daily) > 1 else None,
                "daily": _indicator_pack(daily, "1d", include_profile=False, market="equity"),
                "60m": _indicator_pack(hourly, "60m", include_profile=False, market="equity"),
            }
        except Exception as exc:
            context[symbol] = {"status": "error", "error": _brief_error(exc)}
    return {"context": context, "spy_daily": spy_daily, "daily_candles": daily_candles}


def _equity_context_role(symbol: str) -> str:
    if symbol in {"^VIX", "^TNX", "DX-Y.NYB"}:
        return "external_regime_proxy"
    if symbol in {"SPY", "QQQ", "IWM", "DIA"}:
        return "index_regime"
    return "sector_or_peer"


def _equity_protocol_candidates(
    price: float,
    opening_range: dict[str, Any] | None,
    timeframes: dict[str, dict[str, Any]],
    sector_context: dict[str, Any],
    *,
    premarket: dict[str, Any] | None = None,
    session_vwap: float | None = None,
) -> dict[str, Any]:
    m15 = timeframes["15m"]
    hourly = timeframes["60m"]
    daily = timeframes["1d"]
    weekly = timeframes["1wk"]
    primary = str(sector_context.get("primary_benchmark") or "")
    primary_relative = (
        sector_context.get("relative_factors", {}).get("benchmarks", {}).get(primary, {})
    )
    peer_breadth = sector_context.get("peer_breadth", {})
    return {
        "contract": "Observed evidence for protocol matching. LLM must decide score, state, direction and trigger.",
        "micro": {
            "M-E1_sector_rotation_trend": {
                "target_15m_structure": m15.get("structure"),
                "target_60m_structure": hourly.get("structure"),
                "target_vs_sector_5_bar_pct": primary_relative.get("relative_return_5_pct"),
                "peer_positive_ratio_20": peer_breadth.get("positive_ratio"),
                "price_vs_vwap_pct": m15.get("confluence", {}).get("price_vs_vwap_pct"),
            },
            "M-E2_liquidity_sweep": {
                "target_15m_liquidity": m15.get("liquidity"),
                "target_15m_flow_divergence": m15.get("flow", {}).get("delta_flow", {}).get("divergence"),
                "target_15m_absorption": m15.get("flow", {}).get("delta_flow", {}).get("absorption"),
            },
            "M-E3_opening_range_or_news": {
                "opening_range_30m": opening_range,
                "opening_range_complete": bool(opening_range and opening_range.get("complete")),
                "premarket": premarket,
                "session_vwap": session_vwap,
                "price": price,
                "price_above_orh": bool(
                    opening_range and opening_range.get("complete") and price > float(opening_range["high"])
                ),
                "price_below_orl": bool(
                    opening_range and opening_range.get("complete") and price < float(opening_range["low"])
                ),
                "price_above_session_vwap": bool(session_vwap is not None and price > session_vwap),
                "price_below_session_vwap": bool(session_vwap is not None and price < session_vwap),
                "gap_pct": daily.get("factors", {}).get("price_volume", {}).get("gap_pct"),
                "event_data_available": False,
            },
            "M-E4_breakdown_pullback_short": {
                "daily_structure": daily.get("structure"),
                "hourly_structure": hourly.get("structure"),
                "target_15m_structure": m15.get("structure"),
                "price_vs_vwap_pct": m15.get("confluence", {}).get("price_vs_vwap_pct"),
            },
        },
        "macro": {
            "W-E1_weekly_sector_trend": {
                "weekly_structure": weekly.get("structure"),
                "daily_structure": daily.get("structure"),
                "weekly_ema_alignment": weekly.get("factors", {}).get("trend", {}).get("ema_alignment"),
                "target_vs_sector_20_bar_pct": primary_relative.get("relative_return_20_pct"),
                "peer_positive_ratio_20": peer_breadth.get("positive_ratio"),
            },
            "W-E2_event_revaluation": {
                "weekly_structure": weekly.get("structure"),
                "gap_pct": daily.get("factors", {}).get("price_volume", {}).get("gap_pct"),
                "event_fundamental_data_available": False,
            },
            "W-E3_valuation_repair": {
                "weekly_drawdown_pct": weekly.get("factors", {}).get("volatility", {}).get(
                    "current_drawdown_from_60_bar_peak_pct"
                ),
                "daily_range_position_60": daily.get("factors", {}).get("price_volume", {}).get(
                    "range_position_60"
                ),
                "daily_structure": daily.get("structure"),
            },
            "W-E4_defensive_rotation": {
                "requires_index_and_external_context": ["SPY", "QQQ", "^VIX"],
                "target_sector": primary,
            },
        },
    }


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


def _indicator_pack(
    candles: list[dict[str, Any]],
    timeframe: str,
    include_profile: bool = True,
    market: str = "unknown",
) -> dict[str, Any]:
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
        "factors": _factor_pack(candles, timeframe, market),
    }
    if volume_profile is not None:
        pack["volume_profile"] = volume_profile
        pack["volume_delta_profile"] = _volume_delta_profile(candles)
    pack["confluence"] = _confluence_pack(candles, state, atr, vwap, volume_profile, delta_flow, smart_money)
    pack["setup_candidates"] = _setup_candidates(candles, timeframe, state, atr, relative_volume, pack["factors"])
    return pack


def _factor_pack(candles: list[dict[str, Any]], timeframe: str, market: str = "unknown") -> dict[str, Any]:
    closes = [float(candle["close"]) for candle in candles]
    highs = [float(candle["high"]) for candle in candles]
    lows = [float(candle["low"]) for candle in candles]
    volumes = [float(candle.get("volume") or 0.0) for candle in candles]
    ema_8 = _ema_last(closes, 8)
    ema_20 = _ema_last(closes, 20)
    ema_50 = _ema_last(closes, 50)
    ema_200 = _ema_last(closes, 200)
    close = closes[-1]
    returns = _log_returns(closes)
    periods = _annualization_periods(timeframe, market)
    realized_window = returns[-20:]
    parkinson_window = list(zip(highs[-20:], lows[-20:]))
    rolling_high_60 = max(highs[-60:])
    rolling_low_60 = min(lows[-60:])
    peak = max(closes[-60:])
    return {
        "trend": {
            "ema_8": ema_8,
            "ema_20": ema_20,
            "ema_50": ema_50,
            "ema_200": ema_200,
            "ema_alignment": _ema_alignment(ema_8, ema_20, ema_50, ema_200),
            "price_vs_ema_20_pct": _return_pct(ema_20, close) if ema_20 else None,
            "price_vs_ema_50_pct": _return_pct(ema_50, close) if ema_50 else None,
            "price_vs_ema_200_pct": _return_pct(ema_200, close) if ema_200 else None,
            "ema_20_slope_5_pct": _ema_slope_pct(closes, 20, 5),
            "ema_50_slope_10_pct": _ema_slope_pct(closes, 50, 10),
        },
        "volatility": {
            "annualization_periods_assumed": periods,
            "annualization_market": market,
            "realized_volatility_20_annualized_pct": (
                pstdev(realized_window) * math.sqrt(periods) * 100 if len(realized_window) >= 2 else None
            ),
            "parkinson_volatility_20_annualized_pct": _parkinson_volatility(parkinson_window, periods),
            "range_expansion_ratio_5_to_20": _range_expansion_ratio(candles),
            "current_drawdown_from_60_bar_peak_pct": _return_pct(peak, close),
            "rolling_60_high": rolling_high_60,
            "rolling_60_low": rolling_low_60,
        },
        "price_volume": {
            "efficiency_ratio_20": _efficiency_ratio(closes, 20),
            "range_position_20": _range_position(close, highs[-20:], lows[-20:]),
            "range_position_60": _range_position(close, highs[-60:], lows[-60:]),
            "gap_pct": _gap_pct(candles),
            "dollar_volume_last": close * volumes[-1],
            "dollar_volume_sma20": _sma([price * volume for price, volume in zip(closes, volumes)], 20),
            "volume_trend_5_to_20": _safe_div(_sma(volumes, 5) or 0.0, _sma(volumes, 20) or 0.0),
            "up_volume_ratio_20": _up_volume_ratio(candles[-20:]),
        },
        "data_quality": {
            "bar_count": len(candles),
            "ema_200_ready": len(candles) >= 200,
            "volatility_20_ready": len(returns) >= 20,
            "volume_nonzero_ratio": sum(1 for value in volumes if value > 0) / len(volumes),
        },
    }


def _relative_market_factors(
    target_candles: list[dict[str, Any]],
    benchmark_candles: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    target_by_time = {str(candle["time"]): float(candle["close"]) for candle in target_candles}
    benchmarks: dict[str, Any] = {}
    for symbol, candles in benchmark_candles.items():
        benchmark_by_time = {str(candle["time"]): float(candle["close"]) for candle in candles}
        common = sorted(set(target_by_time) & set(benchmark_by_time))
        if len(common) < 2:
            size = min(len(target_candles), len(candles))
            target_closes = [float(candle["close"]) for candle in target_candles[-size:]]
            benchmark_closes = [float(candle["close"]) for candle in candles[-size:]]
        else:
            target_closes = [target_by_time[key] for key in common]
            benchmark_closes = [benchmark_by_time[key] for key in common]
        target_returns = _simple_returns(target_closes)
        benchmark_returns = _simple_returns(benchmark_closes)
        window = min(60, len(target_returns), len(benchmark_returns))
        benchmarks[str(symbol).upper()] = {
            "relative_return_5_pct": _relative_return(target_closes, benchmark_closes, 5),
            "relative_return_20_pct": _relative_return(target_closes, benchmark_closes, 20),
            "relative_return_60_pct": _relative_return(target_closes, benchmark_closes, 60),
            "beta_60": _beta(target_returns[-window:], benchmark_returns[-window:]) if window >= 3 else None,
            "correlation_60": (
                _correlation(target_returns[-window:], benchmark_returns[-window:]) if window >= 3 else None
            ),
            "aligned_bar_count": min(len(target_closes), len(benchmark_closes)),
        }
    return {"benchmarks": benchmarks}


def _last_bar(candle: dict[str, Any]) -> dict[str, Any]:
    return {
        "time": candle["time"],
        "open": float(candle["open"]),
        "high": float(candle["high"]),
        "low": float(candle["low"]),
        "close": float(candle["close"]),
        "volume": float(candle.get("volume") or 0.0),
        "quote_volume": _optional_float(candle.get("quote_volume")),
        "trade_count": candle.get("trade_count"),
        "taker_buy_volume": _optional_float(candle.get("taker_buy_volume")),
        "taker_sell_volume": _optional_taker_sell_volume(candle),
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


def _ema_last(values: list[float], period: int) -> float | None:
    if len(values) < period:
        return None
    alpha = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for value in values[period:]:
        ema = value * alpha + ema * (1 - alpha)
    return ema


def _ema_slope_pct(values: list[float], period: int, lookback: int) -> float | None:
    if len(values) < period + lookback:
        return None
    current = _ema_last(values, period)
    previous = _ema_last(values[:-lookback], period)
    return _return_pct(previous, current) if previous and current is not None else None


def _ema_alignment(*values: float | None) -> str:
    available = [value for value in values if value is not None]
    if len(available) < 3:
        return "INSUFFICIENT_DATA"
    if all(first > second for first, second in zip(available, available[1:])):
        return "BULLISH"
    if all(first < second for first, second in zip(available, available[1:])):
        return "BEARISH"
    return "MIXED"


def _annualization_periods(timeframe: str, market: str) -> float:
    if market == "crypto":
        return {
            "5m": 365 * 24 * 12,
            "15m": 365 * 24 * 4,
            "1h": 365 * 24,
            "4h": 365 * 6,
            "1d": 365,
            "1wk": 52,
        }.get(timeframe, 365)
    return {
        "5m": 252 * 78,
        "15m": 252 * 26,
        "60m": 252 * 6.5,
        "1h": 252 * 6.5,
        "4h": 252 * 1.625,
        "1d": 252,
        "1wk": 52,
    }.get(timeframe, 252)


def _log_returns(values: list[float]) -> list[float]:
    return [math.log(current / previous) for previous, current in zip(values, values[1:]) if previous > 0 and current > 0]


def _simple_returns(values: list[float]) -> list[float]:
    return [(current / previous) - 1 for previous, current in zip(values, values[1:]) if previous]


def _parkinson_volatility(high_low_pairs: list[tuple[float, float]], periods: float) -> float | None:
    valid = [(high, low) for high, low in high_low_pairs if high > 0 and low > 0 and high >= low]
    if len(valid) < 2:
        return None
    variance = sum(math.log(high / low) ** 2 for high, low in valid) / (4 * math.log(2) * len(valid))
    return math.sqrt(max(0.0, variance) * periods) * 100


def _range_expansion_ratio(candles: list[dict[str, Any]]) -> float | None:
    ranges = [float(candle["high"]) - float(candle["low"]) for candle in candles]
    return _safe_div(_sma(ranges, 5) or 0.0, _sma(ranges, 20) or 0.0)


def _efficiency_ratio(values: list[float], period: int) -> float | None:
    if len(values) <= period:
        return None
    window = values[-period - 1 :]
    path = sum(abs(current - previous) for previous, current in zip(window, window[1:]))
    return _safe_div(abs(window[-1] - window[0]), path)


def _range_position(close: float, highs: list[float], lows: list[float]) -> float | None:
    if not highs or not lows:
        return None
    return _safe_div(close - min(lows), max(highs) - min(lows))


def _gap_pct(candles: list[dict[str, Any]]) -> float | None:
    if len(candles) < 2:
        return None
    return _return_pct(float(candles[-2]["close"]), float(candles[-1]["open"]))


def _up_volume_ratio(candles: list[dict[str, Any]]) -> float | None:
    total = sum(float(candle.get("volume") or 0.0) for candle in candles)
    up = sum(
        float(candle.get("volume") or 0.0)
        for candle in candles
        if float(candle["close"]) > float(candle["open"])
    )
    return _safe_div(up, total)


def _relative_return(target: list[float], benchmark: list[float], lookback: int) -> float | None:
    size = min(len(target), len(benchmark))
    if size <= lookback:
        return None
    target_return = _return_pct(target[-lookback - 1], target[-1])
    benchmark_return = _return_pct(benchmark[-lookback - 1], benchmark[-1])
    if target_return is None or benchmark_return is None:
        return None
    return target_return - benchmark_return


def _beta(target_returns: list[float], benchmark_returns: list[float]) -> float | None:
    size = min(len(target_returns), len(benchmark_returns))
    if size < 3:
        return None
    target = target_returns[-size:]
    benchmark = benchmark_returns[-size:]
    target_mean = sum(target) / size
    benchmark_mean = sum(benchmark) / size
    covariance = sum((left - target_mean) * (right - benchmark_mean) for left, right in zip(target, benchmark)) / size
    variance = sum((value - benchmark_mean) ** 2 for value in benchmark) / size
    return _safe_div(covariance, variance)


def _correlation(left_values: list[float], right_values: list[float]) -> float | None:
    size = min(len(left_values), len(right_values))
    if size < 3:
        return None
    left = left_values[-size:]
    right = right_values[-size:]
    left_mean = sum(left) / size
    right_mean = sum(right) / size
    covariance = sum((x - left_mean) * (y - right_mean) for x, y in zip(left, right)) / size
    left_variance = sum((x - left_mean) ** 2 for x in left) / size
    right_variance = sum((y - right_mean) ** 2 for y in right) / size
    denominator = math.sqrt(left_variance * right_variance)
    return _safe_div(covariance, denominator)


def _setup_candidates(
    candles: list[dict[str, Any]],
    timeframe: str,
    state: dict[str, Any],
    atr: float,
    relative_volume: float | None,
    factors: dict[str, Any],
) -> dict[str, Any]:
    last = candles[-1]
    close = float(last["close"])
    liquidity = _liquidity_proxy(candles)
    trend = factors["trend"]
    price_volume = factors["price_volume"]
    squeeze = _squeeze(candles)
    return {
        "contract": "Evidence only. These are candidate conditions, not trade triggers or recommendations.",
        "timeframe": timeframe,
        "liquidity_sweep": {
            "swept_high": bool(liquidity.get("swept_recent_high_and_closed_back_inside")),
            "swept_low": bool(liquidity.get("swept_recent_low_and_closed_back_inside")),
            "lower_wick_ratio": liquidity.get("lower_wick_ratio"),
            "upper_wick_ratio": liquidity.get("upper_wick_ratio"),
        },
        "trend_pullback": {
            "ema_alignment": trend.get("ema_alignment"),
            "distance_to_ema20_atr": (
                _safe_div(close - float(trend["ema_20"]), atr) if trend.get("ema_20") is not None else None
            ),
            "efficiency_ratio_20": price_volume.get("efficiency_ratio_20"),
        },
        "breakout_or_breakdown": {
            "bos_up": bool(state["structure"].get("bos_up")),
            "bos_down": bool(state["structure"].get("bos_down")),
            "relative_volume": relative_volume,
            "range_position_20": price_volume.get("range_position_20"),
        },
        "volatility_transition": {
            "squeeze_on": squeeze.get("squeeze_on"),
            "squeeze_momentum": squeeze.get("momentum"),
            "range_expansion_ratio_5_to_20": factors["volatility"].get("range_expansion_ratio_5_to_20"),
        },
    }


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
        "method": last_row["method"],
        "source": _delta_source_summary(rows),
        "quality": "TAKER_DELTA" if last_row["source"] == "taker_buy_volume" else "OHLCV_PROXY",
        "last": {
            "source": last_row["source"],
            "close_location_value": last_row["clv"],
            "body_ratio": last_row["body_ratio"],
            "buy_volume_proxy": last_row["buy_volume_proxy"],
            "sell_volume_proxy": last_row["sell_volume_proxy"],
            "taker_buy_volume": last_row["taker_buy_volume"],
            "taker_sell_volume": last_row["taker_sell_volume"],
            "clv_delta": last_row["clv_delta"],
            "body_delta": last_row["body_delta"],
            "signed_volume_delta": last_row["signed_volume_delta"],
            "hybrid_delta": last_delta,
            "imbalance_ratio": _safe_div(last_delta, last_row["volume"]),
            "buy_ratio": _safe_div(last_row["buy_volume_proxy"], last_row["volume"]),
        },
        "delta_sma5": delta_sma5,
        "delta_sma20": delta_sma20,
        "delta_zscore20": delta_zscore,
        "positive_delta_sum20": sum(value for value in deltas[-20:] if value > 0),
        "negative_delta_sum20": sum(value for value in deltas[-20:] if value < 0),
        "net_delta_pct20": _safe_div(sum(deltas[-20:]), sum(row["volume"] for row in rows[-20:])),
        "cumulative_delta_20": sum(deltas[-20:]) if deltas else 0.0,
        "cumulative_delta_50": cvd[-1] if cvd else 0.0,
        "cumulative_delta_normalized": _safe_div(cvd[-1], sum(row["volume"] for row in rows)) if cvd else None,
        "cvd_slope_5": slope_5,
        "cvd_slope_20": slope_20,
        "cvd_acceleration": acceleration,
        "cvd_trend": _trend_label(slope_5),
        "stacked_delta": _stacked_delta(deltas),
        "divergence": _cvd_divergence(window, cvd),
        "absorption": _absorption_pack(window, rows, delta_zscore),
    }


def _delta_row(candle: dict[str, Any]) -> dict[str, Any]:
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
    taker_buy_volume = _optional_float(candle.get("taker_buy_volume"))
    if taker_buy_volume is not None and volume:
        taker_buy_volume = _clamp(taker_buy_volume, 0.0, volume)
        taker_sell_volume = volume - taker_buy_volume
        hybrid_delta = taker_buy_volume - taker_sell_volume
        buy_volume = taker_buy_volume
        sell_volume = taker_sell_volume
        source = "taker_buy_volume"
        method = "Binance taker buy volume: taker_buy_volume - taker_sell_volume"
    else:
        taker_sell_volume = None
        hybrid_delta = clv_delta * 0.6 + body_delta * 0.4
        buy_volume = volume * _clamp((clv + 1) / 2, 0.0, 1.0)
        sell_volume = volume - buy_volume
        source = "ohlcv_proxy"
        method = "OHLCV proxy: 60% close-location delta + 40% candle-body delta"
    return {
        "volume": volume,
        "source": source,
        "method": method,
        "clv": clv,
        "body_ratio": body_ratio,
        "clv_delta": clv_delta,
        "body_delta": body_delta,
        "signed_volume_delta": signed_volume_delta,
        "hybrid_delta": hybrid_delta,
        "taker_buy_volume": taker_buy_volume,
        "taker_sell_volume": taker_sell_volume,
        "buy_volume_proxy": buy_volume,
        "sell_volume_proxy": sell_volume,
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
    if not candles:
        return {"status": "empty"}
    start = max(0, len(candles) - lookback)
    events = []
    for index in range(start, len(candles)):
        candle = candles[index]
        baseline = _displacement_baseline(candles, index)
        if baseline is None:
            continue
        avg_range, volume_sma = baseline
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
    if len(candles) < 5:
        return {"bullish_recent": [], "bearish_recent": []}
    start = max(0, len(candles) - lookback)
    bullish = []
    bearish = []
    for index in range(start, len(candles)):
        candle = candles[index]
        baseline = _displacement_baseline(candles, index)
        if baseline is None:
            continue
        avg_range, volume_sma = baseline
        direction = _displacement_direction(candle, avg_range, volume_sma)
        if direction == "BULLISH":
            block = _find_prior_opposite_candle(candles, index, want_bearish=True)
            if block:
                bullish.append(_order_block_record(block, candles, index, "bullish"))
        elif direction == "BEARISH":
            block = _find_prior_opposite_candle(candles, index, want_bearish=False)
            if block:
                bearish.append(_order_block_record(block, candles, index, "bearish"))
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
        row = _delta_row(candle)
        _distribute_value_to_buckets(buckets, float(candle["low"]), float(candle["high"]), row["volume"], "volume")
        _distribute_value_to_buckets(buckets, float(candle["low"]), float(candle["high"]), row["hybrid_delta"], "delta")
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


def _displacement_baseline(
    candles: list[dict[str, Any]],
    index: int,
    baseline_lookback: int = 20,
    minimum_history: int = 5,
) -> tuple[float, float] | None:
    history = candles[max(0, index - baseline_lookback) : index]
    if len(history) < minimum_history:
        return None
    ranges = [max(float(candle["high"]) - float(candle["low"]), 1e-12) for candle in history]
    volumes = [float(candle.get("volume") or 0.0) for candle in history]
    return sum(ranges) / len(ranges), sum(volumes) / len(volumes)


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


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _optional_taker_sell_volume(candle: dict[str, Any]) -> float | None:
    volume = _optional_float(candle.get("volume"))
    taker_buy = _optional_float(candle.get("taker_buy_volume"))
    if volume is None or taker_buy is None:
        return None
    return max(0.0, volume - taker_buy)


def _delta_source_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    if not rows:
        return {"primary": "none", "taker_delta_ratio": 0.0, "proxy_ratio": 0.0}
    taker_count = sum(1 for row in rows if row.get("source") == "taker_buy_volume")
    proxy_count = len(rows) - taker_count
    return {
        "primary": "taker_buy_volume" if taker_count >= proxy_count and taker_count else "ohlcv_proxy",
        "taker_delta_bars": taker_count,
        "proxy_delta_bars": proxy_count,
        "taker_delta_ratio": taker_count / len(rows),
        "proxy_ratio": proxy_count / len(rows),
    }


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
        _distribute_value_to_buckets(
            buckets,
            float(candle["low"]),
            float(candle["high"]),
            float(candle.get("volume") or 0.0),
            "volume",
        )
    sorted_by_volume = sorted(buckets, key=lambda bucket: bucket["volume"], reverse=True)
    poc_bucket = sorted_by_volume[0]
    lvn_buckets = sorted(buckets, key=lambda bucket: bucket["volume"])[:3]
    value_area = _value_area_from_buckets(buckets, poc_bucket, target_ratio=0.7)
    return {
        "lookback_bars": len(window),
        "method": "range-distributed candle volume across price bins",
        "poc": _bucket_mid(poc_bucket),
        "hvn": [_bucket_mid(bucket) for bucket in sorted_by_volume[:3]],
        "lvn": [_bucket_mid(bucket) for bucket in lvn_buckets],
        "value_area_proxy": value_area,
        "bins": buckets,
    }


def _bucket_mid(bucket: dict[str, float]) -> float:
    return (bucket["low"] + bucket["high"]) / 2


def _distribute_value_to_buckets(
    buckets: list[dict[str, float]],
    candle_low: float,
    candle_high: float,
    value: float,
    field: str,
) -> None:
    if not buckets or value == 0:
        return
    low = min(candle_low, candle_high)
    high = max(candle_low, candle_high)
    if math.isclose(low, high):
        index = _bucket_index_for_price(buckets, low)
        buckets[index][field] += value
        return
    total_overlap = 0.0
    overlaps: list[tuple[dict[str, float], float]] = []
    for bucket in buckets:
        overlap = max(0.0, min(high, bucket["high"]) - max(low, bucket["low"]))
        if overlap > 0:
            overlaps.append((bucket, overlap))
            total_overlap += overlap
    if not overlaps or total_overlap == 0:
        index = _bucket_index_for_price(buckets, (low + high) / 2)
        buckets[index][field] += value
        return
    for bucket, overlap in overlaps:
        bucket[field] += value * (overlap / total_overlap)


def _bucket_index_for_price(buckets: list[dict[str, float]], price: float) -> int:
    if price <= buckets[0]["low"]:
        return 0
    if price >= buckets[-1]["high"]:
        return len(buckets) - 1
    for index, bucket in enumerate(buckets):
        if bucket["low"] <= price <= bucket["high"]:
            return index
    return len(buckets) - 1


def _value_area_from_buckets(
    buckets: list[dict[str, float]],
    poc_bucket: dict[str, float],
    target_ratio: float = 0.7,
) -> dict[str, float | int | None]:
    total_volume = sum(bucket["volume"] for bucket in buckets)
    if not buckets or total_volume <= 0:
        return {"low": None, "high": None, "volume_ratio": None, "bucket_count": 0}
    poc_index = buckets.index(poc_bucket)
    included = {poc_index}
    current_volume = buckets[poc_index]["volume"]
    left = poc_index - 1
    right = poc_index + 1
    while current_volume / total_volume < target_ratio and (left >= 0 or right < len(buckets)):
        left_volume = buckets[left]["volume"] if left >= 0 else -1.0
        right_volume = buckets[right]["volume"] if right < len(buckets) else -1.0
        if right_volume > left_volume:
            included.add(right)
            current_volume += max(0.0, right_volume)
            right += 1
        else:
            included.add(left)
            current_volume += max(0.0, left_volume)
            left -= 1
    selected = [buckets[index] for index in sorted(included)]
    return {
        "low": min(bucket["low"] for bucket in selected),
        "high": max(bucket["high"] for bucket in selected),
        "volume_ratio": current_volume / total_volume,
        "bucket_count": len(selected),
    }


def _opening_range(candles: list[dict[str, Any]], minutes: int = 30) -> dict[str, Any] | None:
    """Return the latest New York regular-session opening range.

    Extended-hours candles are deliberately excluded.  A range is not eligible
    for breakout decisions until all expected 15-minute bars have arrived.
    """
    session_date = _latest_local_date(candles)
    if session_date is None:
        return None
    end_minutes = 9 * 60 + 30 + minutes
    opening_bars = [
        candle
        for candle in candles
        if _local_date(candle) == session_date
        and 9 * 60 + 30 <= _local_minutes(candle) < end_minutes
    ]
    expected_bars = max(1, minutes // 15)
    if not opening_bars:
        return {
            "date": session_date,
            "minutes": minutes,
            "bar_count": 0,
            "expected_bar_count": expected_bars,
            "complete": False,
            "status": "NOT_STARTED",
        }
    complete = len(opening_bars) >= expected_bars
    return {
        "date": session_date,
        "minutes": minutes,
        "high": max(float(candle["high"]) for candle in opening_bars),
        "low": min(float(candle["low"]) for candle in opening_bars),
        "bar_count": len(opening_bars),
        "expected_bar_count": expected_bars,
        "complete": complete,
        "status": "COMPLETE" if complete else "FORMING",
    }


def _premarket_summary(candles: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Summarize 04:00-09:30 ET and compute time-aligned premarket RVOL."""
    session_date = _latest_local_date(candles)
    if session_date is None:
        return None
    current = _session_window(candles, session_date, 4 * 60, 9 * 60 + 30)
    if not current:
        return {
            "date": session_date,
            "status": "NO_DATA",
            "bar_count": 0,
        }
    cutoff = max(_local_minutes(candle) for candle in current)
    prior_by_date: dict[str, list[dict[str, Any]]] = {}
    for candle in candles:
        date_value = _local_date(candle)
        minute_value = _local_minutes(candle)
        if date_value >= session_date or not 4 * 60 <= minute_value <= cutoff:
            continue
        prior_by_date.setdefault(date_value, []).append(candle)
    historical_volumes = [
        sum(float(candle.get("volume") or 0.0) for candle in rows)
        for rows in prior_by_date.values()
        if rows
    ]
    volume = sum(float(candle.get("volume") or 0.0) for candle in current)
    average_volume = sum(historical_volumes[-20:]) / len(historical_volumes[-20:]) if historical_volumes else None
    return {
        "date": session_date,
        "status": "OK" if volume > 0 else "PRICE_ONLY",
        "high": max(float(candle["high"]) for candle in current),
        "low": min(float(candle["low"]) for candle in current),
        "volume": volume,
        "relative_volume": volume / average_volume if average_volume else None,
        "volume_quality": "AVAILABLE" if volume > 0 else "UNAVAILABLE_PROVIDER_ZERO_VOLUME",
        "comparison_days": min(len(historical_volumes), 20),
        "bar_count": len(current),
        "through_et": f"{cutoff // 60:02d}:{cutoff % 60:02d}",
    }


def _regular_session_open(candles: list[dict[str, Any]], session_date: str | None) -> float | None:
    if session_date is None:
        return None
    rows = _session_window(candles, session_date, 9 * 60 + 30, 16 * 60)
    return float(rows[0]["open"]) if rows else None


def _session_vwap(candles: list[dict[str, Any]], session_date: str | None) -> float | None:
    if session_date is None:
        return None
    rows = _session_window(candles, session_date, 9 * 60 + 30, 16 * 60)
    return _vwap(rows) if rows else None


def _previous_daily_close(candles: list[dict[str, Any]], session_date: str | None) -> float | None:
    if not candles:
        return None
    if session_date is None:
        return float(candles[-1]["close"])
    previous = [candle for candle in candles if _local_date(candle) < session_date]
    return float(previous[-1]["close"]) if previous else None


def _session_window(
    candles: list[dict[str, Any]],
    session_date: str,
    start_minutes: int,
    end_minutes: int,
) -> list[dict[str, Any]]:
    return [
        candle
        for candle in candles
        if _local_date(candle) == session_date
        and start_minutes <= _local_minutes(candle) < end_minutes
    ]


def _latest_local_date(candles: list[dict[str, Any]]) -> str | None:
    return max((_local_date(candle) for candle in candles), default=None)


def _local_date(candle: dict[str, Any]) -> str:
    return _local_datetime(candle).date().isoformat()


def _local_minutes(candle: dict[str, Any]) -> int:
    local = _local_datetime(candle)
    return local.hour * 60 + local.minute


def _local_datetime(candle: dict[str, Any]) -> datetime:
    value = str(candle["time"]).replace("Z", "+00:00")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(NEW_YORK_TZ)


def _relative_strength_vs_spy(candles: list[dict[str, Any]], spy_daily: list[dict[str, Any]], lookback: int) -> float | None:
    symbol_return = _lookback_return([float(candle["close"]) for candle in candles], lookback)
    spy_return = _lookback_return([float(candle["close"]) for candle in spy_daily], lookback) if spy_daily else None
    if symbol_return is None or spy_return is None:
        return None
    return symbol_return - spy_return


def _peer_breadth(
    context_daily: dict[str, list[dict[str, Any]]],
    peers: list[str],
    lookback: int = 20,
) -> dict[str, Any]:
    returns: dict[str, float | None] = {}
    for peer in peers:
        symbol = str(peer).upper()
        candles = context_daily.get(symbol, [])
        returns[symbol] = _lookback_return([float(candle["close"]) for candle in candles], lookback) if candles else None
    available = [value for value in returns.values() if value is not None]
    return {
        "lookback_bars": lookback,
        "peer_returns_pct": returns,
        "available_count": len(available),
        "positive_count": sum(1 for value in available if value > 0),
        "positive_ratio": sum(1 for value in available if value > 0) / len(available) if available else None,
        "median_return_pct": median(available) if available else None,
    }


def _leveraged_etf_risk(
    target_daily: list[dict[str, Any]],
    profile: dict[str, Any],
    context_daily: dict[str, list[dict[str, Any]]],
) -> dict[str, Any] | None:
    leverage = float(profile.get("leverage_multiple") or 1)
    if leverage <= 1:
        return None
    benchmark = str(profile.get("primary_benchmark") or "").upper()
    benchmark_daily = context_daily.get(benchmark, [])
    target_closes = [float(candle["close"]) for candle in target_daily]
    benchmark_closes = [float(candle["close"]) for candle in benchmark_daily]
    target_return = _lookback_return(target_closes, 20)
    benchmark_return = _lookback_return(benchmark_closes, 20)
    return {
        "daily_reset": True,
        "leverage_multiple": leverage,
        "primary_benchmark": benchmark,
        "path_dependency_warning": "Multi-day return can diverge from leverage_multiple × benchmark return.",
        "target_return_20_pct": target_return,
        "benchmark_return_20_pct": benchmark_return,
        "leveraged_tracking_gap_20_pct": (
            target_return - leverage * benchmark_return
            if target_return is not None and benchmark_return is not None
            else None
        ),
    }


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
