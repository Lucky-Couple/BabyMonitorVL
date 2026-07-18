from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from ..schemas import ProviderName


@dataclass(slots=True)
class AnalysisRequest:
    image_bytes: bytes
    mime_type: str
    width: int
    height: int
    prompt: str
    output_schema: dict[str, Any]  # Provider transport schema, prepared before audit/history storage.
    model: str
    generation_params: dict[str, Any]


@dataclass(slots=True)
class ProviderCallResult:
    raw_response: str
    usage: dict[str, Any] = field(default_factory=dict)


def token_count(usage: dict[str, Any], key: str) -> int:
    value = usage.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def aggregate_usage(attempts: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate standardized token counts while retaining provider details."""

    input_tokens = sum(token_count(item, "input_tokens") for item in attempts)
    output_tokens = sum(token_count(item, "output_tokens") for item in attempts)
    return {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "attempts": [dict(item) for item in attempts],
    }


def provider_error_status_code(exc: Exception) -> int | None:
    """Extract an HTTP status code without depending on a provider SDK type."""

    for candidate in (
        getattr(exc, "status_code", None),
        getattr(exc, "code", None),
        getattr(getattr(exc, "response", None), "status_code", None),
    ):
        if isinstance(candidate, int) and not isinstance(candidate, bool):
            return candidate
    return None


def should_retry_provider_error(exc: Exception) -> bool:
    """Retry transient/unknown failures, but never replay deterministic HTTP 4xx."""

    status_code = provider_error_status_code(exc)
    if status_code is None:
        return True
    return status_code in {408, 409, 425, 429} or status_code >= 500


@dataclass(slots=True)
class ProviderHealth:
    available: bool
    detail: str
    models: list[str] = field(default_factory=list)
    version: str | None = None


class VisionBackend(ABC):
    name: ProviderName
    schema_profile = "unmodified"

    def prepare_output_schema(self, schema: dict[str, Any]) -> dict[str, Any]:
        """Adapt the validation schema to the provider's transport contract."""

        return schema

    def sensitive_values(self) -> tuple[str, ...]:
        """Return exact provider secrets that must be removed from public errors."""

        return ()

    @abstractmethod
    async def healthcheck(self) -> ProviderHealth: ...

    @abstractmethod
    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult: ...

    async def close(self) -> None:
        return None
