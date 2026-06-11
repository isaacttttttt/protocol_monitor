from app.llm.openai_compatible import OpenAICompatibleClient, normalize_thinking_options


class DeepSeekClient(OpenAICompatibleClient):
    """Backward-compatible alias for the generic OpenAI-compatible client."""
