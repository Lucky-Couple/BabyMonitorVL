import json

import httpx
import pytest

from babymonitorvl.providers import AnalysisRequest
from babymonitorvl.providers.ollama import OllamaBackend


@pytest.mark.asyncio
async def test_ollama_maps_shared_contract_to_native_api() -> None:
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        return httpx.Response(
            200,
            json={"message": {"content": "{}"}, "prompt_eval_count": 25, "eval_count": 10},
        )

    backend = OllamaBackend("http://ollama.test")
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.test")
    result = await backend.analyze(
        AnalysisRequest(
            image_bytes=b"image",
            mime_type="image/jpeg",
            width=10,
            height=10,
            prompt="shared prompt",
            output_schema={"type": "object"},
            model="qwen3-vl:4b",
            generation_params={"temperature": 0},
        )
    )
    assert result.raw_response == "{}"
    assert result.usage["input_tokens"] == 25
    assert result.usage["output_tokens"] == 10
    assert result.usage["total_tokens"] == 35
    assert captured["format"] == {"type": "object"}
    assert captured["messages"][0]["content"] == "shared prompt"
    assert captured["messages"][0]["images"]
    assert captured["think"] is False
    assert captured["stream"] is False
    await backend.close()


@pytest.mark.asyncio
async def test_ollama_falls_back_to_thinking_when_content_is_empty() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={"message": {"content": "", "thinking": '{"schema_version":"1.0"}'}},
        )

    backend = OllamaBackend("http://ollama.test")
    await backend.client.aclose()
    backend.client = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="http://ollama.test")
    result = await backend.analyze(
        AnalysisRequest(
            image_bytes=b"image",
            mime_type="image/jpeg",
            width=10,
            height=10,
            prompt="shared prompt",
            output_schema={"type": "object"},
            model="qwen3-vl:8b",
            generation_params={"temperature": 0},
        )
    )
    assert result.raw_response == '{"schema_version":"1.0"}'
    assert result.usage["response_field"] == "thinking"
    await backend.close()
