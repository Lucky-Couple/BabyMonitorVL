from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import shutil
import time
import uuid
from dataclasses import dataclass
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
from .ffmpeg import build_ffmpeg_command, collect_stderr, jpeg_dimensions, read_mjpeg_frames
from .history import HistoryRecord, HistoryStore
from .prompt import PROMPT_VERSION, build_prompt, output_schema
from .providers import AnalysisRequest, VisionBackend
from .providers.base import aggregate_usage, should_retry_provider_error, token_count
from .schemas import AnalysisAttempt, FrameAnalysis, MonitorStartRequest, ProviderName


logger = logging.getLogger(__name__)


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
            issues.append(f"{location}:{item.get('type', 'invalid')}")
        return ", ".join(issues[:8])
    return None


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


def offer_latest(queue: asyncio.Queue[CapturedFrame], frame: CapturedFrame) -> bool:
    """Put a frame without blocking, replacing the queued frame when necessary."""
    dropped = False
    if queue.full():
        with contextlib.suppress(asyncio.QueueEmpty):
            queue.get_nowait()
            dropped = True
    queue.put_nowait(frame)
    return dropped


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
        self._queue: asyncio.Queue[CapturedFrame] = asyncio.Queue(maxsize=1)
        self._capture_task: asyncio.Task[None] | None = None
        self._analysis_task: asyncio.Task[None] | None = None
        self._process: asyncio.subprocess.Process | None = None
        self._session_id: str | None = None
        self._config: MonitorStartRequest | None = None
        self._model: str | None = None
        self._latest_capture: CapturedFrame | None = None
        self._state: dict[str, Any] = self._new_state()

    @staticmethod
    def _new_state() -> dict[str, Any]:
        return {
            "state": "stopped",
            "session_id": None,
            "source": None,
            "provider": None,
            "model": None,
            "fps": None,
            "capture_count": 0,
            "submitted_count": 0,
            "completed_count": 0,
            "error_count": 0,
            "dropped_count": 0,
            "last_capture_at": None,
            "last_analysis_at": None,
            "last_latency_ms": None,
            "last_record_id": None,
            "last_error": None,
            "reconnect_attempt": 0,
            "reconnect_delay_seconds": None,
            "input_tokens": 0,
            "output_tokens": 0,
        }

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
            self._queue = asyncio.Queue(maxsize=1)
            self._latest_capture = None
            self._state = self._new_state()
            self._state.update(
                {
                    "state": "connecting",
                    "session_id": session_id,
                    "source": redact_url(config.rtsp_url),
                    "provider": config.provider.value,
                    "model": model,
                    "fps": config.fps,
                }
            )
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
        if process is not None:
            await self._terminate_process(process)
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        async with self._lock:
            self._config = None
            self._model = None
            self._state["state"] = "stopped"
            self._state["session_id"] = None
            self._state["reconnect_attempt"] = 0
            self._state["reconnect_delay_seconds"] = None
        await self._publish_status()

    async def close(self) -> None:
        await self.stop()
        await asyncio.gather(*(provider.close() for provider in self.providers.values()), return_exceptions=True)

    async def status(self) -> dict[str, Any]:
        state = dict(self._state)
        state["history"] = await self.history.stats()
        return state

    async def latest_image(self) -> CapturedFrame | None:
        return self._latest_capture

    async def _publish_status(self) -> None:
        await self.events.publish({"type": "status", "data": await self.status()})

    async def _capture_loop(self, session_id: str) -> None:
        assert self._config is not None
        config = self._config
        reconnect_delay = 1
        reconnect_attempt = 0
        sequence = 0
        try:
            while self._session_id == session_id:
                self._state.update(
                    {
                        "state": "connecting" if reconnect_attempt == 0 else "reconnecting",
                        "reconnect_delay_seconds": None,
                    }
                )
                await self._publish_status()
                command = build_ffmpeg_command(
                    self.settings.ffmpeg_binary,
                    config.rtsp_url,
                    config.fps,
                    config.rtsp_transport,
                    config.max_image_edge,
                )
                stderr_lines: list[str] = []
                process: asyncio.subprocess.Process | None = None
                stderr_task: asyncio.Task[None] | None = None
                try:
                    process = await asyncio.create_subprocess_exec(
                        *command,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    self._process = process
                    assert process.stdout is not None and process.stderr is not None
                    stderr_task = asyncio.create_task(collect_stderr(process.stderr, stderr_lines))
                    async for image_bytes in read_mjpeg_frames(process.stdout):
                        if self._session_id != session_id:
                            break
                        try:
                            width, height = jpeg_dimensions(image_bytes)
                        except ValueError as exc:
                            self._state["last_error"] = str(exc)
                            continue
                        sequence += 1
                        captured_at = utc_now()
                        frame = CapturedFrame(image_bytes, captured_at, width, height, sequence)
                        self._latest_capture = frame
                        self._state.update(
                            {
                                "state": "streaming",
                                "capture_count": sequence,
                                "last_capture_at": captured_at.isoformat(),
                                "reconnect_attempt": 0,
                                "reconnect_delay_seconds": None,
                            }
                        )
                        reconnect_delay = 1
                        reconnect_attempt = 0
                        if offer_latest(self._queue, frame):
                            self._state["dropped_count"] += 1
                        await self.events.publish(
                            {
                                "type": "capture",
                                "data": {
                                    "sequence": sequence,
                                    "captured_at": captured_at.isoformat(),
                                    "image_url": f"/api/live/image?v={sequence}",
                                    "width": width,
                                    "height": height,
                                },
                            }
                        )
                        await self._publish_status()
                    await process.wait()
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    stderr_lines.append(str(exc))
                finally:
                    if process is not None:
                        await self._terminate_process(process)
                    if stderr_task:
                        stderr_task.cancel()
                        await asyncio.gather(stderr_task, return_exceptions=True)
                    if self._process is process:
                        self._process = None

                if self._session_id != session_id:
                    break
                message = " | ".join(filter(None, stderr_lines[-3:])) or "FFmpeg stream ended"
                message = message.replace(config.rtsp_url, redact_url(config.rtsp_url))
                reconnect_attempt += 1
                self._state.update(
                    {
                        "state": "reconnecting",
                        "last_error": message,
                        "reconnect_attempt": reconnect_attempt,
                        "reconnect_delay_seconds": reconnect_delay,
                    }
                )
                await self._publish_status()
                await asyncio.sleep(reconnect_delay)
                reconnect_delay = min(reconnect_delay * 2, 30)
        except asyncio.CancelledError:
            raise

    async def _analysis_loop(self, session_id: str) -> None:
        assert self._config is not None and self._model is not None
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
        while self._session_id == session_id:
            frame = await self._queue.get()
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
            self._state["submitted_count"] += 1
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
                        self._state["input_tokens"] += token_count(result.usage, "input_tokens")
                        self._state["output_tokens"] += token_count(result.usage, "output_tokens")
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
                                prompt=(
                                    prompt + "\n\nVALIDATION CORRECTION: The previous output failed local response "
                                    + f"validation ({correction}). Return exactly one valid JSON object, include every "
                                    + "required key, and obey all enum and box constraints. "
                                    + "Always return adult_presence and adults; adult_presence=present requires at "
                                    + "least one adult observation, and adults=[] when it is not_detected or unknown. "
                                    + "Always return cats; use cats=[] when no real cat is clearly visible. "
                                    + "If no infant is visible, use infants=[] and overall_risk=unknown."
                                ),
                                output_schema=request.output_schema,
                                model=request.model,
                                generation_params=request.generation_params,
                            )
                latency_ms = (time.perf_counter() - started) * 1000
                if self._session_id != session_id:
                    raise asyncio.CancelledError
                if analysis is not None:
                    updated = await self.history.update(
                        record.id,
                        status="success",
                        analysis=analysis,
                        raw_responses=raw_responses,
                        errors=errors,
                        warnings=warnings,
                        attempt_details=attempt_details,
                        latency_ms=latency_ms,
                        attempts=attempts,
                        usage=usage,
                    )
                    self._state.update(
                        {
                            "completed_count": self._state["completed_count"] + 1,
                            "last_analysis_at": utc_now().isoformat(),
                            "last_latency_ms": round(latency_ms, 1),
                            "last_record_id": record.id,
                            "last_error": None,
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
                    self._state.update(
                        {
                            "error_count": self._state["error_count"] + 1,
                            "last_analysis_at": utc_now().isoformat(),
                            "last_latency_ms": round(latency_ms, 1),
                            "last_record_id": record.id,
                            "last_error": errors[-1] if errors else "model analysis failed",
                        }
                    )
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
