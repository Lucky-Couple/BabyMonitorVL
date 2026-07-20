from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from pathlib import Path


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    return int(raw) if raw is not None else default


def _env_float(name: str, default: float) -> float:
    raw = os.getenv(name)
    return float(raw) if raw is not None else default


def _env_path(name: str, default: Path) -> Path:
    raw = os.getenv(name)
    return Path(raw) if raw is not None else default


_DEFAULT_FRONTEND_DIST = Path(__file__).resolve().parent.parent / "frontend" / "dist"


@dataclass(frozen=True, slots=True)
class Settings:
    # Environment-backed defaults must be evaluated for each Settings instance. This keeps
    # tests, application startup, and any future explicit configuration rebuild consistent.
    ollama_base_url: str = field(
        default_factory=lambda: os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    )
    gemini_api_key: str | None = field(default_factory=lambda: os.getenv("GEMINI_API_KEY"))
    default_ollama_model: str = field(
        default_factory=lambda: os.getenv("DEFAULT_OLLAMA_MODEL", "qwen3-vl:4b")
    )
    default_gemini_model: str = field(
        default_factory=lambda: os.getenv("DEFAULT_GEMINI_MODEL", "gemini-3.5-flash")
    )
    model_timeout_seconds: float = field(
        default_factory=lambda: _env_float("MODEL_TIMEOUT_SECONDS", 60.0)
    )
    rtsp_stall_timeout_seconds: float = field(
        default_factory=lambda: _env_float("RTSP_STALL_TIMEOUT_SECONDS", 30.0)
    )
    history_max_bytes: int = field(
        default_factory=lambda: _env_int("HISTORY_MAX_BYTES", 1024 * 1024 * 1024)
    )
    max_infants: int = field(default_factory=lambda: _env_int("MAX_INFANTS", 1))
    max_adults: int = field(default_factory=lambda: _env_int("MAX_ADULTS", 4))
    ffmpeg_binary: str = field(default_factory=lambda: os.getenv("FFMPEG_BINARY", "ffmpeg"))
    frontend_dist: Path = field(
        default_factory=lambda: _env_path("FRONTEND_DIST", _DEFAULT_FRONTEND_DIST)
    )

    def __post_init__(self) -> None:
        if not math.isfinite(self.model_timeout_seconds) or self.model_timeout_seconds <= 0:
            raise ValueError("MODEL_TIMEOUT_SECONDS must be a finite number greater than 0")
        if not math.isfinite(self.rtsp_stall_timeout_seconds) or self.rtsp_stall_timeout_seconds <= 0:
            raise ValueError("RTSP_STALL_TIMEOUT_SECONDS must be a finite number greater than 0")
        if self.history_max_bytes <= 0:
            raise ValueError("HISTORY_MAX_BYTES must be greater than 0")
        for name, value in (("MAX_INFANTS", self.max_infants), ("MAX_ADULTS", self.max_adults)):
            if value < 1 or value > 64:
                raise ValueError(f"{name} must be between 1 and 64")
