import httpx
import pytest

from babymonitorvl.config import Settings
from babymonitorvl.main import create_app


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
