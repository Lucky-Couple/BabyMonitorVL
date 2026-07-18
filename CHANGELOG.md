# Changelog

All notable changes to this project are documented here. The format follows Keep a Changelog, and application releases follow Semantic Versioning.

## [Unreleased]

### Changed

- Prepared repository-level development, architecture, contract, and release documentation.
- Pinned frontend dependency declarations to the versions recorded in the lockfile.
- Centralized FastAPI application version reporting on `babymonitorvl.__version__`.

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
