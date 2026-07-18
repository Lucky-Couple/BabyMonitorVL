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

For Gemini, set `GEMINI_API_KEY` only in the untracked `.env`. Selected frames leave the machine when Gemini is active.

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

Gemini smoke criteria add explicit confirmation that sending the chosen frame to Google is acceptable. Never use a household camera for a release smoke test unless the owner has expressly authorized it.

## Debugging guidance

- Inspect `/api/monitor/status` for queue pressure, dropped frames, latency, and redacted errors.
- Inspect the selected history record before changing prompts: raw response, schema, prompt version, coordinate metadata, and attempts identify whether the failure is generation, parsing, conversion, or rendering.
- Compare the annotated history image, not the live preview.
- Use `/api/prompt` for the canonical YXYX contract. Session history contains the actual model-specific prompt/schema.
- A JSON parse failure is not proof that a model lacks vision support; inspect the raw provider response and provider error separately.

## Files that must remain local

`.env`, `.venv`, `frontend/node_modules`, `frontend/dist`, caches, logs, camera frames, recordings, model weights, real raw provider dumps, and any file containing credentials or household imagery.
