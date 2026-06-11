from app.llm.deepseek import normalize_thinking_options
from app.config.settings import Settings
from app.llm.openai_compatible import OpenAICompatibleClient


def test_thinking_high_is_normalized_to_enabled_with_high_effort():
    assert normalize_thinking_options("high", "") == ("enabled", "high")


def test_enabled_uses_configured_max_effort():
    assert normalize_thinking_options("enabled", "max") == ("enabled", "max")


def test_disabled_keeps_thinking_off():
    assert normalize_thinking_options("disabled", "max") == ("disabled", "max")


def test_generic_llm_uses_openai_compatible_url_and_payload():
    client = OpenAICompatibleClient(
        Settings(
            llm_provider_name="FineRes",
            llm_api_key="test-key",
            llm_base_url="https://it-ai.fineres.com/v1",
            llm_model="gpt-5.5",
        )
    )

    payload = client._payload([{"role": "user", "content": "Hello!"}])

    assert client.display_name == "FineRes"
    assert client.chat_url == "https://it-ai.fineres.com/v1/chat/completions"
    assert client.is_configured is True
    assert payload == {
        "model": "gpt-5.5",
        "messages": [{"role": "user", "content": "Hello!"}],
        "max_tokens": 6000,
        "temperature": 0.2,
    }


def test_generic_llm_accepts_full_chat_completions_url():
    client = OpenAICompatibleClient(
        Settings(
            llm_api_key="test-key",
            llm_chat_completions_url="https://it-ai.fineres.com/v1/chat/completions",
            llm_model="gpt-5.5",
        )
    )

    assert client.chat_url == "https://it-ai.fineres.com/v1/chat/completions"
