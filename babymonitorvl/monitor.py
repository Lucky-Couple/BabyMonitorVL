from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
import uuid
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import ValidationError

from .config import Settings
from .coordinates import (
    CANONICAL_BOX_ORDER,
    ModelOutputError,
    SubjectLimitError,
    deduplicate_analysis_boxes,
    enforce_subject_limits,
    model_box_order,
    parse_model_analysis_with_repairs,
)
from .events import EventHub
from .ffmpeg import (
    build_ffmpeg_command,
    collect_stderr,
    describe_rtsp_failure,
    jpeg_dimensions,
    read_mjpeg_frames,
)
from .history import HistoryRecord, HistoryStore
from .prompt import PROMPT_VERSION, build_prompt, output_schema
from .providers import AnalysisRequest, VisionBackend
from .providers.base import aggregate_usage, should_retry_provider_error, token_count
from .schemas import (
    AlarmState,
    AnalysisAttempt,
    FrameAnalysis,
    HistoryStats,
    MonitorStartRequest,
    MonitorStatus,
    ProviderName,
)
from .stabilizer import StabilizerConfig, TemporalStabilizer


logger = logging.getLogger(__name__)
RTSP_URL_IN_TEXT = re.compile(r"rtsps?://[^\s|]+", re.IGNORECASE)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def redact_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ""
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parsed.port:
            host = f"{host}:{parsed.port}"
        if parsed.username is not None:
            host = f"***:***@{host}"
        query = urlencode([(key, "***") for key, _ in parse_qsl(parsed.query, keep_blank_values=True)])
        return urlunsplit((parsed.scheme, host, parsed.path, query, ""))
    except Exception:
        return "rtsp://***"


def redact_sensitive_text(value: str, secrets: tuple[str, ...]) -> str:
    """Remove exact non-empty provider secrets before text reaches history or APIs."""

    result = value
    for secret in secrets:
        if secret:
            result = result.replace(secret, "***")
    return result


def redact_rtsp_error_text(value: str, configured_url: str) -> str:
    """Redact configured and incidental RTSP URLs from provider/FFmpeg diagnostics."""

    result = value.replace(configured_url, redact_url(configured_url))

    def replace_url(match: re.Match[str]) -> str:
        candidate = match.group(0)
        trailing = ""
        while candidate and candidate[-1] in ".,;)]}":
            trailing = candidate[-1] + trailing
            candidate = candidate[:-1]
        return redact_url(candidate) + trailing

    return RTSP_URL_IN_TEXT.sub(replace_url, result)


def local_validation_correction(exc: Exception) -> str | None:
    """Describe local model-output failures without copying raw output into prompts."""

    if isinstance(exc, json.JSONDecodeError):
        return "root:invalid_json_envelope"
    if isinstance(exc, ModelOutputError):
        return "root:not_json_object"
    if isinstance(exc, SubjectLimitError):
        return "root:configured_subject_limit"
    if isinstance(exc, ValidationError):
        issues = []
        for item in exc.errors(include_input=False):
            location = ".".join(str(part) for part in item.get("loc", ())) or "root"
            message = str(item.get("msg", ""))
            if (
                "partially_covered requires a grounded related object" in message
                or "fully_covered requires a grounded related object" in message
            ):
                issue = "mouth_nose_spatial_grounding"
            elif "mouth_nose_box is required for clear, partially_covered, or fully_covered" in message:
                issue = "mouth_nose_box_required"
            else:
                issue = str(item.get("type", "invalid"))
            issues.append(f"{location}:{issue}")
        return ", ".join(issues[:8])
    return None


def validation_correction_prompt(baseline_prompt: str, correction: str) -> str:
    """Build a fixed, raw-output-free retry prompt with targeted local guidance."""

    targeted_guidance = ""
    if "mouth_nose_spatial_grounding" in correction:
        targeted_guidance = (
            " The failed mouth/nose coverage claim had no matching related-object box with "
            "positive-area intersection. Recompute the two returned rectangles before selecting "
            "the state: max(mouth_ymin, object_ymin) < min(mouth_ymax, object_ymax) AND "
            "max(mouth_xmin, object_xmin) < min(mouth_xmax, object_xmax) must both be true. "
            "If either test is false, do not return partially_covered or fully_covered and do not "
            "use a covering relation; choose clear, not_visible, or unknown from visible evidence. "
            "A nearby blanket below the face does not cover the mouth/nose."
        )
    elif "mouth_nose_box_required" in correction:
        targeted_guidance = (
            " A clear, partially_covered, or fully_covered state requires a non-null tight "
            "mouth_nose_box. If the region cannot be localized, return not_visible or unknown."
        )

    return (
        baseline_prompt
        + "\n\nVALIDATION CORRECTION: The previous output failed local response "
        + f"validation ({correction}). Return exactly one valid JSON object, include every "
        + "required key, and obey all enum and box constraints."
        + targeted_guidance
        + " Always return adult_presence and adults; adult_presence=present requires at "
        + "least one adult observation, and adults=[] when it is not_detected or unknown. "
        + "For every infant, always return mouth_nose_box and mouth_nose_occlusion; "
        + "partial/full coverage requires a boxed related object whose box overlaps "
        + "mouth_nose_box and has a matching relation; use not_visible or unknown "
        + "instead of inventing coverage. "
        + "Always return cats; use cats=[] when no real cat is clearly visible. "
        + "If no infant is visible, use infants=[] and overall_risk=unknown."
    )


def version_at_least(version: str | None, minimum: tuple[int, int, int]) -> bool:
    if not version:
        return False
    parts = [int(part) for part in re.findall(r"\d+", version)[:3]]
    parts.extend([0] * (3 - len(parts)))
    return tuple(parts[:3]) >= minimum


@dataclass(slots=True)
class CapturedFrame:
    image_bytes: bytes
    captured_at: datetime
    width: int
    height: int
    sequence: int


@dataclass(slots=True)
class CaptureTelemetry:
    """Measure recent continuous MJPEG preview cadence and encoded byte rate."""

    max_samples: int = 60
    samples: deque[tuple[float, int]] = field(default_factory=deque)

    def __post_init__(self) -> None:
        if self.max_samples < 2:
            raise ValueError("capture telemetry requires at least two samples")

    def observe(self, observed_at: float, jpeg_bytes: int) -> tuple[float | None, float | None]:
        if jpeg_bytes < 0:
            raise ValueError("JPEG byte count must be non-negative")
        self.samples.append((observed_at, jpeg_bytes))
        while len(self.samples) > self.max_samples:
            self.samples.popleft()
        if len(self.samples) < 2:
            return None, None
        elapsed = self.samples[-1][0] - self.samples[0][0]
        if elapsed <= 0:
            return None, None
        preview_fps = (len(self.samples) - 1) / elapsed
        preview_bitrate_kbps = sum(size for _, size in list(self.samples)[1:]) * 8 / elapsed / 1000
        return preview_fps, preview_bitrate_kbps


class MonitorService:
    def __init__(
        self,
        settings: Settings,
        history: HistoryStore,
        events: EventHub,
        providers: dict[ProviderName, VisionBackend],
    ) -> None:
        self.settings = settings
        self.history = history
        self.events = events
        self.providers = providers
        self._lock = asyncio.Lock()
        self._frame_condition = asyncio.Condition()
        self._capture_task: asyncio.Task[None] | None = None
        self._analysis_task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        self._config: MonitorStartRequest | None = None
        self._model: str | None = None
        self._latest_capture: CapturedFrame | None = None
        self.stabilizer = TemporalStabilizer(
            StabilizerConfig(
                window_size=settings.stability_window_size,
                confirmation_frames=settings.stability_confirmation_frames,
                clear_frames=settings.stability_clear_frames,
                box_iou_threshold=settings.stability_box_iou_threshold,
                box_ema_alpha=settings.stability_box_ema_alpha,
                timeline_max_points=settings.stability_timeline_max_points,
            )
        )
        self._status = self._new_status()

    @staticmethod
    def _new_status() -> MonitorStatus:
        return MonitorStatus()

    def _default_model(self, provider: ProviderName) -> str:
        if provider is ProviderName.OLLAMA:
            return self.settings.default_ollama_model
        return self.settings.default_gemini_model

    async def start(self, config: MonitorStartRequest) -> dict[str, Any]:
        async with self._lock:
            if self._session_id is not None:
                raise RuntimeError("a monitor session is already active")
            if shutil.which(self.settings.ffmpeg_binary) is None:
                raise RuntimeError(f"FFmpeg binary not found: {self.settings.ffmpeg_binary}")

            model = config.model or self._default_model(config.provider)
            provider = self.providers[config.provider]
            health = await provider.healthcheck()
            if not health.available:
                detail = redact_sensitive_text(health.detail, provider.sensitive_values())
                raise RuntimeError(detail)
            if config.provider is ProviderName.OLLAMA and model not in health.models:
                raise RuntimeError(f"Ollama model is not installed: {model}")
            if (
                config.provider is ProviderName.OLLAMA
                and model.startswith("qwen3-vl")
                and not version_at_least(health.version, (0, 12, 7))
            ):
                raise RuntimeError("qwen3-vl requires Ollama 0.12.7 or newer")

            session_id = str(uuid.uuid4())
            self._session_id = session_id
            self._config = config
            self._model = model
            self._latest_capture = None
            self._status = self._new_status()
            self._status.state = "connecting"
            self._status.session_id = session_id
            self._status.source = redact_url(config.rtsp_url)
            self._status.provider = config.provider.value
            self._status.model = model
            self._status.min_frame_interval_seconds = config.min_frame_interval_seconds
            self._status.alarm = self.stabilizer.start_session(session_id)
            self._capture_task = asyncio.create_task(self._capture_loop(session_id), name="rtsp-capture")
            self._analysis_task = asyncio.create_task(self._analysis_loop(session_id), name="frame-analysis")
        await self._publish_status()
        return {"session_id": session_id, "model": model}

    async def replace_provider(self, name: ProviderName, provider: VisionBackend) -> VisionBackend:
        """Atomically replace an idle provider and return the previous instance."""

        async with self._lock:
            if self._session_id is not None:
                raise RuntimeError("stop the active monitor session before changing provider credentials")
            previous = self.providers[name]
            self.providers[name] = provider
            return previous

    async def require_idle(self) -> None:
        async with self._lock:
            if self._session_id is not None:
                raise RuntimeError("stop the active monitor session before changing provider credentials")

    async def stop(self) -> None:
        async with self._lock:
            if self._session_id is None:
                return
            self._session_id = None
            tasks = [task for task in (self._capture_task, self._analysis_task) if task]
            for task in tasks:
                task.cancel()
            self._capture_task = None
            self._analysis_task = None
            process = self._process
            self._process = None
        async with self._frame_condition:
            self._frame_condition.notify_all()
        if process is not None:
            await self._terminate_process(process)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._config = None
            self._model = None
            self._status.state = "stopped"
            self._status.session_id = None
            self._status.reconnect_attempt = 0
            self._status.reconnect_delay_seconds = None
        await self._publish_status()

    async def close(self) -> None:
        await self.stop()
        await asyncio.gather(*(provider.close() for provider in self.providers.values()), return_exceptions=True)

    async def status(self) -> dict[str, Any]:
        self._status.history = HistoryStats.model_validate(await self.history.stats())
        self._status.alarm = self.stabilizer.state().current
        return self._status.model_dump(mode="json")

    def alarm_state(self) -> AlarmState:
        return self.stabilizer.state()

    async def latest_image(self) -> CapturedFrame | None:
        return self._latest_capture

    def live_stream(self, session_id: str | None = None) -> AsyncIterator[bytes] | None:
        """Return one shared-camera MJPEG stream for the requested active session."""

        active_session_id = self._session_id
        if active_session_id is None or (
            session_id is not None and session_id != active_session_id
        ):
            return None
        return self._mjpeg_stream(active_session_id)

    async def _mjpeg_stream(self, session_id: str) -> AsyncIterator[bytes]:
        last_sequence = 0
        while self._session_id == session_id:
            async with self._frame_condition:
                await self._frame_condition.wait_for(
                    lambda: self._session_id != session_id
                    or (
                        self._latest_capture is not None
                        and self._latest_capture.sequence > last_sequence
                    )
                )
                if self._session_id != session_id or self._latest_capture is None:
                    return
                frame = self._latest_capture
            last_sequence = frame.sequence
            yield (
                b"--frame\r\n"
                b"Content-Type: image/jpeg\r\n"
                + f"Content-Length: {len(frame.image_bytes)}\r\n\r\n".encode("ascii")
                + frame.image_bytes
                + b"\r\n"
            )

    async def _publish_status(self) -> None:
        await self.events.publish({"type": "status", "data": await self.status()})

    async def _request_capture(self, session_id: str) -> CapturedFrame:
        """Wait for the first continuously decoded frame after this model request point."""

        if self._session_id != session_id:
            raise asyncio.CancelledError
        async with self._frame_condition:
            baseline_sequence = self._latest_capture.sequence if self._latest_capture else 0
            await self._frame_condition.wait_for(
                lambda: self._session_id != session_id
                or (
                    self._latest_capture is not None
                    and self._latest_capture.sequence > baseline_sequence
                )
            )
            if self._session_id != session_id or self._latest_capture is None:
                raise asyncio.CancelledError
            return self._latest_capture

    async def _capture_loop(self, session_id: str) -> None:
        assert self._config is not None
        config = self._config
        sequence = 0
        reconnect_delay = 1
        reconnect_attempt = 0
        last_metadata_publish = 0.0
        try:
            while self._session_id == session_id:
                if reconnect_attempt > 0:
                    self._status.state = "reconnecting"
                    self._status.reconnect_delay_seconds = None
                else:
                    self._status.state = "connecting"
                    self._status.reconnect_delay_seconds = None
                await self._publish_status()

                command = build_ffmpeg_command(
                    self.settings.ffmpeg_binary,
                    config.rtsp_url,
                    config.rtsp_transport,
                    self.settings.rtsp_stall_timeout_seconds,
                )
                stderr_lines: list[str] = []
                process: asyncio.subprocess.Process | None = None
                stderr_task: asyncio.Task[None] | None = None
                telemetry = CaptureTelemetry()
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    self._process = process
                    assert process.stdout is not None and process.stderr is not None
                    stderr_task = asyncio.create_task(collect_stderr(process.stderr, stderr_lines))
                    frames = read_mjpeg_frames(
                        process.stdout,
                        self.settings.rtsp_stall_timeout_seconds,
                    )
                    async for image_bytes in frames:
                        width, height = jpeg_dimensions(image_bytes)
                        sequence += 1
                        observed_at = time.monotonic()
                        frame = CapturedFrame(image_bytes, utc_now(), width, height, sequence)
                        preview_fps, preview_bitrate_kbps = telemetry.observe(
                            observed_at,
                            len(image_bytes),
                        )
                        async with self._frame_condition:
                            self._latest_capture = frame
                            self._frame_condition.notify_all()

                        recovered = self._status.state != "streaming"
                        self._status.state = "streaming"
                        self._status.last_capture_at = frame.captured_at.isoformat()
                        self._status.reconnect_attempt = 0
                        self._status.reconnect_delay_seconds = None
                        self._status.last_error = None
                        reconnect_attempt = 0
                        reconnect_delay = 1
                        if recovered or observed_at - last_metadata_publish >= 1:
                            last_metadata_publish = observed_at
                            await self.events.publish(
                                {
                                    "type": "capture",
                                    "data": {
                                        "sequence": sequence,
                                        "captured_at": frame.captured_at.isoformat(),
                                        "image_url": f"/api/live/stream?session_id={session_id}",
                                        "width": frame.width,
                                        "height": frame.height,
                                        "preview_fps": preview_fps,
                                        "preview_bitrate_kbps": preview_bitrate_kbps,
                                    },
                                }
                            )
                        if recovered:
                            await self._publish_status()

                    if self._session_id == session_id:
                        stderr_lines.append("End of file before the next complete JPEG frame")
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stderr_lines.append(str(exc))
                finally:
                    if process is not None:
                        await self._terminate_process(process)
                    if stderr_task:
                        try:
                            await asyncio.wait_for(stderr_task, timeout=0.5)
                        except asyncio.TimeoutError:
                            stderr_task.cancel()
                        await asyncio.gather(stderr_task, return_exceptions=True)
                    if self._process is process:
                        self._process = None

                if self._session_id != session_id:
                    return
                message = describe_rtsp_failure(
                    stderr_lines[-6:],
                    self.settings.rtsp_stall_timeout_seconds,
                )
                message = redact_rtsp_error_text(message, config.rtsp_url)
                reconnect_attempt += 1
                self._status.state = "reconnecting"
                self._status.last_error = message
                self._status.reconnect_attempt = reconnect_attempt
                self._status.reconnect_delay_seconds = reconnect_delay
                await self._publish_status()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)
        except asyncio.CancelledError:
            raise

    async def _analysis_loop(self, session_id: str) -> None:
        assert self._config is not None and self._model is not None
        current_alarm = self.stabilizer.state().current
        if current_alarm is None or current_alarm.session_id != session_id:
            # Normal starts initialize the stabilizer in start(). This guard also keeps
            # direct scheduler harnesses and restored session orchestration deterministic.
            self._status.alarm = self.stabilizer.start_session(session_id)
        config = self._config
        model = self._model
        provider = self.providers[config.provider]
        box_order = model_box_order(config.provider, model)
        schema = output_schema(
            box_order,
            max_infants=self.settings.max_infants,
            max_adults=self.settings.max_adults,
        )
        prompt = build_prompt(schema, box_order)
        transport_schema = provider.prepare_output_schema(schema)
        generation_params = {
            "temperature": 0,
            "thinking": "disabled" if config.provider is ProviderName.OLLAMA else "provider_default",
            "schema_profile": provider.schema_profile,
            "model_box_order": box_order.value,
            "canonical_box_order": CANONICAL_BOX_ORDER.value,
            "max_infants": self.settings.max_infants,
            "max_adults": self.settings.max_adults,
        }
        last_submission_started: float | None = None
        while self._session_id == session_id:
            if last_submission_started is not None:
                wait_seconds = (
                    last_submission_started
                    + config.min_frame_interval_seconds
                    - time.monotonic()
                )
                if wait_seconds > 0:
                    await asyncio.sleep(wait_seconds)
            frame = await self._request_capture(session_id)
            if self._session_id != session_id:
                raise asyncio.CancelledError
            record = HistoryRecord(
                id=str(uuid.uuid4()),
                session_id=session_id,
                captured_at=frame.captured_at,
                provider=config.provider,
                model=model,
                source=redact_url(config.rtsp_url),
                image_bytes=frame.image_bytes,
                image_width=frame.width,
                image_height=frame.height,
                prompt_version=PROMPT_VERSION,
                prompt=prompt,
                output_schema=transport_schema,
                generation_params=generation_params,
            )
            await self.history.add(record)
            self._status.submitted_count += 1
            await self.events.publish(
                {"type": "analysis_started", "data": {"id": record.id, "captured_at": frame.captured_at.isoformat()}}
            )

            request = AnalysisRequest(
                image_bytes=frame.image_bytes,
                mime_type="image/jpeg",
                width=frame.width,
                height=frame.height,
                prompt=prompt,
                output_schema=transport_schema,
                model=model,
                generation_params=generation_params,
            )
            last_submission_started = time.monotonic()
            raw_responses: list[str] = []
            errors: list[str] = []
            warnings: list[str] = []
            attempt_details: list[AnalysisAttempt] = []
            usage: dict[str, Any] = {}
            attempt_usages: list[dict[str, Any]] = []
            analysis: FrameAnalysis | None = None
            started = time.perf_counter()
            attempts = 0
            try:
                for attempts in range(1, 3):
                    local_output_failure = False
                    response_index: int | None = None
                    attempt_usage: dict[str, Any] = {}
                    attempt_warnings: list[str] = []
                    try:
                        result = await provider.analyze(request)
                        raw_responses.append(result.raw_response)
                        response_index = len(raw_responses) - 1
                        attempt_usage = dict(result.usage)
                        attempt_usages.append(result.usage)
                        usage = aggregate_usage(attempt_usages)
                        self._status.input_tokens += token_count(result.usage, "input_tokens")
                        self._status.output_tokens += token_count(result.usage, "output_tokens")
                        try:
                            candidate, contract_warnings = parse_model_analysis_with_repairs(
                                result.raw_response,
                                box_order,
                            )
                            candidate, duplicate_warnings = deduplicate_analysis_boxes(candidate)
                            attempt_warnings = contract_warnings + duplicate_warnings
                            warnings.extend(attempt_warnings)
                            for warning in attempt_warnings:
                                logger.warning(
                                    "model output warning record_id=%s attempt=%s provider=%s model=%s %s",
                                    record.id,
                                    attempts,
                                    config.provider.value,
                                    model,
                                    warning,
                                )
                            enforce_subject_limits(
                                candidate,
                                max_infants=self.settings.max_infants,
                                max_adults=self.settings.max_adults,
                            )
                            analysis = candidate
                        except (json.JSONDecodeError, ModelOutputError, SubjectLimitError, ValidationError):
                            local_output_failure = True
                            raise
                        attempt_details.append(
                            AnalysisAttempt(
                                attempt=attempts,
                                prompt=request.prompt,
                                outcome="success",
                                response_index=response_index,
                                usage=attempt_usage,
                                warnings=attempt_warnings,
                            )
                        )
                        break
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        correction = local_validation_correction(exc) if local_output_failure else None
                        safe_error = str(exc).replace(config.rtsp_url, redact_url(config.rtsp_url))
                        safe_error = redact_sensitive_text(safe_error, provider.sensitive_values())
                        errors.append(f"attempt {attempts}: {type(exc).__name__}: {safe_error}")
                        retryable = should_retry_provider_error(exc)
                        will_retry = attempts == 1 and retryable
                        retry_reason = None
                        if will_retry:
                            retry_reason = (
                                f"local_validation:{correction}" if correction is not None else "retryable_provider_error"
                            )
                        attempt_details.append(
                            AnalysisAttempt(
                                attempt=attempts,
                                prompt=request.prompt,
                                outcome="validation_error" if local_output_failure else "provider_error",
                                error_type=type(exc).__name__,
                                error=safe_error,
                                response_index=response_index,
                                usage=attempt_usage,
                                warnings=attempt_warnings,
                                will_retry=will_retry,
                                retry_reason=retry_reason,
                            )
                        )
                        if not retryable:
                            break
                        if attempts == 1 and correction is not None:
                            request = AnalysisRequest(
                                image_bytes=request.image_bytes,
                                mime_type=request.mime_type,
                                width=request.width,
                                height=request.height,
                                prompt=validation_correction_prompt(prompt, correction),
                                output_schema=request.output_schema,
                                model=request.model,
                                generation_params=request.generation_params,
                            )
                latency_ms = (time.perf_counter() - started) * 1000
                if self._session_id != session_id:
                    raise asyncio.CancelledError
                if analysis is not None:
                    stabilized = self.stabilizer.observe(
                        session_id=session_id,
                        record_id=record.id,
                        observed_at=utc_now(),
                        analysis=analysis,
                    )
                    updated = await self.history.update(
                        record.id,
                        status="success",
                        analysis=analysis,
                        stabilized=stabilized,
                        raw_responses=raw_responses,
                        errors=errors,
                        warnings=warnings,
                        attempt_details=attempt_details,
                        latency_ms=latency_ms,
                        attempts=attempts,
                        usage=usage,
                    )
                    self._status.completed_count += 1
                    self._status.last_analysis_at = utc_now().isoformat()
                    self._status.last_latency_ms = round(latency_ms, 1)
                    self._status.last_record_id = record.id
                    self._status.last_error = None
                    self._status.alarm = stabilized
                    await self.events.publish(
                        {
                            "type": "alarm_updated",
                            "data": stabilized.model_dump(mode="json"),
                        }
                    )
                    await self.events.publish(
                        {
                            "type": "analysis_completed",
                            "data": updated.as_summary().model_dump(mode="json") if updated else {"id": record.id},
                        }
                    )
                else:
                    updated = await self.history.update(
                        record.id,
                        status="error",
                        analysis=None,
                        raw_responses=raw_responses,
                        errors=errors,
                        warnings=warnings,
                        attempt_details=attempt_details,
                        latency_ms=latency_ms,
                        attempts=attempts,
                        usage=usage,
                    )
                    self._status.error_count += 1
                    self._status.last_analysis_at = utc_now().isoformat()
                    self._status.last_latency_ms = round(latency_ms, 1)
                    self._status.last_record_id = record.id
                    self._status.last_error = errors[-1] if errors else "model analysis failed"
                    await self.events.publish(
                        {
                            "type": "analysis_failed",
                            "data": updated.as_summary().model_dump(mode="json") if updated else {"id": record.id},
                        }
                    )
                await self._publish_status()
            except asyncio.CancelledError:
                latency_ms = (time.perf_counter() - started) * 1000
                cancellation_error = "analysis canceled because the session stopped"
                if attempts > 0 and not any(item.attempt == attempts for item in attempt_details):
                    attempt_details.append(
                        AnalysisAttempt(
                            attempt=attempts,
                            prompt=request.prompt,
                            outcome="cancelled",
                            error_type="CancelledError",
                            error=cancellation_error,
                        )
                    )
                await self.history.update(
                    record.id,
                    status="error",
                    analysis=None,
                    raw_responses=raw_responses,
                    errors=[*errors, cancellation_error],
                    warnings=warnings,
                    attempt_details=attempt_details,
                    latency_ms=latency_ms,
                    attempts=attempts,
                    usage=usage,
                )
                raise

    @staticmethod
    async def _terminate_process(process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            process.terminate()
        try:
            await asyncio.wait_for(process.wait(), timeout=3)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                process.kill()
            await process.wait()
