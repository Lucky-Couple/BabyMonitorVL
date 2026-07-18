from datetime import timedelta

import pytest

from babymonitorvl.history import HistoryRecord, HistoryStore, utc_now
from babymonitorvl.schemas import ProviderName


def make_record(record_id: str, image_size: int = 20, age_seconds: int = 0) -> HistoryRecord:
    return HistoryRecord(
        id=record_id,
        session_id="session",
        captured_at=utc_now() - timedelta(seconds=age_seconds),
        provider=ProviderName.OLLAMA,
        model="qwen3-vl:4b",
        source="rtsp://***:***@camera/stream",
        image_bytes=b"x" * image_size,
        image_width=640,
        image_height=480,
        prompt_version="v1",
        prompt="prompt",
        output_schema={"type": "object"},
        generation_params={"temperature": 0},
    )


@pytest.mark.asyncio
async def test_history_has_no_item_count_limit() -> None:
    store = HistoryStore(max_bytes=1_000_000)
    for index in range(205):
        await store.add(make_record(str(index)))
    assert await store.get("0") is not None
    assert (await store.stats())["items"] == 205


@pytest.mark.asyncio
async def test_history_prunes_oldest_by_byte_budget() -> None:
    sample = make_record("sample", image_size=100)
    per_record = sample.calculate_bytes()
    store = HistoryStore(max_bytes=per_record + 10)
    await store.add(make_record("first", image_size=100))
    await store.add(make_record("second", image_size=100))
    assert await store.get("first") is None
    assert await store.get("second") is not None


@pytest.mark.asyncio
async def test_history_summary_exposes_token_usage() -> None:
    store = HistoryStore(max_bytes=1_000_000)
    await store.add(make_record("tokens"))
    await store.update(
        "tokens",
        status="error",
        analysis=None,
        raw_responses=["invalid"],
        errors=["parse failed"],
        latency_ms=10,
        attempts=2,
        usage={"input_tokens": 240, "output_tokens": 80, "total_tokens": 320},
    )
    items, _ = await store.list()
    assert items[0].input_tokens == 240
    assert items[0].output_tokens == 80
