import asyncio
from types import SimpleNamespace

import pytest

from babymonitorvl.coordinates import BoxCoordinateOrder
from babymonitorvl.prompt import output_schema
from babymonitorvl.providers import AnalysisRequest
from babymonitorvl.providers.gemini import (
    GEMINI_SCHEMA_KEYWORDS,
    GEMINI_SCHEMA_PROFILE,
    GeminiBackend,
    gemini_compatible_schema,
    normalize_gemini_usage,
)


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
            FakeModel("models/gemma-4-31b-it", ["generateContent"]),
            FakeModel("models/gemma-4-26b-a4b-it", ["generate_content"]),
            FakeModel("models/gemma-3-27b-it", ["generateContent"]),
            FakeModel("models/gemma-2-27b-it", ["generateContent"]),
            FakeModel("models/gemini-3.1-flash-image", ["generateContent"]),
            FakeModel("models/gemini-2.5-flash-tts", ["generateContent"]),
            FakeModel("models/text-embedding-005", ["embedContent"]),
        ]


class FakeClient:
    models = FakeModels()


class FakeInteractions:
    def __init__(self) -> None:
        self.kwargs = None

    async def create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(
            output_text='{"ok":true}',
            usage=SimpleNamespace(
                model_dump=lambda **_: {
                    "total_input_tokens": 120,
                    "total_output_tokens": 30,
                    "total_thought_tokens": 10,
                    "total_tokens": 160,
                }
            ),
        )


class FakeAsyncClient:
    def __init__(self) -> None:
        self.interactions = FakeInteractions()
        self.closed = False

    async def aclose(self) -> None:
        self.closed = True


class FakeAnalyzeClient:
    def __init__(self) -> None:
        self.aio = FakeAsyncClient()
        self.closed = False

    def close(self) -> None:
        self.closed = True


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
    assert health.models == [
        "gemini-3.1-pro-preview",
        "gemini-3.5-flash",
        "gemma-4-26b-a4b-it",
        "gemma-4-31b-it",
    ]


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


def test_gemini_schema_adapter_keeps_only_documented_transport_subset() -> None:
    source = {
        "type": "object",
        "properties": {
            "version": {"const": "1.1", "default": "1.1", "type": "string"},
            "summary": {"type": "string", "maxLength": 500, "pattern": "x"},
            "score": {"type": "number", "minimum": 0, "maximum": 1},
            "quality": {"$ref": "#/$defs/Quality"},
        },
        "required": ["version", "summary", "score", "quality"],
        "additionalProperties": False,
        "$defs": {
            "Quality": {
                "type": "string",
                "enum": ["good", "poor"],
                "title": "Quality",
            }
        },
        "$schema": "https://json-schema.org/draft/2020-12/schema",
    }

    adapted = gemini_compatible_schema(source)

    assert adapted == {
        "type": "object",
        "properties": {
            "version": {"enum": ["1.1"], "type": "string"},
            "summary": {"type": "string"},
            "score": {"type": "number"},
            "quality": {"type": "string", "enum": ["good", "poor"]},
        },
        "required": ["version", "summary", "score", "quality"],
        "additionalProperties": False,
    }
    assert source["properties"]["version"]["const"] == "1.1"


def test_project_schema_is_google_transport_compatible_after_adaptation() -> None:
    adapted = gemini_compatible_schema(output_schema(BoxCoordinateOrder.YXYX))

    assert adapted["properties"]["schema_version"]["enum"] == ["1.3"]
    assert "adult_presence" in adapted["required"]
    assert "adults" in adapted["required"]
    assert "adult_box" in adapted["properties"]["adults"]["items"]["properties"]
    infant_properties = adapted["properties"]["infants"]["items"]["properties"]
    infant_required = adapted["properties"]["infants"]["items"]["required"]
    assert "mouth_nose_box" in infant_properties
    assert "mouth_nose_box" in infant_required
    assert "mouth_nose_occlusion" in infant_properties
    assert gemini_compatible_schema(adapted) == adapted

    def assert_keywords(node: dict, *, property_map: bool = False) -> None:
        for key, value in node.items():
            if not property_map:
                assert key in GEMINI_SCHEMA_KEYWORDS
            if key == "properties" and isinstance(value, dict):
                for child in value.values():
                    assert_keywords(child)
            elif key == "anyOf" and isinstance(value, list):
                for child in value:
                    assert_keywords(child)
            elif key in {"items", "additionalProperties"} and isinstance(value, dict):
                assert_keywords(value)

    assert_keywords(adapted)
    serialized = str(adapted)
    assert "'const'" not in serialized
    assert "'default'" not in serialized
    assert "'maxLength'" not in serialized
    assert "'$defs'" not in serialized
    assert "'$ref'" not in serialized
    assert "'minimum'" not in serialized
    assert "'maximum'" not in serialized
    assert "additionalProperties" not in adapted["properties"]["infants"]["items"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model",
    ["gemma-4-31b-it", "gemini-3.1-pro-preview", "gemini-2.0-flash", "future-image-model"],
)
async def test_gemini_uses_portable_v2_interactions_schema_without_thinking_override(model: str) -> None:
    backend = GeminiBackend("test-key")
    client = FakeAnalyzeClient()
    backend._client = client
    schema = {"type": "object", "properties": {"ok": {"type": "boolean"}}}

    result = await backend.analyze(
        AnalysisRequest(
            image_bytes=b"jpeg-data",
            mime_type="image/jpeg",
            width=640,
            height=480,
            prompt="Return JSON.",
            output_schema=schema,
            model=model,
            generation_params={"temperature": 0},
        )
    )

    assert client.aio.interactions.kwargs["response_format"] == {
        "type": "text",
        "mime_type": "application/json",
        "schema": schema,
    }
    assert backend.schema_profile == GEMINI_SCHEMA_PROFILE
    assert client.aio.interactions.kwargs["store"] is False
    assert client.aio.interactions.kwargs["generation_config"] == {"temperature": 0}
    assert client.aio.interactions.kwargs["timeout"] == backend.timeout_seconds
    assert result.raw_response == '{"ok":true}'
    assert result.usage["input_tokens"] == 120
    assert result.usage["output_tokens"] == 40
    assert result.usage["total_tokens"] == 160


@pytest.mark.asyncio
async def test_gemini_usage_accepts_mapping_response_metadata() -> None:
    backend = GeminiBackend("test-key")
    client = FakeAnalyzeClient()

    async def create(**_) -> SimpleNamespace:
        return SimpleNamespace(
            output_text='{"ok":true}',
            usage={"total_input_tokens": 7, "total_output_tokens": 3, "total_tokens": 10},
        )

    client.aio.interactions.create = create
    backend._client = client

    result = await backend.analyze(
        AnalysisRequest(
            image_bytes=b"jpeg-data",
            mime_type="image/jpeg",
            width=1,
            height=1,
            prompt="Return JSON.",
            output_schema={"type": "object"},
            model="gemini-test",
            generation_params={"temperature": 0},
        )
    )

    assert result.usage["input_tokens"] == 7
    assert result.usage["output_tokens"] == 3


@pytest.mark.asyncio
async def test_gemini_timeout_cancels_native_async_interaction() -> None:
    backend = GeminiBackend("test-key", timeout_seconds=0.01)
    cancelled = False

    async def create(**_) -> None:
        nonlocal cancelled
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            cancelled = True
            raise

    async_client = SimpleNamespace(interactions=SimpleNamespace(create=create))
    backend._client = SimpleNamespace(aio=async_client)

    with pytest.raises(TimeoutError):
        await backend.analyze(
            AnalysisRequest(
                image_bytes=b"jpeg-data",
                mime_type="image/jpeg",
                width=1,
                height=1,
                prompt="Return JSON.",
                output_schema={"type": "object"},
                model="gemini-test",
                generation_params={"temperature": 0},
            )
        )

    assert cancelled is True


@pytest.mark.asyncio
async def test_gemini_close_handles_async_and_awaitable_sync_clients() -> None:
    backend = GeminiBackend("test-key")
    async_closed = False
    sync_closed = False

    async def aclose() -> None:
        nonlocal async_closed
        async_closed = True

    async def finish_sync_close() -> None:
        nonlocal sync_closed
        sync_closed = True

    client = SimpleNamespace(
        aio=SimpleNamespace(aclose=aclose),
        close=lambda: finish_sync_close(),
    )
    backend._client = client
    backend._async_client = client.aio

    await backend.close()

    assert async_closed is True
    assert sync_closed is True
    assert backend._client is None
    assert backend._async_client is None
