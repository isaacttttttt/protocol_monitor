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


class DeepSeekClient:
    def __init__(self, settings: Settings) -> None:
        self.api_key = settings.deepseek_api_key
        self.base_url = settings.deepseek_base_url.rstrip("/")
        self.model = settings.deepseek_model
        self.thinking = settings.deepseek_thinking
        self.reasoning_effort = settings.deepseek_reasoning_effort
        self.temperature = settings.deepseek_temperature
        self.max_tokens = settings.deepseek_max_tokens
        self.timeout_seconds = settings.deepseek_timeout_seconds

    @property
    def is_configured(self) -> bool:
        return bool(self.api_key)

    async def chat(self, messages: list[dict[str, str]]) -> str:
        if not self.api_key:
            raise RuntimeError("DEEPSEEK_API_KEY is not configured")

        thinking_type, reasoning_effort = normalize_thinking_options(self.thinking, self.reasoning_effort)
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "max_tokens": self.max_tokens,
        }
        if thinking_type:
            payload["thinking"] = {"type": thinking_type}
        if reasoning_effort and thinking_type != "disabled":
            payload["reasoning_effort"] = reasoning_effort
        if thinking_type in (None, "disabled"):
            payload["temperature"] = self.temperature
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        timeout = aiohttp.ClientTimeout(total=self.timeout_seconds)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.base_url}/chat/completions", json=payload, headers=headers) as response:
                    text = await response.text()
                    if response.status >= 400:
                        raise RuntimeError(f"DeepSeek API HTTP {response.status}: {text[:500] or '<empty response body>'}")
                    data = await response.json()
        except asyncio.TimeoutError as exc:
            raise RuntimeError(f"DeepSeek API timeout after {self.timeout_seconds}s") from exc
        except aiohttp.ClientError as exc:
            raise RuntimeError(f"DeepSeek API request failed: {exc.__class__.__name__}: {exc}") from exc
        choices = data.get("choices") or []
        if not choices:
            raise RuntimeError("DeepSeek API returned no choices")
        content = choices[0].get("message", {}).get("content", "")
        if not content:
            raise RuntimeError("DeepSeek API returned empty content")
        return str(content).strip()


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
