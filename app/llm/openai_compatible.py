from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from app.config.settings import Settings
from app.llm.config import LlmConfigError, load_llm_provider_config, resolve_config_api_key


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.config_name = settings.llm_config.strip()
        self.config_source = ""
        self.configuration_error = ""
        self.api_key_env = "LLM_API_KEY"
        self.provider_name = ""
        self.api_key = ""
        self.model = ""
        self.chat_url = ""
        self.timeout_seconds = 300
        self.payload_parameters: dict[str, Any] = {}

        if not self.config_name:
            self.provider_name = "LLM"
            self.configuration_error = "LLM_CONFIG is required"
            return
        self._init_from_config_file(settings)

    def _init_from_config_file(self, settings: Settings) -> None:
        try:
            config = load_llm_provider_config(settings.llm_config, settings.llm_config_dir)
        except LlmConfigError as exc:
            self.provider_name = settings.llm_config.strip() or "LLM"
            self.configuration_error = str(exc)
            return

        self.provider_name = config.provider_name
        self.api_key_env = config.api_key_env
        self.api_key = resolve_config_api_key(settings, config)
        self.model = config.model
        self.chat_url = _join_chat_path(config.base_url, config.chat_completions_path)
        if config.chat_completions_url:
            self.chat_url = _clean_url(config.chat_completions_url)
        self.timeout_seconds = config.timeout_seconds
        self.payload_parameters = config.payload_parameters()
        self.config_source = str(config.source_path or "")

    @property
    def display_name(self) -> str:
        return self.provider_name or "大模型"

    @property
    def is_configured(self) -> bool:
        return bool(not self.configuration_error and self.api_key and self.chat_url and self.model)

    @property
    def missing_config_keys(self) -> list[str]:
        if self.configuration_error:
            return [self.configuration_error]
        keys: list[str] = []
        if not self.api_key:
            keys.append(self.api_key_env)
        if not self.chat_url:
            keys.append(_missing_key_name(self, "base_url or chat_completions_url"))
        if not self.model:
            keys.append(_missing_key_name(self, "model"))
        return keys

    async def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.is_configured:
            missing = ", ".join(self.missing_config_keys) or "LLM configuration"
            raise RuntimeError(f"{self.display_name} is not configured: {missing}")

        payload = self._payload(messages)
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(self.chat_url, json=payload, headers=headers) as response:
                    text = await response.text()
                    if response.status >= 400:
                        raise RuntimeError(f"{self.display_name} API HTTP {response.status}: {text[:500] or '<empty response body>'}")
                    data = await response.json()
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"{self.display_name} API timeout after {self.timeout_seconds}s") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"{self.display_name} API request failed: {exc.__class__.__name__}: {exc}") from exc

        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError(f"{self.display_name} API returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError(f"{self.display_name} API returned empty content")
        return str(content).strip()

    def _payload(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
        }
        payload.update(self.payload_parameters)
        return payload


def _missing_key_name(client: OpenAICompatibleClient, key: str) -> str:
    source = client.config_source or f"configs/llms/{client.config_name}.yaml"
    return f"{source}: {key}"


def _join_chat_path(base_url: str, path: str) -> str:
    base = _clean_url(base_url)
    if not base:
        return ""
    if base.rstrip("/").endswith("/chat/completions"):
        return base.rstrip("/")
    clean_path = (path or "/chat/completions").strip()
    if not clean_path.startswith("/"):
        clean_path = f"/{clean_path}"
    return f"{base.rstrip('/')}{clean_path}"


def _clean_url(value: str) -> str:
    return value.strip().strip("'\"").rstrip("/")
