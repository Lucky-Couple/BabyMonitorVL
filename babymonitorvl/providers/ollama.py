from __future__ import annotations

import base64
from typing import Any

import httpx

from ..schemas import ProviderName
from .base import AnalysisRequest, ProviderCallResult, ProviderHealth, VisionBackend


class OllamaBackend(VisionBackend):
    name = ProviderName.OLLAMA

    def __init__(self, base_url: str, timeout_seconds: float = 60.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout_seconds)

    async def healthcheck(self) -> ProviderHealth:
        try:
            version_response = await self.client.get("/api/version")
            version_response.raise_for_status()
            tags_response = await self.client.get("/api/tags")
            tags_response.raise_for_status()
            version = version_response.json().get("version")
            models = [item.get("name", "") for item in tags_response.json().get("models", [])]
            return ProviderHealth(True, "Ollama is reachable", sorted(filter(None, models)), version)
        except Exception as exc:
            return ProviderHealth(False, f"Ollama unavailable: {type(exc).__name__}")

    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        payload: dict[str, Any] = {
            "model": request.model,
            "messages": [
                {
                    "role": "user",
                    "content": request.prompt,
                    "images": [base64.b64encode(request.image_bytes).decode("ascii")],
                }
            ],
            "format": request.output_schema,
            "stream": False,
            "think": False,
            "options": {"temperature": request.generation_params.get("temperature", 0)},
        }
        response = await self.client.post("/api/chat", json=payload)
        response.raise_for_status()
        body = response.json()
        message = body.get("message", {})
        content = message.get("content")
        thinking = message.get("thinking")
        raw = content.strip() if isinstance(content, str) else ""
        response_field = "content"
        # Some Ollama thinking-capable vision models currently place the final
        # structured JSON in `thinking` even when `think=false`.
        if not raw and isinstance(thinking, str) and thinking.strip():
            raw = thinking.strip()
            response_field = "thinking"
        if not raw:
            raise ValueError("Ollama response did not contain structured output")
        usage = {
            key: body.get(key)
            for key in (
                "total_duration",
                "load_duration",
                "prompt_eval_count",
                "prompt_eval_duration",
                "eval_count",
                "eval_duration",
            )
            if body.get(key) is not None
        }
        input_tokens = body.get("prompt_eval_count")
        output_tokens = body.get("eval_count")
        usage["input_tokens"] = input_tokens if isinstance(input_tokens, int) else 0
        usage["output_tokens"] = output_tokens if isinstance(output_tokens, int) else 0
        usage["total_tokens"] = usage["input_tokens"] + usage["output_tokens"]
        usage["response_field"] = response_field
        return ProviderCallResult(raw_response=raw, usage=usage)

    async def close(self) -> None:
        await self.client.aclose()
