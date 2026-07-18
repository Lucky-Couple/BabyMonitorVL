from __future__ import annotations

import asyncio
import base64
from typing import Any

from ..schemas import ProviderName
from .base import AnalysisRequest, ProviderCallResult, ProviderHealth, VisionBackend


def normalize_gemini_usage(raw_usage: dict[str, Any]) -> dict[str, Any]:
    usage = dict(raw_usage)

    def first_count(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                return max(0, int(value))
        return 0

    input_tokens = first_count("input_tokens", "input_token_count", "prompt_token_count")
    direct_output_tokens = first_count("output_tokens", "output_token_count")
    if direct_output_tokens:
        output_tokens = direct_output_tokens
    else:
        output_tokens = first_count("candidates_token_count", "candidate_token_count")
        output_tokens += first_count("thoughts_token_count", "thinking_token_count")
    provider_total = first_count("total_tokens", "total_token_count")
    if provider_total > input_tokens + output_tokens:
        output_tokens = provider_total - input_tokens
    usage.update(
        {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
        }
    )
    return usage


class GeminiBackend(VisionBackend):
    name = ProviderName.GEMINI

    def __init__(self, api_key: str | None, timeout_seconds: float = 60.0) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self._client: Any = None

    def _get_client(self) -> Any:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    @staticmethod
    def _compatible_model_names(client: Any) -> list[str]:
        """Return Gemini models compatible with this image-to-JSON workflow."""
        excluded_variants = (
            "embedding",
            "imagen",
            "veo-",
            "-live",
            "live-",
            "-tts",
            "native-audio",
            "-image",
        )
        result: set[str] = set()
        for model in client.models.list(config={"page_size": 100, "query_base": True}):
            resource_name = getattr(model, "name", None)
            if not isinstance(resource_name, str):
                continue
            name = resource_name.removeprefix("models/")
            actions = getattr(model, "supported_actions", None) or []
            normalized_actions = {str(action).replace("_", "").lower() for action in actions}
            if (
                name.startswith("gemini-")
                and "generatecontent" in normalized_actions
                and not any(marker in name.lower() for marker in excluded_variants)
            ):
                result.add(name)
        return sorted(result)

    async def healthcheck(self) -> ProviderHealth:
        if not self.api_key:
            return ProviderHealth(False, "GEMINI_API_KEY is not configured")
        try:
            client = self._get_client()
            models = await asyncio.wait_for(
                asyncio.to_thread(self._compatible_model_names, client),
                timeout=min(self.timeout_seconds, 15.0),
            )
            if not models:
                return ProviderHealth(False, "Gemini returned no compatible image analysis models")
            return ProviderHealth(True, f"Gemini API reachable · {len(models)} compatible models", models)
        except Exception as exc:
            return ProviderHealth(False, f"Gemini unavailable: {type(exc).__name__}")

    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        client = self._get_client()
        image_data = base64.b64encode(request.image_bytes).decode("ascii")

        def invoke() -> Any:
            return client.interactions.create(
                model=request.model,
                input=[
                    {"type": "text", "text": request.prompt},
                    {"type": "image", "data": image_data, "mime_type": request.mime_type},
                ],
                response_format={
                    "type": "text",
                    "mime_type": "application/json",
                    "schema": request.output_schema,
                },
                generation_config={
                    "temperature": request.generation_params.get("temperature", 0),
                    "thinking_level": "minimal",
                },
            )

        response = await asyncio.wait_for(asyncio.to_thread(invoke), timeout=self.timeout_seconds)
        raw = getattr(response, "output_text", None)
        if not isinstance(raw, str):
            raise ValueError("Gemini response did not contain output_text")
        usage_meta = getattr(response, "usage_metadata", None)
        raw_usage = usage_meta.model_dump(mode="json") if hasattr(usage_meta, "model_dump") else {}
        usage = normalize_gemini_usage(raw_usage)
        return ProviderCallResult(raw_response=raw, usage=usage)
