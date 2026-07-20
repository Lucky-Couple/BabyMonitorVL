# BabyMonitorVL agent development contract

This file is the repository-level instruction set for coding agents. Read it before changing code. If a task conflicts with this file, follow the explicit user request, document the deviation, and keep the change as narrow as possible.

## Product boundary

BabyMonitorVL is an experimental, human-reviewed, single-camera visual-language-model demo. It is not a medical device, a life-safety alarm, or an unattended monitoring system. Every user-facing surface must keep that limitation visible.

The MVP intentionally uses a multimodal LLM/VLM for all semantic visual interpretation. This boundary is non-negotiable unless the product owner explicitly changes it:

- FFmpeg may decode RTSP, sample at a fixed FPS, resize while preserving aspect ratio, and encode JPEG.
- Do not add OpenCV, YOLO, MediaPipe, image classifiers, conventional object detectors, trackers, optical flow, pose estimation, segmentation models, or CV-based temporal smoothing.
- Do not infer breathing, health, emotion, diagnosis, events outside the current frame, or medical advice.
- Each inference request contains one still frame. Do not add hidden cross-frame context or tracking.
- Keep the latest-frame queue at size one. When inference is slower than capture, replace the queued frame and increment the dropped-frame counter; never build an unbounded latency backlog.

## Architectural invariants

- One active monitor session and one inference call at a time.
- Capture and inference remain separate asynchronous tasks.
- `latest_capture` is for the unannotated live preview. The annotated result must always use the exact historical JPEG submitted to the model.
- History is process-memory-only and is pruned only by `HISTORY_MAX_BYTES`. Do not add persistence, a frame-count cap, or a TTL without an explicit product decision and migration note.
- RTSP reconnect backoff remains bounded exponential backoff: 1, 2, 4, 8, 16, then 30 seconds. Status keeps consecutive `reconnect_attempt` separate from nullable `reconnect_delay_seconds`; never overload the attempt counter with a duration. Preserve both FFmpeg network I/O timeouts and the Python complete-JPEG watchdog so a half-open camera cannot block reconnection indefinitely. The watchdog interval must account for configured low FPS.
- No audio, push, SMS, email, or audible alarm is part of this MVP.
- API and UI coordinates are always canonical `[ymin, xmin, ymax, xmax]`, integer normalized to `0..1000`.
- Ollama model basenames matching `qwen*` use model-native `[xmin, ymin, xmax, ymax]`; convert every box to canonical order before Pydantic validation and API/history exposure. Conversion is driven recursively by Pydantic `BoundingBox` annotations, not a field-name allowlist. Gemini and unknown model families use canonical order unless a tested model adapter says otherwise.
- `mouth_nose_box` is the sole permitted limited hidden-region estimate: the VLM may infer its spatial location only from connected visible head geometry, orientation, outline, and nearby facial landmarks. Partial/full occlusion still requires visible object pixels overlapping that region. Never extend this exception to airflow, breathing, suffocation, health, hidden-body reconstruction, or temporal inference.
- Never silently clamp, reorder, smooth, or fabricate model boxes. The sole deduplication exception is exact coordinate equality within the same semantic category: keep the first box, drop later boxes, preserve the raw response, emit a server warning, and store that warning in per-call history. Related objects are deduplicated only within one infant observation; the same object box may remain associated with different infants. Never add IoU/fuzzy suppression without an explicit product decision.
- Preserve provider raw responses byte-for-character in history. Parsing may tolerate only one JSON value with an optional `json`/empty Markdown fence wrapper; never discard prose, accept a second JSON value, or use greedy substring extraction to hide malformed output.
- Preserve explicit per-call audit mapping between attempt number, the exact prompt sent for that call, outcome, sanitized error, response index, usage, and retry reason. Provider failures can occur before a response or usage exists, so never correlate parallel arrays by list position. The top-level history prompt is only the immutable session baseline; retry corrections belong to the corresponding attempt record.

See [Architecture](docs/ARCHITECTURE.md), [Analysis contract](docs/ANALYSIS_CONTRACT.md), [provider compatibility method](docs/PROVIDER_COMPATIBILITY.md), and [Gemini/Gemma provider rules](docs/GEMINI_PROVIDER.md) before touching scheduling, schemas, prompts, providers, or coordinates.

## Repository map

- `babymonitorvl/main.py`: FastAPI composition, API routes, WebSocket, frontend static hosting.
- `babymonitorvl/config.py`: environment-backed immutable runtime settings and defaults.
- `babymonitorvl/events.py`: bounded WebSocket event fan-out and subscriber lifecycle.
- `babymonitorvl/monitor.py`: session lifecycle, capture/inference scheduling, retry and status accounting.
- `babymonitorvl/ffmpeg.py`: command construction, MJPEG framing, JPEG dimension parsing. No semantic vision belongs here.
- `babymonitorvl/schemas.py`: public structured analysis and API contracts.
- `babymonitorvl/prompt.py`: shared, versioned provider-neutral prompt and generated JSON Schema.
- `babymonitorvl/coordinates.py`: per-provider/model coordinate convention and canonical conversion.
- `babymonitorvl/providers/`: provider adapters only; no provider-specific semantic prompt content.
- `babymonitorvl/history.py`: byte-accounted in-memory records.
- `frontend/src/App.tsx`: monitor controls, live/result panels, history, overlays, debug response formatting.
- `frontend/src/types.ts`: frontend mirror of public response types.
- `tests/`: default offline test suite; real-provider calls must remain opt-in.

## Development environment rules

- Prefer Docker for building, testing, smoke checks, and FFmpeg-dependent work.
- Use `uv` for Python dependency management. Do not run global `pip install` and do not modify the system Python.
- If native development is required, `uv sync --frozen` may create only the repository-local `.venv`.
- Use the pinned `pnpm` version declared in `frontend/package.json`; do not use npm or yarn to rewrite the lockfile.
- Lockfiles are release inputs. Any dependency declaration change must update and validate `uv.lock` or `frontend/pnpm-lock.yaml` in the same change.
- Never replace pinned frontend versions with `latest`.
- Do not commit `.env`, model weights, JPEG frames, RTSP recordings, build output, caches, or local virtual environments.

Canonical commands are in [Development](docs/DEVELOPMENT.md). Do not invent a second setup path without updating that document.

## Change workflow

Before editing:

1. Read this file and the relevant document under `docs/`.
2. Inspect the working tree and preserve unrelated user changes.
3. Identify whether the change affects the public schema, prompt baseline, coordinate mapping, provider request, scheduler, privacy boundary, or release version.

While editing:

- Keep changes scoped and explicit; avoid unrelated refactors.
- Maintain async cancellation and session-id guards around capture/inference tasks.
- WebSocket delivery must concurrently observe client disconnects, bound every application send, and retain an idle heartbeat; protocol ping alone does not release an application task that never receives a disconnect event.
- Sanitize errors before storing or publishing them.
- Add or update tests at the same time as behavior changes.
- Keep backend Pydantic types and frontend TypeScript types synchronized.
- Public monitor status must remain a validated `MonitorStatus` model; do not replace it with an untyped dictionary. Keep the Python/TypeScript enum and interface sync tests current when either public contract changes.
- Keep UI enum labels stable and preserve English `summary`/`evidence` for provider comparison.
- The overlay category palette is stable: infant blue, mouth/nose green, blanket amber, pillow indigo, toy orange, hand cyan, other occluder red, cat purple, adult pink. Change it only on explicit UI direction.
- Current overlay boxes are thin (`2` main, `1.5` history) and label backgrounds use `fillOpacity=0.45`. Do not add label displacement, fuzzy deduplication, leader lines, or CV-derived corrections without explicit approval.

Before handoff:

1. Run the offline Python tests.
2. Run frontend typecheck/build with the frozen lockfile.
3. Build the production Docker image.
4. Run the secret scan and review the intended tracked-file list.
5. Update documentation and `CHANGELOG.md` when behavior, contracts, setup, or operational expectations change.
6. Report checks actually run; never claim a real-provider smoke test unless it was executed.

## Prompt and schema discipline

`FrameAnalysis`, the prompt, and provider structured-output settings form one cross-provider comparison baseline.

- Generate the JSON Schema from Pydantic; do not maintain a separate handwritten schema.
- A semantic prompt change requires a new `PROMPT_VERSION` and snapshot assertions.
- A breaking or required-field schema change requires a new `schema_version`, synchronized frontend types, provider tests, coordinate tests for every box field, API tests, and changelog entry.
- Provider adapters may translate transport and structured-output mechanics. They must not append provider-specific semantic instructions.
- Provider schema compatibility belongs in `VisionBackend.prepare_output_schema()`. Ollama keeps the complete Pydantic schema; Gemini must use the smoke-validated `google-ai-structured-output-compact-v1` representation while retaining the complete schema in the prompt and `FrameAnalysis` validation. Never bypass or broaden the compact projection merely because a keyword works in isolation; require the evidence and tests in `docs/GEMINI_PROVIDER.md`.
- Keep temperature at zero unless an explicit experiment changes the comparison baseline.
- Ollama sends `think=false`. Gemini omits `thinking_level` for the dynamic model list and uses the provider/model default. Never add a Gemini thinking override without the capability adapter, evidence, fallback, and tests required by `docs/GEMINI_PROVIDER.md`. If a provider reports thinking tokens, count them as output tokens.
- Gemini inference uses the SDK's native `client.aio.interactions.create()` path so monitor cancellation reaches the in-flight HTTP coroutine. Do not replace it with `asyncio.to_thread()` around the synchronous call.
- Retry at most once for transient/unclassified failures and local validation failures. Do not replay deterministic provider HTTP 4xx errors with an unchanged request. Preserve raw responses and errors for debugging.
- Treat model hallucinations as visible model output. Improve the shared prompt or contract; do not hide failures with conventional CV post-processing.

## Provider changes

A new backend must implement `VisionBackend.healthcheck()`, `analyze()`, and `close()`; return raw output plus standardized token usage; enforce the shared prompt/schema; keep API keys server-side; and include offline mocked tests for request mapping, model listing, timeout/error behavior, usage normalization, and raw-response preservation.

For Gemini/Gemma specifically, SDK-level field availability is not evidence of model-level support. Follow [Gemini/Gemma provider rules](docs/GEMINI_PROVIDER.md); unknown models must use the portable baseline with optional model-specific fields omitted. Every Google transport schema must pass the full-project allowlist traversal test.

Adding a model-family coordinate exception requires:

- a narrow, documented matcher in `model_box_order()`;
- provider/model-specific schema description and prompt instruction;
- conversion of infant, mouth/nose, related-object, adult, cat, and all future box fields;
- tests proving raw responses remain unchanged and API/UI output is canonical.

## Security and privacy

- Never commit or paste real RTSP credentials, API keys, camera IPs, captured images, or household details into source, fixtures, docs, logs, screenshots, or changelogs.
- Gemini credentials may come from the backend environment or the web settings endpoint. Web values must remain process-memory-only, must be validated before replacing the active provider, and must never enter browser storage, history, logs, errors, events, or API responses. Credential replacement is allowed only while monitoring is stopped.
- Redact RTSP username, password, and every query value before status, history, events, errors, or logs.
- The frontend keeps the RTSP draft in browser `sessionStorage` only so provider/model changes do not clear it. Do not move it to persistent storage without an explicit privacy decision.
- Gemini sends selected frames to Google. Any Gemini UI or workflow must keep that privacy difference clear.
- Default published binding remains `127.0.0.1:8000`; do not expose the service publicly without authentication and a separate security review.

## Testing gates

Default CI-equivalent checks must not require a camera, Ollama, Gemini credentials, or internet after dependencies are available:

```bash
uv run --frozen pytest -q -p no:cacheprovider
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend typecheck
pnpm --dir frontend build
docker build -t babymonitorvl:verify .
```

Prefer the Docker-isolated variants from `docs/DEVELOPMENT.md` when avoiding host changes. Real Ollama/Gemini tests are explicit smoke/contract checks and must never enter the default test suite.

## Versioning and release

- Application version currently appears in `pyproject.toml`, `babymonitorvl/__init__.py`, and `frontend/package.json`; keep all three identical. FastAPI reads `babymonitorvl.__version__`.
- Prompt and analysis schema versions are independent from the application SemVer.
- Follow SemVer for application releases and Keep-a-Changelog structure in `CHANGELOG.md`.
- Do not create a Git tag, commit, push, registry image, or public release unless explicitly authorized.
- Follow [Release checklist](docs/RELEASE.md). The project is MIT licensed; do not change the license or copyright holder without explicit owner approval.

## Current intentional limitations

Single camera, single process, single inference concurrency, no authentication, no persistence, no audio, no notifications, no temporal reasoning, no medical judgment, and no automatic fail-safe behavior. Do not describe these as production-ready capabilities.
