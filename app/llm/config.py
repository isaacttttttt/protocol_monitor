from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from app.config.settings import Settings


class LlmConfigError(ValueError):
    """Raised when a selected LLM configuration file cannot be used."""


@dataclass(frozen=True)
class LlmProviderConfig:
    name: str
    provider_name: str
    api_key_env: str
    base_url: str = ""
    chat_completions_url: str = ""
    chat_completions_path: str = "/chat/completions"
    model: str = ""
    timeout_seconds: int = 300
    parameters: dict[str, Any] | None = None
    allowed_params: tuple[str, ...] = ()
    source_path: Path | None = None

    def payload_parameters(self) -> dict[str, Any]:
        params = _clean_payload_dict(self.parameters or {})
        if not self.allowed_params:
            return params
        allowed = set(self.allowed_params)
        return {key: value for key, value in params.items() if key in allowed}


def load_llm_provider_config(config_name: str, config_dir: str | Path) -> LlmProviderConfig:
    path = _resolve_config_path(config_name, config_dir)
    if not path.exists():
        raise LlmConfigError(f"LLM config not found: {path}")

    with path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle) or {}
    if not isinstance(data, dict):
        raise LlmConfigError(f"LLM config must be a YAML mapping: {path}")
    if data.get("api_key"):
        raise LlmConfigError(f"Do not put api_key in {path}; use an environment variable instead")

    parameters = data.get("parameters") or data.get("request_parameters") or {}
    if not isinstance(parameters, dict):
        raise LlmConfigError(f"LLM config parameters must be a mapping: {path}")

    allowed_params = data.get("allowed_params") or []
    if not isinstance(allowed_params, list):
        raise LlmConfigError(f"LLM config allowed_params must be a list: {path}")

    return LlmProviderConfig(
        name=path.stem,
        provider_name=str(data.get("provider_name") or data.get("name") or path.stem),
        api_key_env=str(data.get("api_key_env") or "LLM_API_KEY"),
        base_url=str(data.get("base_url") or ""),
        chat_completions_url=str(data.get("chat_completions_url") or ""),
        chat_completions_path=str(data.get("chat_completions_path") or "/chat/completions"),
        model=str(data.get("model") or ""),
        timeout_seconds=int(data.get("timeout_seconds") or 300),
        parameters=parameters,
        allowed_params=tuple(str(item) for item in allowed_params),
        source_path=path,
    )


def resolve_config_api_key(settings: Settings, config: LlmProviderConfig) -> str:
    env_name = config.api_key_env.strip()
    if env_name == "LLM_API_KEY":
        return settings.llm_api_key.strip()
    if env_name == "DEEPSEEK_API_KEY":
        return settings.deepseek_api_key.strip()
    return os.getenv(env_name, "").strip()


def _resolve_config_path(config_name: str, config_dir: str | Path) -> Path:
    clean_name = config_name.strip().strip("'\"")
    if not clean_name:
        raise LlmConfigError("LLM_CONFIG is empty")

    root = Path(config_dir or "configs/llms").resolve()
    raw_path = Path(clean_name)
    if raw_path.suffix not in {".yaml", ".yml"}:
        raw_path = raw_path.with_suffix(".yaml")
    candidate = raw_path if raw_path.is_absolute() else root / raw_path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise LlmConfigError(f"LLM config must be under {root}: {resolved}") from exc
    return resolved


def _clean_payload_dict(values: dict[str, Any]) -> dict[str, Any]:
    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        cleaned_value = _clean_payload_value(value)
        if cleaned_value is not None:
            cleaned[str(key)] = cleaned_value
    return cleaned


def _clean_payload_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        nested = _clean_payload_dict(value)
        return nested or None
    if isinstance(value, list):
        return [_clean_payload_value(item) for item in value]
    return value
