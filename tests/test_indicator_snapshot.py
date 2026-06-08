import json
from datetime import datetime, timedelta, timezone

import pytest

from app.config.settings import Settings
from app.review import llm_protocol_report
from app.review.indicator_snapshot import _indicator_pack


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
    assert "swept_recent_high_and_closed_back_inside" in pack["liquidity"]


@pytest.mark.asyncio
async def test_missing_deepseek_key_still_archives_snapshot(tmp_path, monkeypatch):
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

    monkeypatch.setattr(llm_protocol_report, "build_indicator_snapshot", lambda _system_config: dict(snapshot))
    settings = Settings(
        deepseek_api_key="",
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

    assert title == "SPM 2H DeepSeek 协议监控报告"
    assert "DeepSeek API Key 未配置" in body
    assert saved["payload"]["run_id"] == "test-run"
    archived = json.loads(archive_path.read_text(encoding="utf-8").strip())
    assert archived["run_id"] == "test-run"
