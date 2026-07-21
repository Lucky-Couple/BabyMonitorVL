# Development environment

## Supported toolchain

- Docker with Compose v2 (preferred)
- Python 3.11 or newer; production image currently uses Python 3.12
- `uv` 0.8.x for Python environments and lockfiles
- Node.js 22 or newer
- pnpm 10.12.1 through Corepack
- FFmpeg for native capture development; included in Docker

Do not install Python packages globally. Prefer Docker. If native development is necessary, `uv` must keep dependencies in the repository-local `.venv`.

## Docker-first setup

Run Ollama on the host and pull a vision-capable model:

```bash
ollama pull qwen3-vl:4b
cp .env.example .env
docker compose up --build
```

Open <http://127.0.0.1:8000>. The Compose configuration maps `host.docker.internal` on Linux and macOS-compatible Docker environments so the container can reach the host Ollama service.

Subject limits default to `MAX_INFANTS=1` and `MAX_ADULTS=4`. Both values accept integers from 1 through 64. They are injected into the shared prompt and Ollama JSON Schema; Gemini's compact transport profile omits `maxItems`, so the prompt plus local post-response validation enforce the same configured limits.

Every environment-backed `Settings` default is read when a `Settings()` instance is constructed, not when `babymonitorvl.config` is imported. Preserve that lifecycle for new settings and add a post-import monkeypatch regression test for every new environment variable. Constructing settings rejects non-positive `HISTORY_MAX_BYTES`, and `MODEL_TIMEOUT_SECONDS` must be both finite and greater than zero; configuration errors should fail at startup rather than produce an empty cache or invalid timeout behavior later.

`RTSP_STALL_TIMEOUT_SECONDS` defaults to 30 seconds and must be a finite positive number. It configures FFmpeg socket I/O timeout and is the baseline for the complete-JPEG watchdog. The effective frame watchdog is `max(RTSP_STALL_TIMEOUT_SECONDS, 3 / fps)`, preventing false reconnects at low sampling rates. A reconnect test must prove watchdog expiry terminates the child process before the backoff state is published.

The production image builds the exact FFmpeg release pinned by `FFMPEG_VERSION` and `FFMPEG_SHA256` in `Dockerfile` from the official `ffmpeg.org` source archive. Do not replace it with an unversioned distribution package. The image build must exercise an RTSP open attempt using the RTSP-native `timeout`/transport options and execute the actual `fps` + MJPEG `image2pipe` processing path without resizing. Do not use the generic `rw_timeout` for RTSP: FFmpeg 8.1.2 lists it in global protocol help but rejects it when the RTSP demuxer opens the input. An argument-list unit test or help-text grep alone does not prove that the runtime binary accepts an option in the target demuxer context.

For Gemini, either set `GEMINI_API_KEY` in the untracked `.env` or use the page's Gemini Key dialog. A dialog value is validated and kept only in backend process memory; it is not browser-persisted and disappears on restart. Because the MVP has no authentication, configure credentials only over the default loopback binding or trusted HTTPS. Selected frames leave the machine when Gemini is active.

## Native development

```bash
uv sync --frozen
corepack enable
pnpm --dir frontend install --frozen-lockfile
```

Backend:

```bash
uv run --frozen uvicorn babymonitorvl.main:app --reload --host 127.0.0.1 --port 8000
```

Frontend in another terminal:

```bash
pnpm --dir frontend dev
```

The Vite server runs at <http://127.0.0.1:5173> and proxies API/WebSocket requests to FastAPI.

## Isolated test commands

To avoid changing the host Python environment, run the offline suite against the source in a disposable container:

```bash
docker build -t babymonitorvl:dev .
docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/babymonitor-test-venv \
  -v "$PWD:/workspace:ro" \
  -w /workspace \
  babymonitorvl:dev \
  uv run --with pytest --with pytest-asyncio pytest -q -p no:cacheprovider
```

Frontend lockfile, type, and production-build validation occurs in the normal Docker build:

```bash
docker build --pull -t babymonitorvl:verify .
```

Native equivalents are:

```bash
uv run --frozen pytest -q -p no:cacheprovider
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend typecheck
pnpm --dir frontend build
```

`tests/test_contract_sync.py` directly compares backend enum values and public model fields with the committed TypeScript unions/interfaces. Frontend labels are typed as a complete `Record` over every displayed enum, so an added value must receive a deliberate UI label before `tsc` succeeds. Do not replace these checks with source-text presence tests or a snapshot that can pass while the frontend contract remains stale.

## Dependency changes

Python:

```bash
uv add package-name
uv lock
uv sync --frozen
```

Development-only Python dependency:

```bash
uv add --dev package-name
```

Frontend:

```bash
pnpm --dir frontend add package-name@exact-version
pnpm --dir frontend add -D package-name@exact-version
```

Never edit a dependency declaration without updating its lockfile. Avoid broad upgrades inside feature changes. Record significant dependency upgrades in `CHANGELOG.md` and validate both architectures if publishing multi-architecture images.

## Real-provider smoke checks

Real calls are opt-in because they require hardware, local models, private camera data, network access, or billing.

Ollama smoke criteria:

- `/api/providers` lists the intended local model.
- Session starts with a known safe/private RTSP source.
- At least one history item succeeds.
- Raw JSON matches the shared schema.
- Overlay coordinates visually correspond to the exact submitted frame.
- Token input/output counts are present when Ollama reports them.
- Interrupt or stall a disposable RTSP source and confirm status reaches `reconnecting` without waiting for FFmpeg EOF, then returns to `streaming` after recovery.

Gemini smoke criteria add explicit confirmation that sending the chosen frame to Google is acceptable. Test the exact selected model, inspect the actual serialized generation parameters, confirm that the portable baseline does not send an unsupported `thinking_level`, and verify the history schema profile is `google-ai-structured-output-compact-v1`. The returned JSON must pass `FrameAnalysis`, not merely complete the provider request. Any model-specific generation option or schema keyword must satisfy [Gemini/Gemma provider rules](GEMINI_PROVIDER.md). Never use a household camera for a release smoke test unless the owner has expressly authorized it.

## Debugging guidance

- Follow the [provider compatibility engineering method](PROVIDER_COMPATIBILITY.md) and identify the failing layer before changing prompts or request parameters.
- Inspect `/api/monitor/status` for queue pressure, dropped frames, latency, and redacted errors.
- Inspect the selected history record before changing prompts: raw response, schema, prompt version, coordinate metadata, and attempts identify whether the failure is generation, parsing, conversion, or rendering. The top-level prompt is the session baseline; each attempt's prompt is the exact text sent for that call and is authoritative when a retry correction was applied.
- Compare the annotated history image, not the live preview.
- Use `/api/prompt` for the canonical YXYX contract. Session history contains the actual model-specific prompt/schema.
- A JSON parse failure is not proof that a model lacks vision support; inspect the raw provider response and provider error separately.

## Files that must remain local

`.env`, `.venv`, `frontend/node_modules`, `frontend/dist`, caches, logs, camera frames, recordings, model weights, real raw provider dumps, and any file containing credentials or household imagery.
