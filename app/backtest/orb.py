from __future__ import annotations

from datetime import UTC
from typing import Sequence

from app.backtest.engine import EntryIntent
from app.market.models import Kline
from app.strategies.equity_orb_retest import OrbRetestConfig, evaluate_orb_retest


def build_orb_intents(
    bars: Sequence[Kline],
    previous_closes: dict[str, float],
    *,
    config: OrbRetestConfig | None = None,
    sector_relative_by_date: dict[str, float] | None = None,
    risk_fraction: float = 0.0025,
) -> dict[int, EntryIntent]:
    """Replay ORB decisions prefix-by-prefix so future bars are unavailable."""
    intents: dict[int, EntryIntent] = {}
    seen_signal_times: set[str] = set()
    rows: list[dict] = []
    for index, bar in enumerate(bars):
        timestamp = bar.open_time.replace(tzinfo=UTC).isoformat()
        rows.append(
            {
                "time": timestamp,
                "open": float(bar.open),
                "high": float(bar.high),
                "low": float(bar.low),
                "close": float(bar.close),
                "volume": float(bar.volume),
            }
        )
        date_key = bar.open_time.date().isoformat()
        decision = evaluate_orb_retest(
            rows,
            previous_closes.get(date_key),
            sector_relative_5_pct=(sector_relative_by_date or {}).get(date_key),
            config=config,
        )
        signal_time = str(decision.get("signal_time") or "")
        if decision.get("status") != "TRIGGERED" or not signal_time or signal_time in seen_signal_times:
            continue
        seen_signal_times.add(signal_time)
        intents[index] = EntryIntent(
            direction=decision["direction"],
            stop=float(decision["stop"]),
            target=float(decision["target"]),
            reason="deterministic_orb_retest",
            risk_fraction=risk_fraction,
        )
    return intents
