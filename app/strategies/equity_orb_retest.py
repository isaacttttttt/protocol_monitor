from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from statistics import mean
from typing import Any, Literal
from zoneinfo import ZoneInfo

NEW_YORK_TZ = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class OrbRetestConfig:
    """Deterministic opening-range retest parameters."""

    opening_range_minutes: int = 30
    entry_cutoff_et: str = "12:00"
    minimum_premarket_rvol: float = 1.5
    minimum_confirmation_rvol: float = 1.2
    minimum_absolute_gap_pct: float = 1.0
    require_premarket_rvol: bool = False
    retest_buffer_fraction: float = 0.001
    reward_r: float = 2.0


def evaluate_orb_retest(
    candles: list[dict[str, Any]],
    previous_close: float | None,
    *,
    sector_relative_5_pct: float | None = None,
    config: OrbRetestConfig | None = None,
) -> dict[str, Any]:
    """Evaluate a closed-bar ORB breakout/retest without future information."""
    parameters = config or OrbRetestConfig()
    if not candles:
        return {"status": "NO_DATA", "eligible": False}
    session_date = max(_local(candle).date() for candle in candles)
    premarket = [
        candle
        for candle in candles
        if _local(candle).date() == session_date and 4 * 60 <= _minutes(candle) < 9 * 60 + 30
    ]
    regular = [
        candle
        for candle in candles
        if _local(candle).date() == session_date and 9 * 60 + 30 <= _minutes(candle) < 16 * 60
    ]
    expected = max(1, parameters.opening_range_minutes // 15)
    if len(regular) < expected:
        return {"status": "OPENING_RANGE_FORMING", "eligible": False, "bar_count": len(regular)}
    opening = regular[:expected]
    opening_high = max(float(candle["high"]) for candle in opening)
    opening_low = min(float(candle["low"]) for candle in opening)
    session_open = float(opening[0]["open"])
    gap_pct = ((session_open / previous_close) - 1) * 100 if previous_close else None
    premarket_rvol = _premarket_rvol(candles, session_date, premarket)
    filters = {
        "gap": gap_pct is not None and abs(gap_pct) >= parameters.minimum_absolute_gap_pct,
        "premarket_rvol": (
            premarket_rvol is not None and premarket_rvol >= parameters.minimum_premarket_rvol
        ) or (premarket_rvol is None and not parameters.require_premarket_rvol),
        "liquid_premarket": bool(premarket),
    }
    base = {
        "filters": filters,
        "gap_pct": gap_pct,
        "premarket_rvol": premarket_rvol,
        "premarket_volume_quality": "AVAILABLE" if premarket_rvol is not None else "UNAVAILABLE_PROVIDER_ZERO_VOLUME",
        "opening_high": opening_high,
        "opening_low": opening_low,
    }
    if not all(filters.values()):
        return {"status": "FILTERED", "eligible": False, **base}

    cutoff = _parse_minutes(parameters.entry_cutoff_et)
    breakout: Literal["LONG", "SHORT"] | None = None
    breakout_index: int | None = None
    cumulative = list(opening)
    for index, candle in enumerate(regular[expected:], start=expected):
        if _minutes(candle) >= cutoff:
            break
        cumulative.append(candle)
        close = float(candle["close"])
        session_vwap = _vwap(cumulative)
        if breakout is None:
            if close > opening_high and close > session_vwap and _sector_allows("LONG", sector_relative_5_pct):
                breakout = "LONG"
                breakout_index = index
            elif close < opening_low and close < session_vwap and _sector_allows("SHORT", sector_relative_5_pct):
                breakout = "SHORT"
                breakout_index = index
            continue
        if breakout_index is None or index <= breakout_index:
            continue
        current_time = _local(candle)
        prior_volumes = [
            float(item.get("volume") or 0.0)
            for item in candles
            if _local(item) < current_time and _is_regular(item) and float(item.get("volume") or 0.0) > 0
        ]
        average_volume = mean(prior_volumes[-20:]) if prior_volumes else 0.0
        confirmation_rvol = float(candle.get("volume") or 0.0) / average_volume if average_volume else None
        level = opening_high if breakout == "LONG" else opening_low
        buffer = level * parameters.retest_buffer_fraction
        if breakout == "LONG":
            confirmed = float(candle["low"]) <= level + buffer and close > level and close > session_vwap
            stop = min(float(candle["low"]), level - buffer)
            risk = close - stop
            target = close + risk * parameters.reward_r
        else:
            confirmed = float(candle["high"]) >= level - buffer and close < level and close < session_vwap
            stop = max(float(candle["high"]), level + buffer)
            risk = stop - close
            target = close - risk * parameters.reward_r
        if confirmed and confirmation_rvol is not None and confirmation_rvol >= parameters.minimum_confirmation_rvol and risk > 0:
            return {
                "status": "TRIGGERED",
                "eligible": True,
                "direction": breakout,
                "signal_time": candle["time"],
                "entry_reference": close,
                "stop": stop,
                "target": target,
                "reward_r": parameters.reward_r,
                "confirmation_rvol": confirmation_rvol,
                "exit_policy": "no new entries after cutoff; close Micro exposure by 16:00 ET",
                **base,
            }
    return {
        "status": "WAIT_RETEST" if breakout else "WAIT_BREAKOUT",
        "eligible": True,
        "direction": breakout,
        "entry_cutoff_et": parameters.entry_cutoff_et,
        **base,
    }


def _premarket_rvol(candles: list[dict[str, Any]], session_date: date, current: list[dict[str, Any]]) -> float | None:
    if not current:
        return None
    cutoff = max(_minutes(candle) for candle in current)
    by_date: dict[date, float] = {}
    for candle in candles:
        local = _local(candle)
        if local.date() >= session_date or not 4 * 60 <= _minutes(candle) <= cutoff:
            continue
        by_date[local.date()] = by_date.get(local.date(), 0.0) + float(candle.get("volume") or 0.0)
    if not by_date:
        return None
    current_volume = sum(float(candle.get("volume") or 0.0) for candle in current)
    baseline = mean(list(by_date.values())[-20:])
    return current_volume / baseline if baseline else None


def _sector_allows(direction: str, sector_relative: float | None) -> bool:
    if sector_relative is None:
        return True
    return sector_relative >= 0 if direction == "LONG" else sector_relative <= 0


def _vwap(candles: list[dict[str, Any]]) -> float:
    volume = sum(float(candle.get("volume") or 0.0) for candle in candles)
    if volume <= 0:
        return float(candles[-1]["close"])
    numerator = sum(
        ((float(candle["high"]) + float(candle["low"]) + float(candle["close"])) / 3)
        * float(candle.get("volume") or 0.0)
        for candle in candles
    )
    return numerator / volume


def _local(candle: dict[str, Any]) -> datetime:
    parsed = datetime.fromisoformat(str(candle["time"]).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=NEW_YORK_TZ)
    return parsed.astimezone(NEW_YORK_TZ)


def _minutes(candle: dict[str, Any]) -> int:
    local = _local(candle)
    return local.hour * 60 + local.minute


def _is_regular(candle: dict[str, Any]) -> bool:
    return 9 * 60 + 30 <= _minutes(candle) < 16 * 60


def _parse_minutes(value: str) -> int:
    hour, minute = value.split(":", maxsplit=1)
    return int(hour) * 60 + int(minute)
