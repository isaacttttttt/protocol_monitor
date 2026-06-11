from app.llm.deepseek import normalize_thinking_options
from app.config.settings import Settings
from app.llm.openai_compatible import OpenAICompatibleClient


def test_thinking_high_is_normalized_to_enabled_with_high_effort():
    assert normalize_thinking_options("high", "") == ("enabled", "high")


def test_enabled_uses_configured_max_effort():
    assert normalize_thinking_options("enabled", "max") == ("enabled", "max")


def test_disabled_keeps_thinking_off():
    assert normalize_thinking_options("disabled", "max") == ("disabled", "max")


def test_selected_llm_config_uses_yaml_payload_without_thinking():
    client = OpenAICompatibleClient(
        Settings(
            llm_config="fineres",
            llm_api_key="test-key",
            llm_thinking="enabled",
            llm_reasoning_effort="max",
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
    assert "thinking" not in payload
    assert "reasoning_effort" not in payload


def test_selected_deepseek_config_can_send_provider_specific_params():
    client = OpenAICompatibleClient(Settings(llm_config="deepseek", llm_api_key="test-key"))

    payload = client._payload([])

    assert client.display_name == "DeepSeek"
    assert client.chat_url == "https://api.deepseek.com/chat/completions"
    assert payload["thinking"] == {"type": "enabled"}
    assert payload["reasoning_effort"] == "max"
    assert "temperature" not in payload


def test_llm_config_allowed_params_filters_unknown_values(tmp_path):
    config_dir = tmp_path / "llms"
    config_dir.mkdir()
    (config_dir / "custom.yaml").write_text(
        "\n".join(
            [
                "provider_name: Custom",
                "api_key_env: LLM_API_KEY",
                "base_url: https://example.test/v1",
                "model: custom-model",
                "allowed_params:",
                "  - max_tokens",
                "parameters:",
                "  max_tokens: 1000",
                "  thinking:",
                "    type: enabled",
            ]
        ),
        encoding="utf-8",
    )
    client = OpenAICompatibleClient(
        Settings(llm_config="custom", llm_config_dir=str(config_dir), llm_api_key="test-key")
    )

    assert client._payload([]) == {"model": "custom-model", "messages": [], "max_tokens": 1000}


def test_generic_llm_uses_openai_compatible_url_and_payload():
    client = OpenAICompatibleClient(
        Settings(
            llm_config="",
            llm_provider_name="FineRes",
            llm_api_key="test-key",
            llm_base_url="https://it-ai.fineres.com/v1",
            llm_model="gpt-5.5",
            llm_thinking="enabled",
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
    assert "thinking" not in payload


def test_generic_llm_reasoning_effort_uses_openai_values_only():
    medium_client = OpenAICompatibleClient(
        Settings(
            llm_config="",
            llm_api_key="test-key",
            llm_base_url="https://it-ai.fineres.com/v1",
            llm_model="gpt-5.5",
            llm_reasoning_effort="medium",
        )
    )
    max_client = OpenAICompatibleClient(
        Settings(
            llm_config="",
            llm_api_key="test-key",
            llm_base_url="https://it-ai.fineres.com/v1",
            llm_model="gpt-5.5",
            llm_reasoning_effort="max",
        )
    )

    assert medium_client._payload([])["reasoning_effort"] == "medium"
    assert "reasoning_effort" not in max_client._payload([])


def test_generic_llm_accepts_full_chat_completions_url():
    client = OpenAICompatibleClient(
        Settings(
            llm_config="",
            llm_api_key="test-key",
            llm_chat_completions_url="https://it-ai.fineres.com/v1/chat/completions",
            llm_model="gpt-5.5",
        )
    )

    assert client.chat_url == "https://it-ai.fineres.com/v1/chat/completions"
