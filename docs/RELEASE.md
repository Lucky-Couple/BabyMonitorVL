# Release checklist

## Release policy

Application versions use SemVer. Prompt and analysis schema versions are independent contracts and may change without matching the application version number, but every release note must list all three.

Do not publish directly from an uncommitted working directory. Build from a clean, reviewed commit and record the image digest.

For `0.x`, the supported product artifact is the Docker image containing FFmpeg, FastAPI, and the built React application. The Python wheel contains only the backend package and is not a standalone product artifact.

## Pre-release decisions

- Verify that the repository still contains the owner-approved MIT `LICENSE` and that package metadata declares `MIT`. Do not change the license or copyright holder as part of an unrelated release.
- Confirm whether the release is source-only, a container image, or both.
- Confirm the target image registry, architectures, and retention/signing policy.
- Confirm whether Gemini support and its privacy notice are included in the release scope.

## Version update

Keep these application versions identical:

- `pyproject.toml` project version
- `babymonitorvl/__init__.py` `__version__`
- `frontend/package.json` version

FastAPI reads the Python package version. Do not hardcode a fourth copy.

Also record current:

- `PROMPT_VERSION` from `babymonitorvl/prompt.py`
- `FrameAnalysis.schema_version` from `babymonitorvl/schemas.py`

Move applicable `CHANGELOG.md` Unreleased entries into a dated release section.

## Required quality gates

Run from the repository root:

```bash
uv lock --check
uv run --frozen pytest -q -p no:cacheprovider
pnpm --dir frontend install --frozen-lockfile
pnpm --dir frontend typecheck
pnpm --dir frontend build
docker compose config
docker build --pull -t babymonitorvl:release-candidate .
```

The Docker build is also the FFmpeg CLI compatibility gate: it verifies the pinned official release, exercises an actual RTSP open path with the configured RTSP-native timeout/transport options, and executes the capture pipeline's FPS filter, MJPEG encoder, and image-pipe muxer without resizing. Help-text presence alone is not a compatibility check. Do not waive this gate after changing either `Dockerfile` or `build_ffmpeg_command()`.

Run an isolated container test if the native environment is not trusted:

```bash
docker run --rm \
  -e UV_PROJECT_ENVIRONMENT=/tmp/babymonitor-test-venv \
  -v "$PWD:/workspace:ro" \
  -w /workspace \
  babymonitorvl:release-candidate \
  uv run --with pytest --with pytest-asyncio pytest -q -p no:cacheprovider
```

## Security and privacy gates

Review the intended tracked files and scan for secrets before commit:

```bash
git status --short
git diff --check
git ls-files
rg -n --hidden \
  -g '!.git/**' -g '!.venv/**' -g '!frontend/node_modules/**' \
  'GEMINI_API_KEY=.+|rtsp://[^[:space:]]+@|BEGIN (RSA|OPENSSH|EC) PRIVATE KEY'
```

Expected matches must be placeholders or redaction tests only. Verify that no JPEG, video, model file, `.env`, log, cache, build output, or local virtual environment is tracked.

Verify runtime behavior:

- server binds to `127.0.0.1` through Compose;
- status/history/errors never expose RTSP credentials or query values;
- Gemini key is absent from all responses;
- Gemini privacy text is visible;
- disclaimer remains visible;
- source restart clears history as documented.

## Smoke tests

Docker/UI smoke:

1. Start the release-candidate image on a disposable port.
2. Verify `/`, `/docs`, `/api/providers`, `/api/monitor/status`, `/api/prompt`, WebSocket connection, idle heartbeat, and disconnect cleanup.
3. Start a safe synthetic/local RTSP source with a mock or explicitly authorized model.
4. Stall the RTSP source without clean EOF and verify the complete-frame watchdog terminates FFmpeg, publishes reconnect status, and recovers through bounded backoff.
5. Confirm exact-frame overlay, separate live preview, history image/detail, JSON highlighting, token totals, and stop/restart.
6. Confirm slow inference increases dropped count rather than queue latency.

Real-provider contract smoke is opt-in. Record provider/model/version, prompt version, schema version, whether frames left the machine, latency, token fields, and result. Never commit captured frames or raw household data.

## Container publication

- Build from a clean commit.
- Prefer immutable SemVer and commit-SHA tags; do not publish only `latest`.
- Record base images, build timestamp, source commit, supported architectures, and resulting digest.
- Scan the image for known vulnerabilities and secrets using the organization-approved scanner.
- Sign/provenance-attest the image if the registry supports it.
- Re-run a minimal startup/API smoke test on the exact digest.

## Git release steps

Only after explicit authorization:

```bash
git status --short
git tag -s vX.Y.Z -m "BabyMonitorVL vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

Use an annotated tag if signing is unavailable and the owner accepts that tradeoff. Release notes must include safety limitations, provider/privacy differences, schema/prompt versions, upgrade notes, checks run, image digest, and known issues.

## Rollback

Retain the previous immutable image tag/digest and source tag. Rollback means replacing the running container with that exact digest. History is memory-only and cannot be migrated or recovered after a restart; state this before operational rollback.
