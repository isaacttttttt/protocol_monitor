from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


DEFAULT_SECTOR_CONFIG = Path("configs/equity_sectors.yaml")


def load_equity_sector_map(path: str | Path = DEFAULT_SECTOR_CONFIG) -> dict[str, dict[str, Any]]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle) or {}
    raw_symbols = payload.get("symbols", {})
    if not isinstance(raw_symbols, dict):
        return {}

    result: dict[str, dict[str, Any]] = {}
    for raw_symbol, raw_profile in raw_symbols.items():
        if not isinstance(raw_profile, dict):
            continue
        symbol = str(raw_symbol).strip().upper()
        if not symbol:
            continue
        result[symbol] = {
            **raw_profile,
            "asset_type": str(raw_profile.get("asset_type") or "equity"),
            "sector": str(raw_profile.get("sector") or "Unclassified"),
            "industry": str(raw_profile.get("industry") or "Unclassified"),
            "primary_benchmark": str(raw_profile.get("primary_benchmark") or "SPY").upper(),
            "secondary_benchmarks": _symbols(raw_profile.get("secondary_benchmarks")),
            "peers": _symbols(raw_profile.get("peers")),
            "leverage_multiple": float(raw_profile.get("leverage_multiple") or 1),
        }
    return result


def required_equity_context_symbols(
    target_symbols: list[str],
    base_symbols: list[str],
    sector_map: dict[str, dict[str, Any]] | None = None,
) -> list[str]:
    profiles = sector_map if sector_map is not None else load_equity_sector_map()
    result: list[str] = []
    seen: set[str] = set()

    def append(symbol: str) -> None:
        normalized = str(symbol).strip().upper()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)

    for symbol in base_symbols:
        append(symbol)
    for target in target_symbols:
        profile = profiles.get(str(target).upper(), {})
        append(profile.get("primary_benchmark", ""))
        for symbol in profile.get("secondary_benchmarks", []):
            append(symbol)
        for symbol in profile.get("peers", []):
            append(symbol)
    return result


def _symbols(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip().upper() for item in value if str(item).strip()]

