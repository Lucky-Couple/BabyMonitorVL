import asyncio
import json
from datetime import timezone

import pytest
from pydantic import ValidationError

import babymonitorvl.monitor as monitor_module
from babymonitorvl.config import Settings
from babymonitorvl.coordinates import ModelOutputError
from babymonitorvl.events import EventHub
from babymonitorvl.ffmpeg import FrameReadTimeout
from babymonitorvl.history import HistoryStore
from babymonitorvl.monitor import (
    CaptureTelemetry,
    CapturedFrame,
    MonitorService,
    local_validation_correction,
    redact_sensitive_text,
    redact_url,
    utc_now,
    version_at_least,
)
from babymonitorvl.providers.base import (
    AnalysisRequest,
    ProviderCallResult,
    ProviderHealth,
    VisionBackend,
    aggregate_usage,
    should_retry_provider_error,
)
from babymonitorvl.schemas import MonitorStartRequest, MonitorStatus, ProviderName


def frame(sequence: int) -> CapturedFrame:
    return CapturedFrame(b"jpeg", utc_now().astimezone(timezone.utc), 10, 10, sequence)


def install_capture(service: MonitorService, captured_frame: CapturedFrame) -> None:
    async def capture(_session_id: str) -> CapturedFrame:
        return captured_frame

    service._request_capture = capture  # type: ignore[method-assign]


def test_capture_telemetry_measures_recent_demand_driven_captures() -> None:
    telemetry = CaptureTelemetry(max_samples=3)

    assert telemetry.observe(10.0, 1000) == (None, None)
    measured_interval_seconds, analysis_kbps = telemetry.observe(11.0, 2000)
    assert measured_interval_seconds == pytest.approx(1.0)
    assert analysis_kbps == pytest.approx(16.0)

    measured_interval_seconds, analysis_kbps = telemetry.observe(13.5, 3000)
    assert measured_interval_seconds == pytest.approx(3.5 / 2)
    assert analysis_kbps == pytest.approx(40 / 3.5)

    measured_interval_seconds, analysis_kbps = telemetry.observe(14.5, 4000)
    assert measured_interval_seconds == pytest.approx(3.5 / 2)
    assert analysis_kbps == pytest.approx(16.0)


def test_capture_telemetry_rejects_invalid_configuration_and_sizes() -> None:
    with pytest.raises(ValueError, match="at least two samples"):
        CaptureTelemetry(max_samples=1)
    with pytest.raises(ValueError, match="byte count"):
        CaptureTelemetry().observe(1.0, -1)


def test_monitor_status_rejects_unknown_fields_and_invalid_assignments() -> None:
    status = MonitorStatus()
    with pytest.raises(ValidationError, match="no_such_attribute"):
        setattr(status, "reconect_delay_seconds", 1)
    with pytest.raises(ValidationError, match="greater than or equal to 0"):
        status.submitted_count = -1


def test_monitor_start_contract_does_not_expose_capture_resizing() -> None:
    properties = MonitorStartRequest.model_json_schema()["properties"]
    assert "max_image_edge" not in properties


def test_monitor_start_uses_minimum_frame_interval_seconds_contract() -> None:
    request = MonitorStartRequest(rtsp_url="rtsp://camera.invalid/stream")
    assert request.min_frame_interval_seconds == 1.0
    with pytest.raises(ValidationError, match="min_frame_interval_seconds"):
        MonitorStartRequest(
            rtsp_url="rtsp://camera.invalid/stream",
            min_frame_interval_seconds=0,
        )
    with pytest.raises(ValidationError, match="fps"):
        MonitorStartRequest(rtsp_url="rtsp://camera.invalid/stream", fps=1)


class StalledFFmpegProcess:
    def __init__(self) -> None:
        self.stdout = asyncio.StreamReader()
        self.stderr = asyncio.StreamReader()
        self.stderr.feed_eof()
        self.returncode: int | None = None
        self.terminated = False

    async def wait(self) -> int:
        while self.returncode is None:
            await asyncio.sleep(0)
        return self.returncode

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = 0
        self.stdout.feed_eof()

    def kill(self) -> None:
        self.returncode = -9
        self.stdout.feed_eof()


def fake_jpeg(width: int = 32, height: int = 16) -> bytes:
    return (
        b"\xff\xd8\xff\xc0\x00\x0b\x08"
        + height.to_bytes(2, "big")
        + width.to_bytes(2, "big")
        + b"\x01\x01\x11\x00\xff\xd9"
    )


class OneFrameFFmpegProcess(StalledFFmpegProcess):
    def __init__(self, image_bytes: bytes) -> None:
        super().__init__()
        self.stdout.feed_data(image_bytes)
        self.stdout.feed_eof()


@pytest.mark.asyncio
async def test_capture_process_starts_only_on_demand_and_returns_one_fresh_frame(monkeypatch, tmp_path) -> None:
    process_calls: list[tuple[str, ...]] = []

    async def create_process(*args, **kwargs):
        process_calls.append(args)
        return OneFrameFFmpegProcess(fake_jpeg(1920, 1080))

    monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", create_process)
    service = MonitorService(
        Settings(frontend_dist=tmp_path, ffmpeg_binary="true"),
        HistoryStore(1024 * 1024),
        EventHub(),
        {},
    )
    session_id = "on-demand-capture-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        provider=ProviderName.OLLAMA,
    )
    capture_loop = asyncio.create_task(service._capture_loop(session_id))
    await asyncio.sleep(0)
    assert process_calls == []

    captured = await asyncio.wait_for(service._request_capture(session_id), timeout=1)
    assert (captured.width, captured.height) == (1920, 1080)
    assert len(process_calls) == 1
    command = process_calls[0]
    assert "-vf" not in command
    assert command[command.index("-frames:v") + 1] == "1"

    service._session_id = None
    capture_loop.cancel()
    await asyncio.gather(capture_loop, return_exceptions=True)


@pytest.mark.asyncio
async def test_frame_stall_terminates_ffmpeg_and_enters_reconnect(monkeypatch, tmp_path) -> None:
    process = StalledFFmpegProcess()

    async def create_process(*args, **kwargs):
        return process

    async def stalled_frames(reader, frame_timeout_seconds):
        raise FrameReadTimeout("FFmpeg produced no complete JPEG frame for 30 seconds")
        yield b""  # pragma: no cover

    monkeypatch.setattr(monitor_module.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(monitor_module, "read_mjpeg_frames", stalled_frames)
    service = MonitorService(
        Settings(frontend_dist=tmp_path, ffmpeg_binary="true"),
        HistoryStore(1024 * 1024),
        EventHub(),
        {},
    )
    session_id = "stalled-capture-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        provider=ProviderName.OLLAMA,
    )
    task = asyncio.create_task(service._capture_loop(session_id))
    request = asyncio.get_running_loop().create_future()
    await service._capture_requests.put(request)

    async def wait_for_reconnect() -> None:
        while service._status.reconnect_attempt < 1:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_reconnect(), timeout=0.2)
    assert process.terminated is True
    assert service._status.state == "reconnecting"
    assert service._status.reconnect_delay_seconds == 1
    assert "no complete JPEG frame" in (service._status.last_error or "")

    service._session_id = None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


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


def test_deterministic_provider_client_errors_are_not_retried() -> None:
    bad_request = RuntimeError("unsupported model parameter")
    bad_request.status_code = 400  # type: ignore[attr-defined]
    assert should_retry_provider_error(bad_request) is False


def test_transient_and_unclassified_provider_errors_are_retried() -> None:
    rate_limited = RuntimeError("rate limited")
    rate_limited.code = 429  # type: ignore[attr-defined]
    unavailable = RuntimeError("unavailable")
    unavailable.response = type("Response", (), {"status_code": 503})()  # type: ignore[attr-defined]
    assert should_retry_provider_error(rate_limited) is True
    assert should_retry_provider_error(unavailable) is True
    assert should_retry_provider_error(ValueError("invalid JSON")) is True


def test_provider_secrets_are_removed_from_public_error_text() -> None:
    secret = "runtime-provider-secret"
    assert redact_sensitive_text(f"request failed for key {secret}", (secret,)) == "request failed for key ***"


def test_local_json_and_schema_failures_get_correction_codes() -> None:
    json_error = json.JSONDecodeError("invalid", "x", 0)
    assert local_validation_correction(json_error) == "root:invalid_json_envelope"
    assert local_validation_correction(ModelOutputError("not an object")) == "root:not_json_object"
    assert local_validation_correction(RuntimeError("provider failed")) is None


class JsonRetryBackend(VisionBackend):
    name = ProviderName.OLLAMA

    def __init__(self) -> None:
        self.prompts: list[str] = []

    async def healthcheck(self) -> ProviderHealth:
        return ProviderHealth(True, "ready", ["test-model"], "1.0.0")

    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        self.prompts.append(request.prompt)
        if len(self.prompts) == 1:
            return ProviderCallResult("not valid JSON")
        return valid_empty_analysis_result()


def valid_empty_analysis_result() -> ProviderCallResult:
    return ProviderCallResult(
        json.dumps(
            {
                "schema_version": "1.3",
                "summary": "No infant is visible.",
                "image_quality": "good",
                "infants": [],
                "adult_presence": "not_detected",
                "adults": [],
                "cats": [],
                "overall_risk": "unknown",
                "risk_reasons": [],
            }
        )
    )


class BlockingBackend(JsonRetryBackend):
    def __init__(self) -> None:
        super().__init__()
        self.first_started = asyncio.Event()
        self.release_first = asyncio.Event()

    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        self.prompts.append(request.prompt)
        if len(self.prompts) == 1:
            self.first_started.set()
            await self.release_first.wait()
        return valid_empty_analysis_result()


@pytest.mark.asyncio
async def test_analysis_requests_fresh_frame_only_after_result_and_respects_minimum_interval(tmp_path) -> None:
    backend = BlockingBackend()
    service = MonitorService(
        Settings(frontend_dist=tmp_path),
        HistoryStore(1024 * 1024),
        EventHub(),
        {ProviderName.OLLAMA: backend},
    )
    session_id = "demand-driven-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        min_frame_interval_seconds=0.1,
        provider=ProviderName.OLLAMA,
        model="test-model",
    )
    service._model = "test-model"
    capture_times: list[float] = []

    async def capture(_session_id: str) -> CapturedFrame:
        capture_times.append(asyncio.get_running_loop().time())
        return frame(len(capture_times))

    service._request_capture = capture  # type: ignore[method-assign]
    task = asyncio.create_task(service._analysis_loop(session_id))
    await asyncio.wait_for(backend.first_started.wait(), timeout=1)
    await asyncio.sleep(0.02)
    assert len(capture_times) == 1

    backend.release_first.set()

    async def wait_for_second_capture() -> None:
        while len(capture_times) < 2:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_second_capture(), timeout=1)
    assert capture_times[1] - capture_times[0] >= 0.08

    service._session_id = None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)


class ProviderJsonErrorRetryBackend(JsonRetryBackend):
    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        self.prompts.append(request.prompt)
        if len(self.prompts) == 1:
            raise json.JSONDecodeError("provider response was not JSON", "x", 0)
        return valid_empty_analysis_result()


class DuplicateBoxBackend(JsonRetryBackend):
    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        self.prompts.append(request.prompt)
        infant = {
            "infant_box": [100, 200, 500, 700],
            "mouth_nose_box": None,
            "posture": "supine",
            "mouth_nose_occlusion": "not_visible",
            "blanket_coverage": "absent",
            "related_objects": [],
            "risk_level": "normal",
            "confidence": 0.9,
            "evidence": ["Infant is visible."],
        }
        return ProviderCallResult(
            json.dumps(
                {
                    "schema_version": "1.3",
                    "summary": "One infant is visible.",
                    "image_quality": "good",
                    "infants": [infant, infant],
                    "adult_presence": "not_detected",
                    "adults": [],
                    "cats": [],
                    "overall_risk": "normal",
                    "risk_reasons": [],
                }
            ),
            usage={"input_tokens": 100, "output_tokens": 50},
        )


class EmptySceneNormalRiskBackend(JsonRetryBackend):
    async def analyze(self, request: AnalysisRequest) -> ProviderCallResult:
        self.prompts.append(request.prompt)
        return ProviderCallResult(
            json.dumps(
                {
                    "schema_version": "1.3",
                    "summary": "No infant is visible.",
                    "image_quality": "good",
                    "infants": [],
                    "adult_presence": "not_detected",
                    "adults": [],
                    "cats": [],
                    "overall_risk": "normal",
                    "risk_reasons": ["No infant detected in frame"],
                }
            ),
            usage={"input_tokens": 100, "output_tokens": 25},
        )


@pytest.mark.asyncio
async def test_analysis_retry_adds_json_validation_correction(tmp_path) -> None:
    backend = JsonRetryBackend()
    history = HistoryStore(1024 * 1024)
    service = MonitorService(
        Settings(frontend_dist=tmp_path),
        history,
        EventHub(),
        {ProviderName.OLLAMA: backend},
    )
    session_id = "json-retry-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        provider=ProviderName.OLLAMA,
        model="test-model",
    )
    service._model = "test-model"
    install_capture(service, frame(1))
    task = asyncio.create_task(service._analysis_loop(session_id))

    async def wait_for_completion() -> None:
        while service._status.completed_count < 1:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_completion(), timeout=1)
    service._session_id = None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(backend.prompts) == 2
    assert "VALIDATION CORRECTION" not in backend.prompts[0]
    assert "root:invalid_json_envelope" in backend.prompts[1]
    assert "Return exactly one valid JSON object" in backend.prompts[1]
    assert "Always return adult_presence and adults" in backend.prompts[1]
    assert "whose box overlaps mouth_nose_box" in backend.prompts[1]
    records, _ = await history.list(limit=10)
    assert records[0].status == "success"
    assert records[0].attempts == 2
    detail = await history.get(records[0].id)
    assert detail is not None
    assert [item.outcome for item in detail.attempt_details] == ["validation_error", "success"]
    assert detail.prompt == backend.prompts[0]
    assert [item.prompt for item in detail.attempt_details] == backend.prompts
    assert "VALIDATION CORRECTION" not in detail.attempt_details[0].prompt
    assert "VALIDATION CORRECTION" in detail.attempt_details[1].prompt
    assert detail.attempt_details[0].response_index == 0
    assert detail.attempt_details[0].will_retry is True
    assert detail.attempt_details[0].retry_reason == "local_validation:root:invalid_json_envelope"
    assert detail.attempt_details[1].response_index == 1
    assert detail.attempt_details[1].will_retry is False


@pytest.mark.asyncio
async def test_provider_json_error_retries_without_model_output_correction(tmp_path) -> None:
    backend = ProviderJsonErrorRetryBackend()
    history = HistoryStore(1024 * 1024)
    service = MonitorService(
        Settings(frontend_dist=tmp_path),
        history,
        EventHub(),
        {ProviderName.OLLAMA: backend},
    )
    session_id = "provider-json-error-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        provider=ProviderName.OLLAMA,
        model="test-model",
    )
    service._model = "test-model"
    install_capture(service, frame(1))
    task = asyncio.create_task(service._analysis_loop(session_id))

    async def wait_for_completion() -> None:
        while service._status.completed_count < 1:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_completion(), timeout=1)
    service._session_id = None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    assert len(backend.prompts) == 2
    assert "VALIDATION CORRECTION" not in backend.prompts[0]
    assert "VALIDATION CORRECTION" not in backend.prompts[1]
    records, _ = await history.list(limit=10)
    detail = await history.get(records[0].id)
    assert detail is not None
    assert [item.outcome for item in detail.attempt_details] == ["provider_error", "success"]
    assert detail.prompt == backend.prompts[0]
    assert [item.prompt for item in detail.attempt_details] == backend.prompts
    assert all("VALIDATION CORRECTION" not in item.prompt for item in detail.attempt_details)
    assert detail.attempt_details[0].response_index is None
    assert detail.attempt_details[0].retry_reason == "retryable_provider_error"
    assert detail.attempt_details[1].response_index == 0


@pytest.mark.asyncio
async def test_empty_scene_normal_risk_is_audited_and_does_not_retry(tmp_path, caplog) -> None:
    caplog.set_level("WARNING", logger="babymonitorvl.monitor")
    backend = EmptySceneNormalRiskBackend()
    history = HistoryStore(1024 * 1024)
    service = MonitorService(
        Settings(frontend_dist=tmp_path),
        history,
        EventHub(),
        {ProviderName.OLLAMA: backend},
    )
    session_id = "empty-scene-repair-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        provider=ProviderName.OLLAMA,
        model="test-model",
    )
    service._model = "test-model"
    install_capture(service, frame(1))
    task = asyncio.create_task(service._analysis_loop(session_id))

    async def wait_for_completion() -> None:
        while service._status.completed_count < 1:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_completion(), timeout=1)
    service._session_id = None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    records, _ = await history.list(limit=10)
    detail = await history.get(records[0].id)
    assert detail is not None
    assert detail.analysis is not None
    assert detail.analysis.overall_risk.value == "unknown"
    assert detail.attempts == 1
    assert len(backend.prompts) == 1
    assert detail.errors == []
    assert '"overall_risk": "normal"' in detail.raw_responses[0]
    assert detail.warnings == detail.attempt_details[0].warnings
    assert "contract_value_repaired field=overall_risk" in detail.warnings[0]
    assert "contract_value_repaired field=overall_risk" in caplog.text


@pytest.mark.asyncio
async def test_duplicate_boxes_are_dropped_logged_and_audited(tmp_path, caplog) -> None:
    caplog.set_level("WARNING", logger="babymonitorvl.monitor")
    backend = DuplicateBoxBackend()
    history = HistoryStore(1024 * 1024)
    service = MonitorService(
        Settings(frontend_dist=tmp_path, max_infants=1, max_adults=4),
        history,
        EventHub(),
        {ProviderName.OLLAMA: backend},
    )
    session_id = "duplicate-box-session"
    service._session_id = session_id
    service._config = MonitorStartRequest(
        rtsp_url="rtsp://camera.invalid/stream",
        provider=ProviderName.OLLAMA,
        model="test-model",
    )
    service._model = "test-model"
    install_capture(service, frame(1))
    task = asyncio.create_task(service._analysis_loop(session_id))

    async def wait_for_completion() -> None:
        while service._status.completed_count < 1:
            await asyncio.sleep(0)

    await asyncio.wait_for(wait_for_completion(), timeout=1)
    service._session_id = None
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)

    records, _ = await history.list(limit=10)
    detail = await history.get(records[0].id)
    assert detail is not None
    assert detail.analysis is not None
    assert len(detail.analysis.infants) == 1
    assert detail.attempts == 1
    assert detail.warnings == detail.attempt_details[0].warnings
    assert "duplicate_box_dropped category=infant" in detail.warnings[0]
    assert "duplicate_box_dropped category=infant" in caplog.text
