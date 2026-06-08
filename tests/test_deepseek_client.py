from app.llm.deepseek import normalize_thinking_options


def test_thinking_high_is_normalized_to_enabled_with_high_effort():
    assert normalize_thinking_options("high", "") == ("enabled", "high")


def test_enabled_uses_configured_max_effort():
    assert normalize_thinking_options("enabled", "max") == ("enabled", "max")


def test_disabled_keeps_thinking_off():
    assert normalize_thinking_options("disabled", "max") == ("disabled", "max")
