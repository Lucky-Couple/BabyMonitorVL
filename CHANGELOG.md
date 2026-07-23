# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and application releases follow Semantic Versioning.

## [Unreleased]

## [0.3.5] - 2026-07-23

### Added

- Added a separate temporal stabilization layer over validated VLM structures, with configurable `3-of-5` confirmation, three-frame clear hysteresis, same-category IoU association, EMA box smoothing, and bounded in-memory alarm timeline.
- Added `/api/alarm`, `alarm_updated` WebSocket events, per-history-record stabilized snapshots, stable subject/object presence signals, and explicit raw-versus-stabilized risk audit data.
- Added a prominent browser alarm panel, stable reason and subject/object indicators, compact raw/stable risk timeline, and a stable-box versus raw-box overlay switch.

### Changed

- Narrowly expanded the product boundary to permit deterministic temporal filtering of already-validated structured VLM output. Raw `FrameAnalysis`, raw boxes, provider responses, and single-frame history remain unchanged and available for debugging.
- The browser now presents an experimental visual alarm signal for human review, but still provides no audible alarm, external notification, medical guarantee, fail-safe behavior, or unattended-monitoring guarantee.
- Replaced raw/ambiguous RTSP failure strings with categorized operator messages for authentication, permission, missing streams, refused/timed-out connections, DNS failures, stalled frames, and invalid video, while retaining bounded redacted FFmpeg diagnostics.
- Made frontend API error parsing handle FastAPI string, object, and validation-array details without rendering `[object Object]`; clarified unknown stable states as missing, unreliable, or not-yet-confirmed infant information.
- Updated the shared prompt with an explicit numeric mouth/nose-to-object rectangle-intersection preflight and consistent blanket-state mapping, preventing body-only or below-face bedding boxes from claiming mouth/nose coverage.
- Added stable targeted retry guidance for mouth/nose spatial-grounding failures so Gemma-class models receive the violated geometry rule instead of only a generic Pydantic `value_error`.
- Replaced inference-cadence still-image preview with one source-cadence continuous FFmpeg/MJPEG pipeline shared by the browser and sequential model sampling. Preview-only frames remain ephemeral and never form an analysis queue.
- Made “最近完成分析” default to exact single-frame raw boxes while retaining the explicit stable-box switch.
- Updated live diagnostics to show decoded RTSP frame resolution, measured preview FPS, preview-JPEG byte rate, and the independent model minimum frame interval.
- Changed alarm WebSocket handling to append the current snapshot as one bounded timeline point, reserving full `/api/alarm` downloads for initial load and reconnection instead of refetching the complete timeline after every analysis.
- Removed unchanged alarm snapshots from periodic live-preview status events, while retaining status publication on connection/reconnection recovery.
- Marked the preserved alarm snapshot as a desaturated previous-session result whenever monitoring is stopped.
- Added sanitized real-provider compatibility evidence for the stricter mouth/nose prompt/validation path, including first-call success and retry counts without retaining source details, images, or raw household output.
- Expanded backend/TypeScript contract-sync checks across alarm reasons, timeline/state, attempt audit, history summary, and history detail; corrected the detail type so it no longer claims summary-only derived fields that the API does not return.

### Release metadata

- Application version: `0.3.5`.
- Prompt version: `baby-monitor-single-frame-v10-mouth-nose-spatial-preflight`.
- Analysis schema version: `1.3`.

## [0.3.0] - 2026-07-22

### Changed

- Excluded every `gemini-2*` model from Google AI Studio model discovery, even when stale capability metadata still advertises content generation.
- Updated the shared prompt with infrared/night-vision guidance that distinguishes fitted clothing from loose bedding through exposed-leg anchors, body-contour fit, drape, folds, and mattress overflow rather than grayscale color or texture.
- Promoted the latest unannotated RTSP sample to the primary monitor panel and added decoded resolution, actual/configured frame interval, and JPEG-pipe data-rate diagnostics with explicit source-resolution/bitrate labeling.
- Promoted the exact-frame annotated result to a full-width section 03 at its original aspect ratio, with debug history and request audit renumbered to sections 04 and 05.
- Removed the application-level 1280-pixel FFmpeg resize and its Web control; capture, preview, history, and model requests now preserve the RTSP stream's decoded source resolution unless a future tested model capability requires provider-specific adaptation.
- Replaced fixed-rate capture and latest-frame overwrite scheduling with demand-driven one-frame RTSP capture: the next frame is acquired only after the current model result finishes.
- Replaced the public `fps` request/status field with `min_frame_interval_seconds` (`0.1–3600`, default `1`) and made it the sole request-rate limiter measured between logical model-request start times.
- Removed obsolete capture/drop counters from backend status and the dashboard, and relabeled preview telemetry around effective analysis cadence rather than source-video sampling.

### Active contract metadata

- Application version: `0.3.0`.
- Prompt version: `baby-monitor-single-frame-v9-infrared-bedding-geometry`.
- Analysis schema version: `1.3`.

## [0.2.2] - 2026-07-22

### Fixed

- Upgraded the production container from Debian FFmpeg 7.1.5 to the pinned official FFmpeg 8.1.2 source release, verified by SHA-256, and added image-build execution checks for the exact RTSP timeout/open, filter, scaling, MJPEG, and image-pipe command contract.
- Removed the generic `rw_timeout` input option because FFmpeg 8.1.2 advertises it in protocol help but the RTSP demuxer rejects it while opening an RTSP input; the RTSP-native `timeout` option and Python complete-JPEG watchdog remain active.
- Prevented another silent FFmpeg CLI compatibility regression by making a command-capability smoke test part of the production image build rather than relying only on argument-list unit tests.
- Added full-value hover text to clipped dashboard metrics, status errors, headings, and history-card rows, and clarified that the top-level input/output counters are cumulative.

### Release metadata

- Application version: `0.2.2`.
- Prompt version: `baby-monitor-single-frame-v8-mouth-nose-occlusion`.
- Analysis schema version: `1.3`.

### Known limitations

- Experimental, human-reviewed demo only; no medical, life-safety, unattended-monitoring, authentication, persistence, multi-camera, temporal reasoning, or external alerting guarantees.

## [0.2.1] - 2026-07-21

### Added

- Added conservative adult-presence analysis with `present | not_detected | unknown` state, per-adult grounding boxes, confidence, and visible evidence.
- Added stable pink adult overlays and adult-first status/details in current results and debug history.
- Added per-call audit records that explicitly connect call number, the exact prompt sent, outcome, sanitized error, model response, usage, and retry reason, including failures that produce no response.
- Added configurable `MAX_INFANTS`/`MAX_ADULTS` limits, defaulting to one infant and four adults, with prompt/schema injection and local enforcement.
- Added exact same-category duplicate-box removal that preserves the first box and raw response while emitting server and per-call audit warnings.

### Changed

- Bumped the analysis schema to `1.3`; adult detection precedes cat detection, and infant assessment now distinguishes mouth/nose object coverage from simple camera visibility.
- Extended Qwen-family XYXY-to-canonical conversion and validation coverage to every adult box.
- Changed history cards to identify retry-success records and replaced the ambiguous combined error JSON with a numbered call-by-call audit view.
- Removed numeric suffixes from single infant/adult overlay labels and detail headings while retaining numbering for multiple subjects.
- Updated the shared prompt to `baby-monitor-single-frame-v8-mouth-nose-occlusion` with configured subject maxima, duplicate-box instructions, an empty-infant risk consistency check, and a narrowly bounded geometric mouth/nose overlap assessment.
- Replaced face-level visibility/coverage fields with mouth/nose-specific boxes, occlusion states, blanket coverage, and related-object relations; the model may estimate the region from connected head geometry but may not infer breathing or medical status.
- Made `mouth_nose_box` a required structured-output key whose value remains nullable, preventing providers from omitting it and then failing cross-field occlusion validation.
- Avoided a second inference for the deterministic `infants=[]` plus non-`unknown` risk conflict by applying an explicit audited repair while preserving the raw provider response.
- Unified all environment-backed settings to read defaults at `Settings()` construction time and added startup validation for history-budget and model-timeout lower bounds.
- Replaced the untyped public monitor-state dictionary with an assignment-validated Pydantic contract shared by HTTP and WebSocket status serialization.
- Added direct backend-to-TypeScript enum/interface synchronization tests and compile-time complete UI label coverage, including body-related object relations.
- Replaced field-name-based Qwen box conversion with recursive Pydantic-annotation-driven normalization plus an independently discovered JSON Schema box-path guard.
- Added layered RTSP stall recovery with FFmpeg network I/O timeouts and a low-FPS-aware complete-JPEG watchdog that forces half-open streams into the existing reconnect loop.
- Added explicit production WebSocket protocol ping settings, concurrent application disconnect observation, idle JSON heartbeats, bounded sends, and guarded frontend event parsing.

### Release metadata

- Application version: `0.2.1`.
- Prompt version: `baby-monitor-single-frame-v8-mouth-nose-occlusion`.
- Analysis schema version: `1.3`.

### Known limitations

- Experimental, human-reviewed demo only; no medical, life-safety, unattended-monitoring, authentication, persistence, multi-camera, temporal reasoning, or external alerting guarantees.

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
