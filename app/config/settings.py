from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "dev"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./spm.db"
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    feishu_webhook_url: str = ""
    feishu_keyword: str = "监控报告"
    binance_ws_base: str = "wss://fstream.binance.com/ws"
    binance_rest_base: str = "https://fapi.binance.com"
    okx_rest_base: str = "https://www.okx.com"
    yahoo_chart_base: str = "https://query1.finance.yahoo.com/v8/finance/chart"
    watchlist_crypto_symbols: str = ""
    watchlist_equity_symbols: str = ""
    equity_context_symbols: str = ""
    llm_provider_name: str = ""
    llm_api_key: str = ""
    llm_base_url: str = ""
    llm_chat_completions_url: str = ""
    llm_chat_completions_path: str = "/chat/completions"
    llm_model: str = ""
    llm_thinking: str = ""
    llm_reasoning_effort: str = ""
    llm_temperature: float = 0.2
    llm_max_tokens: int = 6000
    llm_timeout_seconds: int = 300
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-v4-pro"
    deepseek_thinking: str = "enabled"
    deepseek_reasoning_effort: str = "max"
    deepseek_temperature: float = 0.2
    deepseek_max_tokens: int = 6000
    deepseek_timeout_seconds: int = 300
    indicator_archive_path: str = "data/indicator_snapshots.jsonl"
    crypto_protocol_path: str = "protocols/crypto_smartmoney_protocol_v16.md"
    equity_protocol_path: str = "protocols/equity_smartmoney_protocol_v17.md"

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")


@lru_cache
def get_settings() -> Settings:
    return Settings()
