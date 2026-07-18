from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw is not None else default


@dataclass(frozen=True, slots=True)
class Settings:
    ollama_base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    gemini_api_key: str | None = os.getenv("GEMINI_API_KEY")
    default_ollama_model: str = os.getenv("DEFAULT_OLLAMA_MODEL", "qwen3-vl:4b")
    default_gemini_model: str = os.getenv("DEFAULT_GEMINI_MODEL", "gemini-3.5-flash")
    model_timeout_seconds: float = _env_float("MODEL_TIMEOUT_SECONDS", 60.0)
    history_max_bytes: int = _env_int("HISTORY_MAX_BYTES", 1024 * 1024 * 1024)
    ffmpeg_binary: str = os.getenv("FFMPEG_BINARY", "ffmpeg")
    frontend_dist: Path = Path(
        os.getenv(
            "FRONTEND_DIST",
            str(Path(__file__).resolve().parent.parent / "frontend" / "dist"),
        )
    )
