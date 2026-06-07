from pathlib import Path
from typing import Any

import yaml


CONFIG_ROOT = Path("configs")


def load_yaml(path: str | Path) -> dict[str, Any]:
    with Path(path).open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_system_config(root: Path = CONFIG_ROOT) -> dict[str, Any]:
    return load_yaml(root / "system.yaml")


def load_symbols_config(root: Path = CONFIG_ROOT) -> list[dict[str, Any]]:
    return load_yaml(root / "symbols.yaml").get("symbols", [])


def load_strategy_configs(root: Path = CONFIG_ROOT) -> list[dict[str, Any]]:
    configs: list[dict[str, Any]] = []
    for path in sorted((root / "strategies").glob("*.yaml")):
        data = load_yaml(path)
        if data.get("enabled", True):
            configs.append(data)
    return configs
