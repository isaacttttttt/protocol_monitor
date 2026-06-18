from app.config.settings import Settings
from app.llm.openai_compatible import OpenAICompatibleClient


def test_selected_llm_config_uses_yaml_payload_without_thinking(tmp_path):
    config_dir = tmp_path / "llms"
    config_dir.mkdir()
    (config_dir / "fineres.yaml").write_text(
        "\n".join(
            [
                "provider_name: FineRes",
                "api_key_env: LLM_API_KEY",
                "base_url: https://it-ai.fineres.com/v1",
                "chat_completions_path: /chat/completions",
                "model: gpt-5.5",
                "allowed_params:",
                "  - max_tokens",
                "  - temperature",
                "parameters:",
                "  max_tokens: 6000",
                "  temperature: 0.2",
                "  thinking:",
                "    type: enabled",
            ]
        ),
        encoding="utf-8",
    )
    client = OpenAICompatibleClient(
        Settings(
            llm_config="fineres",
            llm_config_dir=str(config_dir),
            llm_api_key="test-key",
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


def test_missing_llm_config_is_not_configured():
    client = OpenAICompatibleClient(Settings(llm_config="", llm_api_key="test-key"))

    assert client.display_name == "LLM"
    assert client.is_configured is False
    assert client.missing_config_keys == ["LLM_CONFIG is required"]


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
