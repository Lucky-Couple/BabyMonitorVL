from __future__ import annotations

import asyncio
import json
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .schemas import FrameAnalysis, HistoryItem, HistorySummary, ProviderName


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(slots=True)
class HistoryRecord:
    id: str
    session_id: str
    captured_at: datetime
    provider: ProviderName
    model: str
    source: str
    image_bytes: bytes
    image_width: int
    image_height: int
    prompt_version: str
    prompt: str
    output_schema: dict[str, Any]
    generation_params: dict[str, Any]
    completed_at: datetime | None = None
    status: str = "pending"
    analysis: FrameAnalysis | None = None
    raw_responses: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    latency_ms: float | None = None
    attempts: int = 0
    usage: dict[str, Any] = field(default_factory=dict)
    accounted_bytes: int = 0

    def calculate_bytes(self) -> int:
        metadata = {
            "prompt": self.prompt,
            "schema": self.output_schema,
            "generation_params": self.generation_params,
            "analysis": self.analysis.model_dump(mode="json") if self.analysis else None,
            "raw_responses": self.raw_responses,
            "errors": self.errors,
            "usage": self.usage,
        }
        return len(self.image_bytes) + len(json.dumps(metadata, ensure_ascii=False).encode("utf-8"))

    def token_usage(self, key: str) -> int | None:
        value = self.usage.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return max(0, int(value))

    def as_item(self) -> HistoryItem:
        return HistoryItem(
            id=self.id,
            session_id=self.session_id,
            captured_at=self.captured_at.isoformat(),
            completed_at=self.completed_at.isoformat() if self.completed_at else None,
            provider=self.provider,
            model=self.model,
            source=self.source,
            status=self.status,  # type: ignore[arg-type]
            analysis=self.analysis,
            raw_responses=self.raw_responses,
            errors=self.errors,
            latency_ms=self.latency_ms,
            attempts=self.attempts,
            input_tokens=self.token_usage("input_tokens"),
            output_tokens=self.token_usage("output_tokens"),
            prompt_version=self.prompt_version,
            prompt=self.prompt,
            output_schema=self.output_schema,
            generation_params={**self.generation_params, "usage": self.usage},
            image_width=self.image_width,
            image_height=self.image_height,
            image_url=f"/api/history/{self.id}/image",
        )

    def as_summary(self) -> HistorySummary:
        return HistorySummary(
            id=self.id,
            session_id=self.session_id,
            captured_at=self.captured_at.isoformat(),
            completed_at=self.completed_at.isoformat() if self.completed_at else None,
            provider=self.provider,
            model=self.model,
            status=self.status,  # type: ignore[arg-type]
            analysis=self.analysis,
            overall_risk=self.analysis.overall_risk if self.analysis else None,
            latency_ms=self.latency_ms,
            attempts=self.attempts,
            input_tokens=self.token_usage("input_tokens"),
            output_tokens=self.token_usage("output_tokens"),
            error=self.errors[-1] if self.errors else None,
            image_width=self.image_width,
            image_height=self.image_height,
            image_url=f"/api/history/{self.id}/image",
        )


class HistoryStore:
    def __init__(self, max_bytes: int) -> None:
        self.max_bytes = max_bytes
        self._records: deque[HistoryRecord] = deque()
        self._by_id: dict[str, HistoryRecord] = {}
        self._bytes = 0
        self._lock = asyncio.Lock()

    async def add(self, record: HistoryRecord) -> None:
        async with self._lock:
            record.accounted_bytes = record.calculate_bytes()
            self._records.append(record)
            self._by_id[record.id] = record
            self._bytes += record.accounted_bytes
            self._prune_locked()

    async def update(
        self,
        record_id: str,
        *,
        status: str,
        analysis: FrameAnalysis | None,
        raw_responses: list[str],
        errors: list[str],
        latency_ms: float | None,
        attempts: int,
        usage: dict[str, Any] | None = None,
    ) -> HistoryRecord | None:
        async with self._lock:
            record = self._by_id.get(record_id)
            if record is None:
                return None
            self._bytes -= record.accounted_bytes
            record.status = status
            record.analysis = analysis
            record.raw_responses = list(raw_responses)
            record.errors = list(errors)
            record.latency_ms = latency_ms
            record.attempts = attempts
            record.usage = usage or {}
            record.completed_at = utc_now()
            record.accounted_bytes = record.calculate_bytes()
            self._bytes += record.accounted_bytes
            self._prune_locked()
            return record

    async def get(self, record_id: str) -> HistoryRecord | None:
        async with self._lock:
            self._prune_locked()
            return self._by_id.get(record_id)

    async def list(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        provider: ProviderName | None = None,
        model: str | None = None,
        risk: str | None = None,
        errors_only: bool = False,
    ) -> tuple[list[HistorySummary], str | None]:
        async with self._lock:
            self._prune_locked()
            records = list(reversed(self._records))
            if cursor:
                try:
                    start = next(i for i, record in enumerate(records) if record.id == cursor) + 1
                    records = records[start:]
                except StopIteration:
                    records = []
            filtered: list[HistoryRecord] = []
            for record in records:
                if provider and record.provider != provider:
                    continue
                if model and record.model != model:
                    continue
                if errors_only and record.status != "error":
                    continue
                if risk and (not record.analysis or record.analysis.overall_risk.value != risk):
                    continue
                filtered.append(record)
                if len(filtered) >= limit + 1:
                    break
            has_more = len(filtered) > limit
            page = filtered[:limit]
            return [record.as_summary() for record in page], page[-1].id if has_more and page else None

    async def stats(self) -> dict[str, int]:
        async with self._lock:
            self._prune_locked()
            return {
                "items": len(self._records),
                "bytes": self._bytes,
                "max_bytes": self.max_bytes,
            }

    def _prune_locked(self) -> None:
        while self._records and self._bytes > self.max_bytes:
            record = self._records.popleft()
            self._by_id.pop(record.id, None)
            self._bytes -= record.accounted_bytes
