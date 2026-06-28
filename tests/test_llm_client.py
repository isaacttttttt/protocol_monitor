from app.config.settings import Settings
from app.llm.openai_compatible import OpenAICompatibleClient
from app.review.llm_protocol_report import _symbol_messages


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


def test_symbol_prompt_assigns_final_judgment_to_llm_and_separates_books():
    messages = _symbol_messages(
        1,
        "MU",
        "equity",
        {
            "mode": "single_symbol_protocol_analysis",
            "factor_contract": {"code_role": "observations_only", "llm_role": "final_judgment"},
            "symbols": {"equity": [{"symbol": "MU"}], "crypto": []},
        },
        "protocol",
    )

    prompt = messages[1]["content"]
    assert "Micro 结论" in prompt
    assert "Macro 结论" in prompt
    assert "最终评分与交易判断由你完成" in prompt
    assert "不得把候选形态当成已触发" in prompt
