import asyncio
from datetime import timezone

from babymonitorvl.monitor import CapturedFrame, offer_latest, redact_url, utc_now, version_at_least
from babymonitorvl.providers.base import aggregate_usage


def frame(sequence: int) -> CapturedFrame:
    return CapturedFrame(b"jpeg", utc_now().astimezone(timezone.utc), 10, 10, sequence)


def test_latest_frame_queue_replaces_stale_frame() -> None:
    queue: asyncio.Queue[CapturedFrame] = asyncio.Queue(maxsize=1)
    assert offer_latest(queue, frame(1)) is False
    assert offer_latest(queue, frame(2)) is True
    assert queue.get_nowait().sequence == 2


def test_rtsp_credentials_and_query_values_are_redacted() -> None:
    redacted = redact_url("rtsp://alice:secret@camera.local:8554/live?token=abc&profile=main")
    assert "alice" not in redacted
    assert "secret" not in redacted
    assert "abc" not in redacted
    assert redacted == "rtsp://***:***@camera.local:8554/live?token=%2A%2A%2A&profile=%2A%2A%2A"


def test_ollama_version_comparison() -> None:
    assert version_at_least("0.12.7", (0, 12, 7))
    assert version_at_least("v0.13.1", (0, 12, 7))
    assert not version_at_least("0.12.6", (0, 12, 7))
    assert not version_at_least(None, (0, 12, 7))


def test_usage_aggregation_counts_all_attempts() -> None:
    usage = aggregate_usage(
        [
            {"input_tokens": 100, "output_tokens": 20},
            {"input_tokens": 110, "output_tokens": 30},
        ]
    )
    assert usage["input_tokens"] == 210
    assert usage["output_tokens"] == 50
    assert usage["total_tokens"] == 260
    assert len(usage["attempts"]) == 2
