from __future__ import annotations

import asyncio
import base64
import inspect
from copy import deepcopy
from typing import Any

from ..schemas import ProviderName
from .base import AnalysisRequest, ProviderCallResult, ProviderHealth, VisionBackend


GEMINI_SCHEMA_PROFILE = "google-ai-structured-output-compact-v1"

# Google AI structured output supports a documented subset of JSON Schema.
# Keep this allowlist synchronized with docs/GEMINI_PROVIDER.md and its tests.
GEMINI_SCHEMA_KEYWORDS = frozenset(
    {
        "additionalProperties",
        "anyOf",
        "enum",
        "items",
        "properties",
        "required",
        "type",
    }
)


def gemini_compatible_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a Google AI structured-output subset without weakening local validation.

    Pydantic emits useful validation keywords that Google AI does not accept in
    `response_format.schema`. The complete Pydantic schema remains in the prompt
    and the response is still validated with the original Pydantic model.
    """

    definitions = schema.get("$defs")
    definitions = definitions if isinstance(definitions, dict) else {}

    def convert(node: dict[str, Any], depth: int, ref_stack: frozenset[str]) -> dict[str, Any]:
        ref = node.get("$ref")
        if isinstance(ref, str):
            prefix = "#/$defs/"
            if not ref.startswith(prefix) or ref in ref_stack:
                raise ValueError(f"unsupported or recursive Gemini schema reference: {ref}")
            target = definitions.get(ref.removeprefix(prefix))
            if not isinstance(target, dict):
                raise ValueError(f"unresolved Gemini schema reference: {ref}")
            return convert(target, depth, ref_stack | {ref})

        result: dict[str, Any] = {}

        const_value = node.get("const")
        if "const" in node and "enum" not in node:
            result["enum"] = [deepcopy(const_value)]

        for key, value in node.items():
            if key not in GEMINI_SCHEMA_KEYWORDS:
                continue
            if key == "properties":
                if isinstance(value, dict):
                    result[key] = {
                        str(name): convert(child, depth + 1, ref_stack)
                        for name, child in value.items()
                        if isinstance(child, dict)
                    }
            elif key == "anyOf":
                if isinstance(value, list):
                    result[key] = [
                        convert(child, depth + 1, ref_stack) for child in value if isinstance(child, dict)
                    ]
            elif key == "items":
                if isinstance(value, dict):
                    result[key] = convert(value, depth + 1, ref_stack)
            elif key == "additionalProperties":
                if depth == 0 and isinstance(value, bool):
                    result[key] = value
            else:
                result[key] = deepcopy(value)
        return result

    return convert(schema, 0, frozenset())


def normalize_gemini_usage(raw_usage: dict[str, Any]) -> dict[str, Any]:
    usage = dict(raw_usage)

    def first_count(*keys: str) -> int:
        for key in keys:
            value = usage.get(key)
            if not isinstance(value, bool) and isinstance(value, (int, float)):
                return max(0, int(value))
        return 0

    input_tokens = first_count(
        "input_tokens",
        "total_input_tokens",
        "input_token_count",
        "prompt_token_count",
    )
    direct_output_tokens = first_count("output_tokens", "output_token_count")
    if direct_output_tokens:
        output_tokens = direct_output_tokens
    else:
        output_tokens = first_count(
            "total_output_tokens",
            "response_token_count",
            "candidates_token_count",
            "candidate_token_count",
        )
        output_tokens += first_count(
            "total_thought_tokens",
            "thoughts_token_count",
            "thinking_token_count",
        )
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
    schema_profile = GEMINI_SCHEMA_PROFILE

    def prepare_output_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        return gemini_compatible_schema(schema)

    def __init__(
        self,
        api_key: str | None,
        timeout_seconds: float = 60.0,
        key_source: str | None = None,
    ) -> None:
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds
        self.key_source = key_source or ("environment" if api_key else "none")
        self._client: Any = None
        self._async_client: Any = None

    def sensitive_values(self) -> tuple[str, ...]:
        return (self.api_key,) if self.api_key else ()

    def _get_client(self) -> Any:
        if not self.api_key:
            raise RuntimeError("GEMINI_API_KEY is not configured")
        if self._client is None:
            from google import genai

            self._client = genai.Client(api_key=self.api_key)
        return self._client

    @staticmethod
    def _compatible_model_names(client: Any) -> list[str]:
        """Return hosted Google models compatible with this image-to-JSON workflow."""
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
            supported_family = name.startswith("gemini-") or name.startswith("gemma-4-")
            if (
                supported_family
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
        async_client = client.aio
        self._async_client = async_client
        image_data = base64.b64encode(request.image_bytes).decode("ascii")
        response = await asyncio.wait_for(
            async_client.interactions.create(
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
                },
                store=False,
                timeout=self.timeout_seconds,
            ),
            timeout=self.timeout_seconds,
        )
        raw = getattr(response, "output_text", None)
        if not isinstance(raw, str):
            raise ValueError("Gemini response did not contain output_text")
        usage_meta = getattr(response, "usage", None) or getattr(response, "usage_metadata", None)
        if hasattr(usage_meta, "model_dump"):
            raw_usage = usage_meta.model_dump(mode="json")
        elif isinstance(usage_meta, dict):
            raw_usage = usage_meta
        else:
            raw_usage = {}
        usage = normalize_gemini_usage(raw_usage)
        return ProviderCallResult(raw_response=raw, usage=usage)

    async def close(self) -> None:
        client = self._client
        async_client = self._async_client
        self._client = None
        self._async_client = None
        try:
            if async_client is not None:
                aclose = getattr(async_client, "aclose", None)
                if callable(aclose):
                    result = aclose()
                    if inspect.isawaitable(result):
                        await result
        finally:
            if client is not None:
                close = getattr(client, "close", None)
                if callable(close):
                    result = await asyncio.to_thread(close)
                    if inspect.isawaitable(result):
                        await result
