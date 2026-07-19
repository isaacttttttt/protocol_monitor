import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.review import llm_protocol_report
from app.review.indicator_snapshot import (
    IndicatorSnapshotEvent,
    _aggregate_equity_4h,
    _crypto_protocol_candidates,
    _displacement_pack,
    _equity_protocol_candidates,
    _factor_pack,
    _indicator_pack,
    _relative_market_factors,
    _volume_profile,
    compact_snapshot_for_llm,
    compact_symbol_snapshot_for_llm,
    resolve_watchlist,
)


def _candles(count: int = 50):
    start = datetime(2026, 1, 1, tzinfo=timezone.utc)
    candles = []
    price = 100.0
    for index in range(count):
        open_ = price
        close = price + (1.2 if index % 3 else -0.5)
        high = max(open_, close) + 1.0
        low = min(open_, close) - 1.0
        candles.append(
            {
                "time": (start + timedelta(minutes=15 * index)).isoformat(),
                "open": open_,
                "high": high,
                "low": low,
                "close": close,
                "volume": 1000 + index * 10,
            }
        )
        price = close
    return candles


def test_indicator_pack_contains_protocol_metrics():
    pack = _indicator_pack(_candles(), "15m")

    assert pack["structure"]["trend"] in {"UP", "DOWN", "RANGE"}
    assert pack["atr14"] > 0
    assert "macd" in pack
    assert "squeeze_on" in pack["squeeze"]
    assert pack["vwap"] is not None
    assert pack["anchored_vwap"]["from_recent_low"] is not None
    assert pack["volume_profile"]["poc"] is not None
    assert "cvd_proxy" in pack["flow"]
    assert "obv" in pack["flow"]
    assert "ad_line" in pack["flow"]
    assert "nvi" in pack["flow"]
    assert "delta_flow" in pack["flow"]
    assert "swept_recent_high_and_closed_back_inside" in pack["liquidity"]
    assert "smart_money" in pack
    assert "fair_value_gaps" in pack["smart_money"]
    assert "order_blocks" in pack["smart_money"]
    assert "liquidity_pools" in pack["smart_money"]
    assert "volume_delta_profile" in pack
    assert "confluence" in pack
    assert "ai_attention_flags" in pack["confluence"]
    assert "factors" in pack
    assert "trend" in pack["factors"]
    assert "volatility" in pack["factors"]
    assert "price_volume" in pack["factors"]
    assert "setup_candidates" in pack


def test_factor_pack_exposes_observations_without_final_trade_judgment():
    factors = _factor_pack(_candles(220), "1d")

    assert factors["trend"]["ema_20"] is not None
    assert factors["trend"]["ema_50"] is not None
    assert factors["trend"]["ema_200"] is not None
    assert factors["trend"]["price_vs_ema_200_pct"] is not None
    assert factors["volatility"]["realized_volatility_20_annualized_pct"] is not None
    assert factors["volatility"]["parkinson_volatility_20_annualized_pct"] is not None
    assert factors["price_volume"]["efficiency_ratio_20"] is not None
    assert factors["price_volume"]["range_position_20"] is not None
    assert "trade_decision" not in factors
    assert "score" not in factors


def test_relative_market_factors_compare_target_to_index_and_sector():
    target = _candles(80)
    spy = _candles(80)
    sector = _candles(80)
    for index, candle in enumerate(target):
        candle["close"] *= 1 + index * 0.0005

    factors = _relative_market_factors(target, {"SPY": spy, "SOXX": sector})

    assert factors["benchmarks"]["SPY"]["relative_return_20_pct"] > 0
    assert factors["benchmarks"]["SOXX"]["relative_return_20_pct"] > 0
    assert factors["benchmarks"]["SPY"]["beta_60"] is not None
    assert factors["benchmarks"]["SPY"]["correlation_60"] is not None


def test_equity_protocol_candidates_are_evidence_not_final_judgment():
    pack = _indicator_pack(_candles(220), "1d", market="equity")
    candidates = _equity_protocol_candidates(
        120.0,
        {"1h": pack, "4h": pack, "1d": pack},
        {
            "primary_benchmark": "SOXX",
            "relative_factors": {"benchmarks": {"SOXX": {"relative_return_5_pct": 1.2, "relative_return_20_pct": 3.4}}},
            "peer_breadth": {"positive_ratio": 0.75},
        },
    )

    assert "High-timeframe evidence only" in candidates["contract"]
    assert set(candidates["timeframes"]) == {"1H", "4H", "DAY"}
    assert candidates["relative_context"]["primary_benchmark"] == "SOXX"
    assert "15m" not in json.dumps(candidates).lower()


def test_crypto_protocol_candidates_cover_only_high_timeframes():
    pack = _indicator_pack(_candles(220), "1h", market="crypto")
    candidates = _crypto_protocol_candidates(
        {"1h": pack, "4h": pack, "1d": pack},
        {"funding_rate_pct": 0.01, "open_interest": 1000, "basis_pct": 0.02},
    )

    assert set(candidates["timeframes"]) == {"1H", "4H", "DAY"}
    assert candidates["derivatives_context"]["funding_rate_pct"] == 0.01
    assert "15m" not in json.dumps(candidates).lower()


def test_equity_4h_aggregation_never_crosses_new_york_sessions():
    candles = []
    for day in (13, 14):
        for hour in (13, 14, 15, 16):
            candles.append(
                {
                    "time": datetime(2026, 7, day, hour, 30, tzinfo=timezone.utc).isoformat(),
                    "open": 100 + day,
                    "high": 102 + day,
                    "low": 99 + day,
                    "close": 101 + day,
                    "volume": 10,
                }
            )
    candles.append(
        {
            "time": datetime(2026, 7, 14, 17, 30, tzinfo=timezone.utc).isoformat(),
            "open": 999,
            "high": 999,
            "low": 999,
            "close": 999,
            "volume": 1,
        }
    )

    aggregated = _aggregate_equity_4h(candles)

    assert len(aggregated) == 2
    assert [bar["time"] for bar in aggregated] == [candles[0]["time"], candles[4]["time"]]
    assert all(bar["volume"] == 40 for bar in aggregated)
    assert all(bar["close"] != 999 for bar in aggregated)


def test_equity_4h_aggregation_keeps_completed_session_tail_only():
    candles = []
    for day in (13, 14):
        for index, hour in enumerate(range(13, 20)):
            price = day * 10 + index
            candles.append(
                {
                    "time": datetime(2026, 7, day, hour, 30, tzinfo=timezone.utc).isoformat(),
                    "open": price,
                    "high": price + 2,
                    "low": price - 1,
                    "close": price + 1,
                    "volume": 10,
                }
            )

    aggregated = _aggregate_equity_4h(
        candles,
        as_of=datetime(2026, 7, 14, 19, 30, tzinfo=timezone.utc),
    )

    assert [bar["time"] for bar in aggregated] == [
        candles[0]["time"],
        candles[4]["time"],
        candles[7]["time"],
    ]
    assert [bar["volume"] for bar in aggregated] == [40, 30, 40]
    assert aggregated[1]["close"] == candles[6]["close"]
    assert all(bar["time"] != candles[11]["time"] for bar in aggregated)


def test_delta_flow_prefers_taker_buy_volume_when_available():
    candles = _candles()
    candles[-1]["volume"] = 1000
    candles[-1]["taker_buy_volume"] = 700

    pack = _indicator_pack(candles, "15m")
    delta_flow = pack["flow"]["delta_flow"]

    assert delta_flow["quality"] == "TAKER_DELTA"
    assert delta_flow["last"]["source"] == "taker_buy_volume"
    assert delta_flow["last"]["hybrid_delta"] == 400
    assert delta_flow["last"]["buy_ratio"] == 0.7


def test_volume_profile_distributes_wide_candle_across_price_bins():
    candles = [
        {"time": "t1", "open": 10, "high": 20, "low": 10, "close": 15, "volume": 100},
    ]

    profile = _volume_profile(candles, bins=2, lookback=1)

    assert profile["method"] == "range-distributed candle volume across price bins"
    assert profile["bins"][0]["volume"] == 50
    assert profile["bins"][1]["volume"] == 50
    assert profile["value_area_proxy"]["volume_ratio"] >= 0.7


def test_future_volatility_does_not_relabel_past_displacement_events():
    candles = _candles(45)
    candles[6].update(
        {
            "open": candles[5]["close"],
            "low": candles[5]["close"] - 0.5,
            "high": candles[5]["close"] + 5.0,
            "close": candles[5]["close"] + 4.8,
            "volume": 10_000,
        }
    )
    baseline = _displacement_pack(candles, lookback=40)
    future = _candles(1)
    for index, candle in enumerate(future):
        candle["time"] = f"future-{index}"
        candle["high"] *= 4
        candle["low"] *= 0.25
        candle["volume"] *= 100

    extended = _displacement_pack(candles + future, lookback=40)
    baseline_events = [(event["time"], event["direction"]) for event in baseline["recent"]]
    extended_past_events = [
        (event["time"], event["direction"])
        for event in extended["recent"]
        if not str(event["time"]).startswith("future-")
    ]

    assert baseline_events
    assert extended_past_events == baseline_events


def test_compact_snapshot_for_llm_removes_volume_profile_bins():
    pack = _indicator_pack(_candles(), "15m")
    snapshot = {"symbols": {"crypto": [{"timeframes": {"15m": pack}}]}}

    compact = compact_snapshot_for_llm(snapshot)

    assert "bins" in snapshot["symbols"]["crypto"][0]["timeframes"]["15m"]["volume_profile"]
    assert "bins" in snapshot["symbols"]["crypto"][0]["timeframes"]["15m"]["volume_delta_profile"]
    assert "bins" not in compact["symbols"]["crypto"][0]["timeframes"]["15m"]["volume_profile"]
    assert "bins" not in compact["symbols"]["crypto"][0]["timeframes"]["15m"]["volume_delta_profile"]
    assert "llm_payload_note" in compact


def test_compact_symbol_snapshot_for_llm_keeps_single_target_and_context():
    pack = _indicator_pack(_candles(), "15m")
    eth = {"symbol": "ETHUSDT", "market": "crypto", "status": "ok", "timeframes": {"15m": pack}}
    btc = {"symbol": "BTCUSDT", "market": "crypto", "status": "ok", "timeframes": {"15m": pack}}
    snapshot = {
        "run_id": "run-1",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "monitor_window": {},
        "contexts": {"crypto": {"BTCUSDT": btc}},
        "symbols": {"crypto": [eth, btc], "equity": [{"symbol": "CRCL"}]},
    }

    compact = compact_symbol_snapshot_for_llm(
        snapshot,
        "crypto",
        eth,
        recent_signals=[
            {"symbol": "ETHUSDT", "level": "L3"},
            {"symbol": "BTCUSDT", "level": "L2"},
        ],
    )

    assert [item["symbol"] for item in compact["symbols"]["crypto"]] == ["ETHUSDT"]
    assert compact["symbols"]["equity"] == []
    assert "BTCUSDT" in compact["contexts"]["crypto"]
    assert compact["monitor_window"]["recent_signals_for_symbol"] == [{"symbol": "ETHUSDT", "level": "L3"}]
    assert "bins" not in compact["symbols"]["crypto"][0]["timeframes"]["15m"]["volume_profile"]


def test_compact_equity_snapshot_keeps_index_and_sector_context_but_not_full_peer_payload():
    item = {
        "symbol": "MU",
        "market": "equity",
        "status": "ok",
        "classification": {
            "primary_benchmark": "SOXX",
            "secondary_benchmarks": ["SMH"],
            "peers": ["WDC", "NVDA"],
        },
    }
    snapshot = {
        "contexts": {
            "equity": {
                "SPY": {"status": "ok"},
                "QQQ": {"status": "ok"},
                "SOXX": {"status": "ok"},
                "SMH": {"status": "ok"},
                "WDC": {"status": "ok"},
                "NVDA": {"status": "ok"},
            }
        },
        "symbols": {"crypto": [], "equity": [item]},
    }

    compact = compact_symbol_snapshot_for_llm(snapshot, "equity", item)

    assert set(compact["contexts"]["equity"]) == {"SPY", "QQQ", "SOXX", "SMH"}
    assert compact["symbols"]["equity"] == [item]


def test_watchlist_env_overrides_yaml_symbols():
    settings = Settings(
        watchlist_crypto_symbols="ethusdt, solusdt btcusdt",
        watchlist_equity_symbols="crcl; arm\nwdc,CRCL",
        equity_context_symbols="spy, qqq, xbi",
    )
    system_config = {
        "report": {
            "crypto_symbols": ["BTCUSDT"],
            "equity_symbols": ["INTU"],
            "equity_context_symbols": ["SPY", "SMH"],
        }
    }

    watchlist = resolve_watchlist(system_config, settings)

    assert watchlist["crypto_symbols"] == ["ETHUSDT", "SOLUSDT", "BTCUSDT"]
    assert watchlist["crypto_symbols_source"] == "env"
    assert watchlist["equity_symbols"] == ["CRCL", "ARM", "WDC"]
    assert watchlist["equity_symbols_source"] == "env"
    assert watchlist["equity_context_symbols"] == ["SPY", "QQQ", "XBI"]


@pytest.mark.asyncio
async def test_missing_llm_config_still_archives_snapshot(tmp_path, monkeypatch):
    snapshot = {
        "run_id": "test-run",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "symbols": {
            "crypto": [
                {
                    "symbol": "ETHUSDT",
                    "status": "ok",
                    "price": 1234.5,
                    "change_pct": 1.2,
                    "source": "test",
                    "unavailable": ["real_cvd"],
                }
            ],
            "equity": [],
        },
    }
    archive_path = tmp_path / "snapshots.jsonl"
    saved = {}

    class DummyArchiveRepository:
        async def save_snapshot(self, payload):
            saved["payload"] = payload

    item = snapshot["symbols"]["crypto"][0]

    def fake_events(_system_config, _settings):
        yield IndicatorSnapshotEvent(snapshot, "crypto", "ETHUSDT", item)

    monkeypatch.setattr(llm_protocol_report, "iter_indicator_snapshot_events", fake_events)
    settings = Settings(
        llm_config="",
        llm_api_key="",
        indicator_archive_path=str(archive_path),
    )

    title, body = await llm_protocol_report.build_llm_protocol_report(
        settings,
        {"report": {}},
        2,
        0,
        0,
        [],
        DummyArchiveRepository(),
    )

    assert title == "SPM 2H LLM 协议监控报告"
    assert "LLM 未配置，无法按协议完成判断" in body
    assert "LLM_CONFIG is required" in body
    assert saved["payload"]["run_id"] == "test-run"
    archived = json.loads(archive_path.read_text(encoding="utf-8").strip())
    assert archived["run_id"] == "test-run"


@pytest.mark.asyncio
async def test_stream_llm_protocol_report_parts_calls_llm_per_symbol(tmp_path, monkeypatch):
    protocol_path = tmp_path / "protocol.md"
    protocol_path.write_text("protocol", encoding="utf-8")
    archive_path = tmp_path / "snapshots.jsonl"
    timeframes = {
        "1h": {"atr14": 2.0, "last_bar": {"time": "2026-01-01T01:00:00+00:00"}},
        "4h": {"atr14": 2.0, "last_bar": {"time": "2026-01-01T00:00:00+00:00"}},
        "1d": {"atr14": 5.0, "last_bar": {"time": "2026-01-01T00:00:00+00:00"}},
    }
    eth = {
        "symbol": "ETHUSDT",
        "market": "crypto",
        "status": "ok",
        "price": 100.0,
        "source": "test",
        "timeframes": timeframes,
    }
    btc = {
        "symbol": "BTCUSDT",
        "market": "crypto",
        "status": "ok",
        "price": 200.0,
        "source": "test",
        "timeframes": timeframes,
    }
    first_snapshot = {
        "run_id": "stream-run",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "contexts": {},
        "symbols": {"crypto": [eth], "equity": []},
    }
    second_snapshot = {
        "run_id": "stream-run",
        "generated_at": "2026-01-01T00:00:00+00:00",
        "contexts": {},
        "symbols": {"crypto": [eth, btc], "equity": []},
    }
    calls = []
    saved = {}

    class FakeLlmClient:
        is_configured = True
        display_name = "FineRes"

        def __init__(self, settings):
            self.settings = settings

        async def chat(self, messages):
            calls.append(messages)
            if len(calls) == 1:
                return json.dumps(
                    {
                        "status": "TRADE",
                        "timeframe": "4H",
                        "direction": "LONG",
                        "execution_mode": "NOW",
                        "execution_condition": "当前价保持在99-101时执行",
                        "entry": 100,
                        "entry_zone_low": 99,
                        "entry_zone_high": 101,
                        "stop_loss": 96,
                        "tp1": 108,
                        "tp2": 112,
                        "time_stop": "2根4H K线",
                        "position_r": 0.25,
                        "protocol_setup": "C-H1",
                        "score": 82,
                        "evidence": ["DAY EMA20为95", "4H突破位为99", "4H量比为1.6"],
                        "invalidation": "4H收盘跌破96",
                        "summary": "按4H趋势突破做多",
                        "rejection_reasons": [],
                        "risk_reward": None,
                    },
                    ensure_ascii=False,
                )
            return json.dumps(
                {
                    "status": "NO_TRADE",
                    "timeframe": None,
                    "summary": "本轮不做",
                    "rejection_reasons": ["DAY与4H方向冲突，协议得分仅52"],
                },
                ensure_ascii=False,
            )

    class DummyArchiveRepository:
        async def save_snapshot(self, payload):
            saved["payload"] = payload

    def fake_events(_system_config, _settings):
        yield IndicatorSnapshotEvent(first_snapshot, "crypto", "ETHUSDT", eth)
        yield IndicatorSnapshotEvent(second_snapshot, "crypto", "BTCUSDT", btc)

    monkeypatch.setattr(llm_protocol_report, "OpenAICompatibleClient", FakeLlmClient)
    monkeypatch.setattr(llm_protocol_report, "iter_indicator_snapshot_events", fake_events)
    settings = Settings(
        crypto_protocol_path=str(protocol_path),
        equity_protocol_path=str(protocol_path),
        indicator_archive_path=str(archive_path),
    )

    parts = [
        part
        async for part in llm_protocol_report.stream_llm_protocol_report_parts(
            settings,
            {"report": {}},
            1,
            10,
            2,
            [{"symbol": "ETHUSDT", "level": "L3"}],
            DummyArchiveRepository(),
        )
    ]

    assert len(calls) == 2
    assert [part.symbol for part in parts] == ["ETHUSDT", "BTCUSDT"]
    assert parts[0].has_trade_opportunity is True
    assert parts[0].decision["risk_reward"] == 2.0
    assert parts[0].opportunity_id
    assert "交易机会" in parts[0].title
    assert parts[1].has_trade_opportunity is False
    assert "当前指令：不做" in parts[1].body
    assert saved["payload"]["run_id"] == "stream-run"
    assert json.loads(archive_path.read_text(encoding="utf-8").strip())["run_id"] == "stream-run"
