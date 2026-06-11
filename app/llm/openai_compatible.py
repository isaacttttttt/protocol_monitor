from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from app.config.settings import Settings

THINKING_TYPES = {"adaptive", "enabled", "disabled"}
REASONING_EFFORTS = {"high", "max"}
EFFORT_ALIASES = {
    "low": "high",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
    "max": "max",
}


class OpenAICompatibleClient:
    def __init__(self, settings: Settings) -> None:
        self.using_generic_config = bool(
            settings.llm_api_key
            or settings.llm_base_url
            or settings.llm_chat_completions_url
            or settings.llm_model
        )
        self.provider_name = _provider_name(settings, self.using_generic_config)
        self.api_key = settings.llm_api_key if self.using_generic_config else settings.deepseek_api_key
        self.model = settings.llm_model if self.using_generic_config and settings.llm_model else settings.deepseek_model
        self.chat_url = _chat_completions_url(settings, self.using_generic_config)
        self.thinking = settings.llm_thinking if self.using_generic_config else settings.deepseek_thinking
        self.reasoning_effort = settings.llm_reasoning_effort if self.using_generic_config else settings.deepseek_reasoning_effort
        self.temperature = settings.llm_temperature if self.using_generic_config else settings.deepseek_temperature
        self.max_tokens = settings.llm_max_tokens if self.using_generic_config else settings.deepseek_max_tokens
        self.timeout_seconds = settings.llm_timeout_seconds if self.using_generic_config else settings.deepseek_timeout_seconds

    @property
    def display_name(self) -> str:
        return self.provider_name or "大模型"

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key and self.chat_url and self.model)

    @property
    def missing_config_keys(self) -> list[str]:
        keys: list[str] = []
        prefix = "LLM" if self.using_generic_config else "DEEPSEEK"
        if not self.api_key:
            keys.append(f"{prefix}_API_KEY")
        if not self.chat_url:
            keys.append(f"{prefix}_BASE_URL")
        if not self.model:
            keys.append(f"{prefix}_MODEL")
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
            "max_tokens": self.max_tokens,
        }
        thinking_type, reasoning_effort = normalize_thinking_options(self.thinking, self.reasoning_effort)
        if thinking_type:
            payload["thinking"] = {"type": thinking_type}
        if reasoning_effort and thinking_type != "disabled":
            payload["reasoning_effort"] = reasoning_effort
        if thinking_type in (None, "disabled"):
            payload["temperature"] = self.temperature
        return payload


def normalize_thinking_options(thinking: str | None, reasoning_effort: str | None) -> tuple[str | None, str | None]:
    raw_thinking = (thinking or "").strip().lower()
    raw_effort = (reasoning_effort or "").strip().lower()

    effort = raw_effort if raw_effort in REASONING_EFFORTS else None
    if raw_effort in EFFORT_ALIASES:
        effort = EFFORT_ALIASES[raw_effort]

    if raw_thinking in THINKING_TYPES:
        return raw_thinking, effort

    if raw_thinking in EFFORT_ALIASES:
        return "enabled", effort or EFFORT_ALIASES[raw_thinking]

    if not raw_thinking:
        return None, effort

    return "enabled", effort or "high"


def _provider_name(settings: Settings, using_generic_config: bool) -> str:
    if settings.llm_provider_name:
        return settings.llm_provider_name
    if using_generic_config:
        return "LLM"
    return "DeepSeek"


def _chat_completions_url(settings: Settings, using_generic_config: bool) -> str:
    if using_generic_config:
        if settings.llm_chat_completions_url:
            return _clean_url(settings.llm_chat_completions_url)
        return _join_chat_path(settings.llm_base_url, settings.llm_chat_completions_path)
    return _join_chat_path(settings.deepseek_base_url, "/chat/completions")


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
