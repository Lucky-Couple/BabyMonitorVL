# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and application releases follow Semantic Versioning.

## [Unreleased]

### Added

- Added conservative adult-presence analysis with `present | not_detected | unknown` state, per-adult grounding boxes, confidence, and visible evidence.
- Added stable pink adult overlays and adult-first status/details in current results and debug history.
- Added per-call audit records that explicitly connect call number, outcome, sanitized error, model response, usage, and retry reason, including failures that produce no response.
- Added configurable `MAX_INFANTS`/`MAX_ADULTS` limits, defaulting to one infant and four adults, with prompt/schema injection and local enforcement.
- Added exact same-category duplicate-box removal that preserves the first box and raw response while emitting server and per-call audit warnings.

### Changed

- Bumped the analysis schema to `1.2`; adult detection now precedes cat detection and rejects partial/indirect or age-ambiguous evidence.
- Extended Qwen-family XYXY-to-canonical conversion and validation coverage to every adult box.
- Changed history cards to identify retry-success records and replaced the ambiguous combined error JSON with a numbered call-by-call audit view.
- Removed numeric suffixes from single infant/adult overlay labels and detail headings while retaining numbering for multiple subjects.
- Updated the shared prompt to `baby-monitor-single-frame-v7-risk-consistency` with configured subject maxima, duplicate-box instructions, and a mandatory empty-infant risk consistency check reflected in the generated schema description.
- Avoided a second inference for the deterministic `infants=[]` plus non-`unknown` risk conflict by applying an explicit audited repair while preserving the raw provider response.

## [0.2.0] - 2026-07-19

### Changed

- Prepared repository-level development, architecture, contract, and release documentation.
- Pinned frontend dependency declarations to the versions recorded in the lockfile.
- Centralized FastAPI application version reporting on `babymonitorvl.__version__`.
- Expanded Google AI Studio model discovery to include Interactions-compatible Gemma 4 image-input models alongside Gemini models.
- Migrated Gemini/Gemma calls to google-genai 2.x and the current polymorphic Interactions response schema, with stateless remote storage and updated usage accounting.
- Moved the Google frame-upload privacy notice from the main control card into the Gemini Key dialog.
- Removed the invalid universal Gemini/Gemma `minimal` thinking override; dynamic Google models now use their provider defaults, and deterministic HTTP 4xx errors are no longer blindly retried.
- Added a documented model-capability review, testing, and fallback policy to prevent SDK-level fields from being mistaken for cross-model support.
- Added a provider-level, real-smoke-validated Google AI compact structured-output projection that inlines references and retains fields/required/enums while preserving the full prompt and local Pydantic validation contract.
- Added strict single-value JSON decoding that tolerates only an optional Markdown fence wrapper, fixing valid Gemma structured responses with a stray closing fence while continuing to reject prose and duplicate JSON values.
- Added a provider-neutral compatibility engineering playbook covering layered diagnosis, minimal real-provider experiments, portable request baselines, schema projection, strict parsing, retry classification, secret ownership, coordinate adapters, and release regression gates.
- Separated RTSP reconnect ordinals from retry-delay seconds in status and surfaced both in the reconnecting UI state.
- Switched Gemini inference from a synchronous worker thread to the SDK's native async Interactions client with cancellation-aware timeouts and complete sync/async transport cleanup.
- Added concise retry correction prompts for local JSON-envelope and non-object failures as well as Pydantic validation failures.

### Added

- Added a compact Gemini Key settings dialog with server-side validation, process-memory-only overrides, startup-configuration reset, and model-list refresh.

### Security

- Web-submitted Gemini keys are never echoed or browser-persisted, and cannot be changed during an active monitor session.
- Provider-owned runtime secrets, including web-submitted Gemini keys, are redacted from health details and model-analysis errors before reaching APIs, events, status, or history.

### Release metadata

- Application version: `0.2.0`.
- Prompt version: `baby-monitor-single-frame-v4-cat-detection`.
- Analysis schema version: `1.1`.

### Known limitations

- Experimental, human-reviewed demo only; no medical, life-safety, unattended-monitoring, authentication, persistence, multi-camera, temporal reasoning, or external alerting guarantees.

## [0.1.0] - 2026-07-18

### Added

- Single-camera RTSP capture with FFmpeg-only decoding, fixed-FPS sampling, resizing, and MJPEG piping.
- Latest-frame queue semantics with dropped-frame accounting and bounded reconnect backoff.
- Provider-neutral VLM interface with Ollama and Gemini implementations.
- Structured single-frame analysis for infants, posture, face visibility, blanket coverage, related objects, cats, risks, evidence, and normalized boxes.
- Qwen-family native XYXY prompt/schema adapter with canonical YXYX API/UI conversion.
- In-memory byte-budgeted debug history containing submitted frames, raw responses, errors, prompts, schemas, generation parameters, latency, attempts, and token usage.
- React debugging UI with exact-frame overlays, separate live preview, fixed category colors, pretty/highlighted JSON, model selection, history pagination, and token totals.
- Docker-first runtime containing FFmpeg, a production frontend build, and host Ollama connectivity.
- Offline tests for schemas, prompts, coordinates, providers, token accounting, queue replacement, URL redaction, MJPEG parsing, API sanitization, and memory pruning.

### Security

- RTSP credentials and query values are redacted from status, history, events, validation responses, and stored errors.
- Gemini keys remain backend-only environment values.

### Known limitations

- Experimental demo only; no medical, life-safety, unattended-monitoring, authentication, persistence, multi-camera, temporal reasoning, or external alerting guarantees.

### License

- Released under the MIT License, Copyright (c) 2026 Lucky Couple.
