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
    output_schema: dict[str, Any]
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


@dataclass(slots=True)
class ProviderHealth:
    available: bool
    detail: str
    models: list[str] = field(default_factory=list)
    version: str | None = None


class VisionBackend(ABC):
    name: ProviderName

    @abstractmethod
    async def healthcheck(self) -> ProviderHealth: ...

    @abstractmethod
    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult: ...

    async def close(self) -> None:
        return None
