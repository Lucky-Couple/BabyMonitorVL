from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles

from . import __version__
from .config import Settings
from .events import EventHub
from .history import HistoryStore
from .monitor import MonitorService, redact_sensitive_text
from .prompt import PROMPT_VERSION, build_prompt, output_schema
from .providers import GeminiBackend, OllamaBackend
from .providers.base import ProviderHealth
from .schemas import GeminiKeyRequest, MonitorStartRequest, ProviderName


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    events = EventHub()
    history = HistoryStore(settings.history_max_bytes)
    providers = {
        ProviderName.OLLAMA: OllamaBackend(settings.ollama_base_url, settings.model_timeout_seconds),
        ProviderName.GEMINI: GeminiBackend(
            settings.gemini_api_key,
            settings.model_timeout_seconds,
            key_source="environment" if settings.gemini_api_key else "none",
        ),
    }
    monitor = MonitorService(settings, history, events, providers)

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        yield
        await monitor.close()

    app = FastAPI(title="BabyMonitorVL", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.events = events
    app.state.history = history
    app.state.monitor = monitor
    app.state.providers = providers
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def sanitized_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = []
        for item in exc.errors():
            sanitized = {key: value for key, value in item.items() if key not in {"input", "ctx"}}
            errors.append(sanitized)
        return JSONResponse(status_code=422, content={"detail": errors})

    def provider_info(name: ProviderName, health: ProviderHealth) -> dict[str, Any]:
        provider = providers[name]
        default_model = (
            settings.default_ollama_model if name is ProviderName.OLLAMA else settings.default_gemini_model
        )
        models = health.models or [default_model]
        result = {
            "available": health.available,
            "detail": redact_sensitive_text(health.detail, provider.sensitive_values()),
            "models": models,
            "default_model": default_model,
            "version": health.version,
            "cloud": name is ProviderName.GEMINI,
            "models_dynamic": name is ProviderName.OLLAMA or health.available,
        }
        if name is ProviderName.GEMINI:
            gemini = providers[ProviderName.GEMINI]
            result["key_configured"] = bool(getattr(gemini, "api_key", None))
            result["key_source"] = getattr(gemini, "key_source", "none")
        return result

    @app.get("/api/providers")
    async def get_providers() -> dict[str, Any]:
        provider_items = list(providers.items())
        healths = await asyncio.gather(*(provider.healthcheck() for _, provider in provider_items))
        return {
            name.value: provider_info(name, health)
            for (name, _), health in zip(provider_items, healths, strict=True)
        }

    @app.put("/api/providers/gemini/key")
    async def configure_gemini_key(payload: GeminiKeyRequest) -> dict[str, Any]:
        try:
            await monitor.require_idle()
        except RuntimeError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        candidate = GeminiBackend(
            payload.api_key.get_secret_value(),
            settings.model_timeout_seconds,
            key_source="web",
        )
        health = await candidate.healthcheck()
        if not health.available:
            await asyncio.gather(candidate.close(), return_exceptions=True)
            detail = redact_sensitive_text(health.detail, candidate.sensitive_values())
            raise HTTPException(status_code=400, detail=detail)
        try:
            previous = await monitor.replace_provider(ProviderName.GEMINI, candidate)
        except RuntimeError as exc:
            await asyncio.gather(candidate.close(), return_exceptions=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await asyncio.gather(previous.close(), return_exceptions=True)
        return provider_info(ProviderName.GEMINI, health)

    @app.delete("/api/providers/gemini/key")
    async def reset_gemini_key() -> dict[str, Any]:
        candidate = GeminiBackend(
            settings.gemini_api_key,
            settings.model_timeout_seconds,
            key_source="environment" if settings.gemini_api_key else "none",
        )
        try:
            previous = await monitor.replace_provider(ProviderName.GEMINI, candidate)
        except RuntimeError as exc:
            await asyncio.gather(candidate.close(), return_exceptions=True)
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await asyncio.gather(previous.close(), return_exceptions=True)
        health = await candidate.healthcheck()
        return provider_info(ProviderName.GEMINI, health)

    @app.post("/api/monitor/start", status_code=201)
    async def start_monitor(payload: MonitorStartRequest) -> dict[str, Any]:
        try:
            return await monitor.start(payload)
        except RuntimeError as exc:
            message = str(exc)
            status_code = 409 if "already active" in message else 400
            raise HTTPException(status_code=status_code, detail=message) from exc

    @app.post("/api/monitor/stop")
    async def stop_monitor() -> dict[str, bool]:
        await monitor.stop()
        return {"stopped": True}

    @app.get("/api/monitor/status")
    async def monitor_status() -> dict[str, Any]:
        return await monitor.status()

    @app.get("/api/live/image")
    async def live_image() -> Response:
        frame = await monitor.latest_image()
        if frame is None:
            raise HTTPException(status_code=404, detail="no captured frame")
        return Response(
            content=frame.image_bytes,
            media_type="image/jpeg",
            headers={"Cache-Control": "no-store, no-cache, must-revalidate"},
        )

    @app.get("/api/history")
    async def list_history(
        limit: int = Query(50, ge=1, le=200),
        cursor: str | None = None,
        provider: ProviderName | None = None,
        model: str | None = None,
        risk: str | None = None,
        errors_only: bool = False,
    ) -> dict[str, Any]:
        items, next_cursor = await history.list(
            limit=limit,
            cursor=cursor,
            provider=provider,
            model=model,
            risk=risk,
            errors_only=errors_only,
        )
        return {"items": [item.model_dump(mode="json") for item in items], "next_cursor": next_cursor}

    @app.get("/api/history/{record_id}")
    async def history_detail(record_id: str) -> dict[str, Any]:
        record = await history.get(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="history item not found")
        return record.as_item().model_dump(mode="json")

    @app.get("/api/history/{record_id}/image")
    async def history_image(record_id: str) -> Response:
        record = await history.get(record_id)
        if record is None:
            raise HTTPException(status_code=404, detail="history item not found")
        return Response(content=record.image_bytes, media_type="image/jpeg", headers={"Cache-Control": "no-store"})

    @app.get("/api/prompt")
    async def prompt_contract() -> dict[str, Any]:
        schema = output_schema()
        return {"version": PROMPT_VERSION, "prompt": build_prompt(schema), "output_schema": schema}

    @app.websocket("/api/events")
    async def event_socket(websocket: WebSocket) -> None:
        await websocket.accept()
        queue = await events.subscribe()
        try:
            await websocket.send_json({"type": "status", "data": await monitor.status()})
            while True:
                event = await queue.get()
                await websocket.send_json(event)
        except WebSocketDisconnect:
            pass
        finally:
            await events.unsubscribe(queue)

    frontend_dist = settings.frontend_dist
    assets_dir = frontend_dist / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=assets_dir), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str, request: Request) -> Response:
        if path.startswith("api/"):
            raise HTTPException(status_code=404, detail="not found")
        index = frontend_dist / "index.html"
        requested = (frontend_dist / path).resolve()
        if path and frontend_dist.resolve() in requested.parents and requested.is_file():
            return FileResponse(requested)
        if index.is_file():
            return FileResponse(index)
        return HTMLResponse(
            "<h1>BabyMonitorVL API is running</h1>"
            "<p>Frontend build not found. Run <code>pnpm --dir frontend build</code>.</p>"
            f"<p><a href='{request.base_url}docs'>Open API docs</a></p>",
            status_code=200,
        )

    return app


app = create_app()
