from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import FileResponse

from ..errors import AppError
from ..jobs.manager import JobManager
from ..media.pot_provider import ping as pot_ping
from ..media.provider import MediaProvider
from ..schemas import CancelRequest, InfoRequest, JobCreateRequest, ZipRequest
from ..security import RateLimiter, client_ip, verify_download
from ..url_validation import parse_media_url
from .serializers import file_payload, info_payload, job_payload

log = logging.getLogger(__name__)
router = APIRouter(prefix="/api")


def _manager(request: Request) -> JobManager:
    return request.app.state.manager


def _provider(request: Request) -> MediaProvider:
    return request.app.state.provider


def _rate_limit(request: Request, bucket: str, limit: int) -> str:
    settings = request.app.state.settings
    limiter: RateLimiter = request.app.state.rate_limiter
    client = client_ip(request, settings.trust_proxy_headers)
    if not limiter.allow(bucket, client, limit):
        raise AppError("rate_limited", "Too many requests. Please wait a minute and try again.", 429)
    return client


@router.get("/health")
async def health(request: Request) -> dict:
    settings = request.app.state.settings
    return {
        "status": "ok",
        "ffmpeg": settings.ffmpeg_binary is not None,
        "ffprobe": settings.ffprobe_binary is not None,
    }


@router.get("/config")
async def config(request: Request) -> dict:
    settings = request.app.state.settings
    manager = _manager(request)
    provider = _provider(request).diagnostics()
    if settings.pot_provider == "bgutil":
        pong = await pot_ping(settings.bgutil_base_url)
        provider["pot_server"] = {"reachable": pong is not None, "version": (pong or {}).get("version")}
    return {
        "ffmpeg_version": request.app.state.ffmpeg_version,
        "provider": provider,
        "stt": {"available": manager.stt is not None, "label": manager.stt.label if manager.stt else None,
                "provider": settings.stt_provider},
        "limits": {
            "retention_minutes": settings.retention_minutes,
            "max_duration_seconds": settings.max_duration_seconds,
            "max_filesize_mb": settings.max_filesize_mb,
            "max_concurrent_jobs": settings.max_concurrent_jobs,
        },
    }


async def _fetch_info(request: Request, url: str):
    ref = parse_media_url(url)
    info = await _provider(request).fetch_info(ref)
    settings = request.app.state.settings
    if info.duration and info.duration > settings.max_duration_seconds:
        raise AppError("too_long", f"Videos longer than {settings.max_duration_seconds // 60} minutes are not "
                                   "supported by this server.", 422)
    return info


@router.post("/info")
async def video_info(body: InfoRequest, request: Request) -> dict:
    _rate_limit(request, "info", request.app.state.settings.info_rate_limit_per_minute)
    info = await _fetch_info(request, body.url)
    return info_payload(info, _manager(request).stt)


@router.post("/jobs", status_code=201)
async def create_job(body: JobCreateRequest, request: Request) -> dict:
    settings = request.app.state.settings
    client = _rate_limit(request, "jobs", settings.job_rate_limit_per_minute)
    # Metadata is re-resolved server-side; nothing from the client's earlier /info call is trusted.
    info = await _fetch_info(request, body.url)
    manager = _manager(request)
    job, token = manager.create_job(info, body.outputs, client)
    return {"job_id": job.id, "token": token, "job": job_payload(settings, job, manager.expires_at(job))}


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, request: Request, x_job_token: str | None = Header(default=None)) -> dict:
    manager = _manager(request)
    job = manager.get(job_id, x_job_token)
    return job_payload(request.app.state.settings, job, manager.expires_at(job))


@router.post("/jobs/{job_id}/cancel")
async def cancel_job(job_id: str, body: CancelRequest, request: Request,
                     x_job_token: str | None = Header(default=None)) -> dict:
    manager = _manager(request)
    job = manager.get(job_id, x_job_token)
    cancelled = manager.cancel(job, body.task)
    return {"cancelled": cancelled}


@router.post("/jobs/{job_id}/zip")
async def zip_job(job_id: str, body: ZipRequest, request: Request,
                  x_job_token: str | None = Header(default=None)) -> dict:
    manager = _manager(request)
    job = manager.get(job_id, x_job_token)
    artifact = await manager.make_zip(job, body.file_ids)
    return {"file": file_payload(request.app.state.settings, job, artifact)}


@router.delete("/jobs/{job_id}", status_code=204)
async def delete_job(job_id: str, request: Request, x_job_token: str | None = Header(default=None)) -> None:
    manager = _manager(request)
    job = manager.get(job_id, x_job_token)
    await manager.delete(job)


@router.get("/files/{job_id}/{file_id}")
async def download_file(
    job_id: str,
    file_id: str,
    request: Request,
    exp: int = Query(...),
    sig: str = Query(..., max_length=128),
    inline: int = Query(default=0),
) -> FileResponse:
    settings = request.app.state.settings
    if not verify_download(settings.signing_key, job_id, file_id, exp, sig):
        raise AppError("link_expired", "This download link is invalid or has expired.", 403)
    manager = _manager(request)
    job = manager.get_unchecked(job_id)
    artifact = manager.file_for_download(job, file_id)
    disposition = "inline" if inline else "attachment"
    # ASCII fallback for old clients plus the RFC 5987 UTF-8 name.
    ascii_name = artifact.download_name.encode("ascii", "replace").decode().replace("?", "_").replace('"', "")
    headers = {
        "Content-Disposition": f"{disposition}; filename=\"{ascii_name}\"; "
                               f"filename*=UTF-8''{quote(artifact.download_name, safe='')}",
        "X-Content-Type-Options": "nosniff",
        "Cache-Control": "private, no-store",
    }
    if inline:
        # Previews render in the browser: forbid scripts and isolate the origin.
        headers["Content-Security-Policy"] = (
            "default-src 'none'; img-src 'self'; media-src 'self'; style-src 'unsafe-inline'; sandbox"
        )
    return FileResponse(artifact.path, media_type=artifact.mime, headers=headers)
