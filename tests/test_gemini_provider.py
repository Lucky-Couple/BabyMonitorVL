import pytest

from babymonitorvl.providers.gemini import GeminiBackend, normalize_gemini_usage


class FakeModel:
    def __init__(self, name: str, actions: list[str]) -> None:
        self.name = name
        self.supported_actions = actions


class FakeModels:
    def list(self, config):
        assert config["query_base"] is True
        return [
            FakeModel("models/gemini-3.5-flash", ["generateContent", "countTokens"]),
            FakeModel("models/gemini-3.1-pro-preview", ["generate_content"]),
            FakeModel("models/gemini-3.1-flash-image", ["generateContent"]),
            FakeModel("models/gemini-2.5-flash-tts", ["generateContent"]),
            FakeModel("models/text-embedding-005", ["embedContent"]),
        ]


class FakeClient:
    models = FakeModels()


@pytest.mark.asyncio
async def test_gemini_without_key_is_reported_unavailable() -> None:
    backend = GeminiBackend(None)
    health = await backend.healthcheck()
    assert health.available is False
    assert "GEMINI_API_KEY" in health.detail


@pytest.mark.asyncio
async def test_gemini_lists_only_compatible_image_analysis_models() -> None:
    backend = GeminiBackend("test-key")
    backend._client = FakeClient()
    health = await backend.healthcheck()
    assert health.available is True
    assert health.models == ["gemini-3.1-pro-preview", "gemini-3.5-flash"]


def test_gemini_usage_includes_thinking_tokens_as_output() -> None:
    usage = normalize_gemini_usage(
        {
            "prompt_token_count": 100,
            "candidates_token_count": 30,
            "thoughts_token_count": 20,
            "total_token_count": 150,
        }
    )
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 50
    assert usage["total_tokens"] == 150
