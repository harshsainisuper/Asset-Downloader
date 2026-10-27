"""FastAPI application factory.

Run with:  uvicorn app.main:app --host 127.0.0.1 --port 8000   (single worker: jobs are in memory)
"""

from __future__ import annotations

import logging
import shutil
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .api.routes import router
from .config import Settings, get_settings
from .errors import AppError
from .jobs.manager import JobManager
from .media.ffmpeg import ffmpeg_version
from .media.pot_provider import BgutilSupervisor
from .media.provider import MediaProvider
from .media.ytdlp_provider import YtDlpProvider
from .security import BodySizeLimitMiddleware, RateLimiter
from .transcripts.stt import SttProvider, get_stt_provider

log = logging.getLogger("app")

_HTML_CSP = (
    "default-src 'self'; img-src 'self' data: https://i.ytimg.com https://i9.ytimg.com; "
    "media-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
)


def _error(code: str, message: str, status: int) -> JSONResponse:
    return JSONResponse({"error": {"code": code, "message": message}}, status_code=status)


def create_app(
    settings: Settings | None = None,
    provider: MediaProvider | None = None,
    stt: SttProvider | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.provider = provider or YtDlpProvider(settings)
        stt_provider = stt if stt is not None else get_stt_provider(settings)
        app.state.manager = JobManager(settings, app.state.provider, stt_provider)
        app.state.ffmpeg_version = ffmpeg_version()
        if not settings.ffmpeg_binary or not settings.ffprobe_binary:
            log.error("FFmpeg/FFprobe not found. Video merging and audio conversion will fail. See README.")
        diag = app.state.provider.diagnostics()
        if diag.get("js_runtime_available") is False:
            log.warning("No JavaScript runtime (deno/node) found for yt-dlp; YouTube downloads may fail. See README.")
        if diag.get("cookies_status") in ("missing", "unreadable"):
            log.warning("YouTube cookies file is %s; continuing without cookies.", diag["cookies_status"])
        app.state.pot = await _start_pot_provider(settings)
        await app.state.manager.start()
        log.info("YT Media Toolkit started (provider=%s, stt=%s, pot=%s, cookies=%s, proxy=%s, temp=%s)",
                 diag.get("name"), stt_provider.label if stt_provider else "disabled", settings.pot_provider,
                 bool(diag.get("cookies_configured")), bool(diag.get("proxy_configured")), settings.temp_dir)
        try:
            yield
        finally:
            await app.state.manager.stop()
            if app.state.pot:
                await app.state.pot.stop()
            shutdown = getattr(app.state.provider, "shutdown", None)
            if callable(shutdown):
                shutdown()

    app = FastAPI(
        title="YT Media Toolkit",
        lifespan=lifespan,
        docs_url="/api/docs" if settings.app_env == "development" else None,
        redoc_url=None,
        openapi_url="/api/openapi.json" if settings.app_env == "development" else None,
    )
    app.state.settings = settings
    app.state.rate_limiter = RateLimiter()

    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_body_bytes)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_origin_regex=settings.cors_origin_regex or None,
        allow_methods=["GET", "POST", "DELETE"],
        allow_headers=["Content-Type", "X-Job-Token"],
    )

    @app.exception_handler(AppError)
    async def _app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.code, exc.message, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = exc.errors()
        message = "Invalid request."
        if errors:
            first = errors[0]
            loc = ".".join(str(p) for p in first.get("loc", ()) if p not in ("body",))
            msg = str(first.get("msg", "")).removeprefix("Value error, ")
            message = f"{loc}: {msg}" if loc and "Select at least one" not in msg else msg or message
        return _error("validation_error", message, 422)

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        message = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return _error("http_error", message, exc.status_code)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error on %s %s", request.method, request.url.path)
        return _error("internal_error", "An unexpected server error occurred.", 500)

    app.include_router(router)
    _mount_frontend(app, settings)
    return app


async def _start_pot_provider(settings: Settings) -> BgutilSupervisor | None:
    """Start the bundled bgutil PO-token server when configured (Docker image)."""
    if settings.pot_provider != "bgutil" or not settings.bgutil_server_script:
        return None
    node = settings.node_path or shutil.which("node")
    if not node or not Path(settings.bgutil_server_script).is_file():
        log.error("POT_PROVIDER=bgutil but node or BGUTIL_SERVER_SCRIPT (%s) is missing; PO tokens disabled.",
                  settings.bgutil_server_script)
        return None
    supervisor = BgutilSupervisor.for_script(node, settings.bgutil_server_script, settings.bgutil_base_url)
    await supervisor.start()
    return supervisor


def _mount_frontend(app: FastAPI, settings: Settings) -> None:
    dist = settings.frontend_dist.resolve()
    index = dist / "index.html"
    if not settings.serve_frontend or not index.is_file():
        return

    @app.get("/{path:path}", include_in_schema=False)
    async def spa(path: str):
        if path.startswith("api/"):
            return _error("not_found", "Not found.", 404)
        candidate = (dist / path).resolve() if path else index
        if not candidate.is_relative_to(dist) or not candidate.is_file():
            candidate = index
        headers = {"X-Content-Type-Options": "nosniff"}
        if candidate == index:
            headers |= {"Content-Security-Policy": _HTML_CSP, "Cache-Control": "no-cache",
                        "Referrer-Policy": "no-referrer", "X-Frame-Options": "DENY"}
        elif candidate.is_relative_to(dist / "assets"):
            # Vite emits content-hashed asset names, so they can be cached forever.
            headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return FileResponse(Path(candidate), headers=headers)


app = create_app()
