ARG FFMPEG_VERSION=8.1.2
ARG FFMPEG_SHA256=464beb5e7bf0c311e68b45ae2f04e9cc2af88851abb4082231742a74d97b524c

FROM node:22-alpine AS frontend-build
WORKDIR /build/frontend
RUN corepack enable
COPY frontend/package.json frontend/pnpm-lock.yaml* ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm build

FROM debian:trixie-slim AS ffmpeg-build
ARG FFMPEG_VERSION
ARG FFMPEG_SHA256
WORKDIR /build
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        build-essential ca-certificates curl nasm pkg-config xz-utils \
    && rm -rf /var/lib/apt/lists/*
RUN curl --fail --location --show-error \
        --output ffmpeg.tar.xz \
        "https://ffmpeg.org/releases/ffmpeg-${FFMPEG_VERSION}.tar.xz" \
    && echo "${FFMPEG_SHA256}  ffmpeg.tar.xz" | sha256sum --check --strict \
    && tar --extract --file ffmpeg.tar.xz \
    && mv "ffmpeg-${FFMPEG_VERSION}" source
WORKDIR /build/source
RUN ./configure \
        --prefix=/opt/ffmpeg \
        --disable-debug \
        --disable-doc \
        --disable-ffplay \
        --disable-ffprobe \
        --enable-small \
    && make -j2 \
    && make install
# Fail the image build if the pinned binary and the exact capture command contract diverge.
RUN /opt/ffmpeg/bin/ffmpeg -hide_banner -version 2>&1 | grep -F "ffmpeg version ${FFMPEG_VERSION}" \
    && /opt/ffmpeg/bin/ffmpeg -hide_banner -h demuxer=rtsp 2>&1 | grep -F "rtsp_transport" \
    && /opt/ffmpeg/bin/ffmpeg -hide_banner -h demuxer=rtsp 2>&1 | grep -F "timeout" \
    && rtsp_probe="$(/opt/ffmpeg/bin/ffmpeg -hide_banner -loglevel error \
        -timeout 100000 -rtsp_transport tcp \
        -i rtsp://127.0.0.1:9/compatibility-check -f null - 2>&1 || true)" \
    && ! printf '%s' "${rtsp_probe}" | grep -F "Option timeout not found" \
    && /opt/ffmpeg/bin/ffmpeg -hide_banner -loglevel error \
        -f lavfi -i "color=size=640x360:rate=2:duration=1" \
        -vf "fps=1" -frames:v 1 \
        -f image2pipe -vcodec mjpeg /tmp/frame.jpg \
    && test -s /tmp/frame.jpg

FROM ghcr.io/astral-sh/uv:0.8.4 AS uv

FROM python:3.12-slim AS runtime
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    FRONTEND_DIST=/app/frontend/dist \
    PATH=/app/.venv/bin:$PATH
WORKDIR /app
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ffmpeg-build /opt/ffmpeg/bin/ffmpeg /usr/local/bin/ffmpeg
COPY --from=uv /uv /uvx /bin/
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY babymonitorvl/ ./babymonitorvl/
RUN uv sync --frozen --no-dev
COPY --from=frontend-build /build/frontend/dist ./frontend/dist
EXPOSE 8000
CMD ["uvicorn", "babymonitorvl.main:app", "--host", "0.0.0.0", "--port", "8000", "--ws-ping-interval", "20", "--ws-ping-timeout", "20"]
