import httpx
import pytest

from babymonitorvl.config import Settings
from babymonitorvl.main import create_app
from babymonitorvl.providers.base import ProviderHealth
from babymonitorvl.providers.gemini import GeminiBackend
from babymonitorvl.schemas import ProviderName


@pytest.mark.asyncio
async def test_prompt_and_stopped_status_are_available_without_models(tmp_path) -> None:
    app = create_app(Settings(frontend_dist=tmp_path, gemini_api_key=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        prompt = await client.get("/api/prompt")
        status = await client.get("/api/monitor/status")
    assert prompt.status_code == 200
    assert prompt.json()["version"] == "baby-monitor-single-frame-v4-cat-detection"
    assert "JSON_SCHEMA" in prompt.json()["prompt"]
    assert status.status_code == 200
    assert status.json()["state"] == "stopped"
    assert status.json()["history"]["items"] == 0


@pytest.mark.asyncio
async def test_validation_response_does_not_echo_rtsp_credentials(tmp_path) -> None:
    app = create_app(Settings(frontend_dist=tmp_path, gemini_api_key=None))
    transport = httpx.ASGITransport(app=app)
    secret = "rtsp-user-secret"
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/monitor/start",
            json={"rtsp_url": f"http://alice:{secret}@camera/stream", "fps": 1},
        )
    assert response.status_code == 422
    assert secret not in response.text


@pytest.mark.asyncio
async def test_web_gemini_key_is_validated_and_never_returned(tmp_path, monkeypatch) -> None:
    secret = "test-gemini-key-that-must-not-be-returned"

    async def healthy(self) -> ProviderHealth:
        assert self.api_key == secret
        return ProviderHealth(True, "Gemini API reachable", ["gemini-test-vision"])

    monkeypatch.setattr(GeminiBackend, "healthcheck", healthy)
    app = create_app(Settings(frontend_dist=tmp_path, gemini_api_key=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/providers/gemini/key", json={"api_key": secret})

    assert response.status_code == 200
    assert response.json()["key_configured"] is True
    assert response.json()["key_source"] == "web"
    assert response.json()["models"] == ["gemini-test-vision"]
    assert secret not in response.text
    configured = app.state.providers[ProviderName.GEMINI]
    assert isinstance(configured, GeminiBackend)
    assert configured.api_key == secret


@pytest.mark.asyncio
async def test_invalid_web_gemini_key_preserves_existing_configuration(tmp_path, monkeypatch) -> None:
    original_secret = "existing-environment-key"
    rejected_secret = "rejected-web-key-that-must-not-be-returned"

    async def healthcheck(self) -> ProviderHealth:
        if self.api_key == rejected_secret:
            return ProviderHealth(False, f"Gemini unavailable for {rejected_secret}: ClientError")
        return ProviderHealth(True, "Gemini API reachable", ["gemini-test-vision"])

    monkeypatch.setattr(GeminiBackend, "healthcheck", healthcheck)
    app = create_app(Settings(frontend_dist=tmp_path, gemini_api_key=original_secret))
    original = app.state.providers[ProviderName.GEMINI]
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/providers/gemini/key", json={"api_key": rejected_secret})

    assert response.status_code == 400
    assert rejected_secret not in response.text
    assert "***" in response.text
    assert app.state.providers[ProviderName.GEMINI] is original
    assert original.api_key == original_secret


@pytest.mark.asyncio
async def test_web_gemini_key_can_reset_to_startup_configuration(tmp_path, monkeypatch) -> None:
    runtime_secret = "temporary-web-key"

    async def healthcheck(self) -> ProviderHealth:
        if self.api_key:
            return ProviderHealth(True, "Gemini API reachable", ["gemini-test-vision"])
        return ProviderHealth(False, "GEMINI_API_KEY is not configured")

    monkeypatch.setattr(GeminiBackend, "healthcheck", healthcheck)
    app = create_app(Settings(frontend_dist=tmp_path, gemini_api_key=None))
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        configured = await client.put("/api/providers/gemini/key", json={"api_key": runtime_secret})
        reset = await client.delete("/api/providers/gemini/key")

    assert configured.status_code == 200
    assert reset.status_code == 200
    assert reset.json()["key_configured"] is False
    assert reset.json()["key_source"] == "none"
    assert runtime_secret not in reset.text
    assert app.state.providers[ProviderName.GEMINI].api_key is None


@pytest.mark.asyncio
async def test_web_gemini_key_cannot_change_during_active_session(tmp_path, monkeypatch) -> None:
    secret = "runtime-key-blocked-while-active"
    healthcheck_called = False

    async def healthy(self) -> ProviderHealth:
        nonlocal healthcheck_called
        healthcheck_called = True
        return ProviderHealth(True, "Gemini API reachable", ["gemini-test-vision"])

    monkeypatch.setattr(GeminiBackend, "healthcheck", healthy)
    app = create_app(Settings(frontend_dist=tmp_path, gemini_api_key=None))
    original = app.state.providers[ProviderName.GEMINI]
    app.state.monitor._session_id = "active-test-session"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.put("/api/providers/gemini/key", json={"api_key": secret})

    assert response.status_code == 409
    assert secret not in response.text
    assert app.state.providers[ProviderName.GEMINI] is original
    assert healthcheck_called is False


@pytest.mark.asyncio
async def test_monitor_start_redacts_runtime_provider_key_from_health_error(tmp_path, monkeypatch) -> None:
    secret = "runtime-key-in-provider-health-error"

    async def unhealthy(self) -> ProviderHealth:
        return ProviderHealth(False, f"provider rejected credential {secret}")

    monkeypatch.setattr(GeminiBackend, "healthcheck", unhealthy)
    app = create_app(
        Settings(
            frontend_dist=tmp_path,
            gemini_api_key=secret,
            ffmpeg_binary="true",
        )
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/monitor/start",
            json={"rtsp_url": "rtsp://camera.invalid/stream", "provider": "gemini"},
        )

    assert response.status_code == 400
    assert secret not in response.text
    assert "***" in response.text
