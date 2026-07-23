import pytest

from babymonitorvl.config import Settings


def test_environment_backed_defaults_are_read_when_settings_is_instantiated(
    monkeypatch, tmp_path
) -> None:
    first_dist = tmp_path / "first-dist"
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://first-ollama.test:11434")
    monkeypatch.setenv("GEMINI_API_KEY", "first-test-key")
    monkeypatch.setenv("DEFAULT_OLLAMA_MODEL", "first-ollama-model")
    monkeypatch.setenv("DEFAULT_GEMINI_MODEL", "first-gemini-model")
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("RTSP_STALL_TIMEOUT_SECONDS", "18.5")
    monkeypatch.setenv("HISTORY_MAX_BYTES", "4096")
    monkeypatch.setenv("MAX_INFANTS", "2")
    monkeypatch.setenv("MAX_ADULTS", "6")
    monkeypatch.setenv("FFMPEG_BINARY", "first-ffmpeg")
    monkeypatch.setenv("FRONTEND_DIST", str(first_dist))

    first = Settings()
    assert first.ollama_base_url == "http://first-ollama.test:11434"
    assert first.gemini_api_key == "first-test-key"
    assert first.default_ollama_model == "first-ollama-model"
    assert first.default_gemini_model == "first-gemini-model"
    assert first.model_timeout_seconds == 12.5
    assert first.rtsp_stall_timeout_seconds == 18.5
    assert first.history_max_bytes == 4096
    assert first.max_infants == 2
    assert first.max_adults == 6
    assert first.ffmpeg_binary == "first-ffmpeg"
    assert first.frontend_dist == first_dist

    second_dist = tmp_path / "second-dist"
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://second-ollama.test:11434")
    monkeypatch.setenv("FRONTEND_DIST", str(second_dist))

    second = Settings()
    assert second.ollama_base_url == "http://second-ollama.test:11434"
    assert second.frontend_dist == second_dist


def test_subject_limits_default_and_environment_override(monkeypatch, tmp_path) -> None:
    monkeypatch.delenv("MAX_INFANTS", raising=False)
    monkeypatch.delenv("MAX_ADULTS", raising=False)
    defaults = Settings(frontend_dist=tmp_path)
    assert defaults.max_infants == 1
    assert defaults.max_adults == 4

    monkeypatch.setenv("MAX_INFANTS", "3")
    monkeypatch.setenv("MAX_ADULTS", "7")
    configured = Settings(frontend_dist=tmp_path)
    assert configured.max_infants == 3
    assert configured.max_adults == 7


def test_stability_defaults_and_environment_override(monkeypatch, tmp_path) -> None:
    defaults = Settings(frontend_dist=tmp_path)
    assert defaults.stability_window_size == 5
    assert defaults.stability_confirmation_frames == 3
    assert defaults.stability_clear_frames == 3
    assert defaults.stability_box_iou_threshold == 0.2
    assert defaults.stability_box_ema_alpha == 0.35
    assert defaults.stability_timeline_max_points == 500

    monkeypatch.setenv("STABILITY_WINDOW_SIZE", "9")
    monkeypatch.setenv("STABILITY_CONFIRMATION_FRAMES", "4")
    monkeypatch.setenv("STABILITY_CLEAR_FRAMES", "5")
    monkeypatch.setenv("STABILITY_BOX_IOU_THRESHOLD", "0.25")
    monkeypatch.setenv("STABILITY_BOX_EMA_ALPHA", "0.5")
    monkeypatch.setenv("STABILITY_TIMELINE_MAX_POINTS", "250")
    configured = Settings(frontend_dist=tmp_path)
    assert configured.stability_window_size == 9
    assert configured.stability_confirmation_frames == 4
    assert configured.stability_clear_frames == 5
    assert configured.stability_box_iou_threshold == 0.25
    assert configured.stability_box_ema_alpha == 0.5
    assert configured.stability_timeline_max_points == 250


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("STABILITY_WINDOW_SIZE", "2", "STABILITY_WINDOW_SIZE"),
        ("STABILITY_CONFIRMATION_FRAMES", "1", "STABILITY_CONFIRMATION_FRAMES"),
        ("STABILITY_CLEAR_FRAMES", "0", "STABILITY_CLEAR_FRAMES"),
        ("STABILITY_BOX_IOU_THRESHOLD", "0", "STABILITY_BOX_IOU_THRESHOLD"),
        ("STABILITY_BOX_EMA_ALPHA", "1.1", "STABILITY_BOX_EMA_ALPHA"),
        ("STABILITY_TIMELINE_MAX_POINTS", "0", "STABILITY_TIMELINE_MAX_POINTS"),
    ],
)
def test_stability_settings_reject_invalid_values(
    monkeypatch, tmp_path, name: str, value: str, message: str
) -> None:
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match=message):
        Settings(frontend_dist=tmp_path)


@pytest.mark.parametrize(("name", "value"), [("MAX_INFANTS", "0"), ("MAX_ADULTS", "65")])
def test_subject_limits_reject_unsafe_values(monkeypatch, tmp_path, name: str, value: str) -> None:
    monkeypatch.delenv("MAX_INFANTS", raising=False)
    monkeypatch.delenv("MAX_ADULTS", raising=False)
    monkeypatch.setenv(name, value)
    with pytest.raises(ValueError, match="must be between 1 and 64"):
        Settings(frontend_dist=tmp_path)


@pytest.mark.parametrize("value", ["0", "-1"])
def test_history_budget_rejects_non_positive_values(monkeypatch, tmp_path, value: str) -> None:
    monkeypatch.setenv("HISTORY_MAX_BYTES", value)
    with pytest.raises(ValueError, match="HISTORY_MAX_BYTES must be greater than 0"):
        Settings(frontend_dist=tmp_path)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_model_timeout_rejects_non_positive_or_non_finite_values(
    monkeypatch, tmp_path, value: str
) -> None:
    monkeypatch.setenv("MODEL_TIMEOUT_SECONDS", value)
    with pytest.raises(
        ValueError, match="MODEL_TIMEOUT_SECONDS must be a finite number greater than 0"
    ):
        Settings(frontend_dist=tmp_path)


@pytest.mark.parametrize("value", ["0", "-1", "nan", "inf"])
def test_rtsp_stall_timeout_rejects_non_positive_or_non_finite_values(
    monkeypatch, tmp_path, value: str
) -> None:
    monkeypatch.setenv("RTSP_STALL_TIMEOUT_SECONDS", value)
    with pytest.raises(
        ValueError, match="RTSP_STALL_TIMEOUT_SECONDS must be a finite number greater than 0"
    ):
        Settings(frontend_dist=tmp_path)
