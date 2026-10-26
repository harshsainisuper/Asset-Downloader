"""End-to-end API tests: real FFmpeg processing behind a fake provider."""

from __future__ import annotations

import io
import json
import subprocess
import time
import zipfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from app.errors import AppError
from app.media import thumbnails
from app.media.models import CaptionTrack

from .conftest import SAMPLE_JSON3, FakeStt, make_info, requires_ffmpeg, wait_for_job

URL = "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
pytestmark = requires_ffmpeg


def create(client, outputs: dict, url: str = URL) -> tuple[str, str, dict]:
    resp = client.post("/api/jobs", json={"url": url, "outputs": outputs})
    assert resp.status_code == 201, resp.text
    body = resp.json()
    return body["job_id"], body["token"], body["job"]


def probe(data: bytes, tmp_path: Path, name: str) -> list[dict]:
    path = tmp_path / name
    path.write_bytes(data)
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,codec_name",
                          "-show_entries", "format=format_name", "-of", "json", str(path)],
                         capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def task(job: dict, kind: str) -> dict:
    return next(t for t in job["tasks"] if t["kind"] == kind)


# --------------------------------------------------------------------------- info


def test_info_endpoint_returns_metadata_and_qualities(client):
    resp = client.post("/api/info", json={"url": "https://youtu.be/dQw4w9WgXcQ"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["title"].startswith("Test Video")
    assert [q["height"] for q in data["video"]["qualities"]] == [2160, 1440, 1080, 720, 360]
    assert data["video"]["unavailable_standard"] == [480]
    assert data["audio"]["available"] is True
    assert data["transcription"]["stt_available"] is False


def test_info_rejects_foreign_domain(client):
    resp = client.post("/api/info", json={"url": "https://vimeo.com/123"})
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "invalid_url"


@pytest.mark.parametrize("message,code", [
    ("ERROR: [youtube] x: Private video. Sign in if you've been granted access", "video_private"),
    ("ERROR: [youtube] x: Sign in to confirm your age.", "age_restricted"),
    ("ERROR: [youtube] x: Video unavailable", "video_unavailable"),
])
def test_info_maps_provider_errors_without_leaking_details(client, provider, message, code):
    from app.errors import map_provider_error

    provider.fail_info = map_provider_error(Exception(message))
    resp = client.post("/api/info", json={"url": URL})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == code
    assert "[youtube]" not in body["error"]["message"]


def test_unexpected_errors_return_generic_message(client, provider):
    provider.fail_info = RuntimeError("secret internal path /srv/x.py line 42")
    resp = client.post("/api/info", json={"url": URL})
    assert resp.status_code == 500
    assert resp.json() == {"error": {"code": "internal_error", "message": "An unexpected server error occurred."}}


def test_too_long_videos_rejected(make_client, provider):
    provider.info = make_info(duration=10_000)
    client = make_client(max_duration_seconds=3600)
    resp = client.post("/api/info", json={"url": URL})
    assert resp.status_code == 422 and resp.json()["error"]["code"] == "too_long"


# --------------------------------------------------------------------------- selection


def test_video_only_job_processes_only_video(client, provider, tmp_path):
    job_id, token, job = create(client, {"video": {"quality": 1080, "format": "mp4"}})
    assert [t["kind"] for t in job["tasks"]] == ["video"]
    done = wait_for_job(client, job_id, token)
    assert done["status"] == "completed", done
    video = task(done, "video")
    assert len(video["files"]) == 1
    f = video["files"][0]
    assert f["name"] == "Test Video Quotes Slashes [1080p].mp4"
    assert f["label"] == "MP4 · 1080p · H.264 + AAC"
    assert sorted(provider.download_calls) == ["137", "140"]

    resp = client.get(f["download_url"])
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "video/mp4"
    assert "attachment" in resp.headers["content-disposition"]
    info = probe(resp.content, tmp_path, "out.mp4")
    assert sorted(s["codec_name"] for s in info["streams"]) == ["aac", "h264"]
    assert "mp4" in info["format"]["format_name"]


def test_webm_output_is_really_webm(client, tmp_path):
    job_id, token, _ = create(client, {"video": {"quality": 720, "format": "webm"}})
    done = wait_for_job(client, job_id, token)
    f = task(done, "video")["files"][0]
    info = probe(client.get(f["download_url"]).content, tmp_path, "out.webm")
    assert sorted(s["codec_name"] for s in info["streams"]) == ["opus", "vp9"]
    assert "webm" in info["format"]["format_name"]


def test_vp9_only_resolution_is_reencoded_to_h264_for_mp4(client, tmp_path):
    job_id, token, _ = create(client, {"video": {"quality": 1440, "format": "mp4"}})
    done = wait_for_job(client, job_id, token)
    t = task(done, "video")
    assert t["status"] == "completed", t
    assert any("re-encoded" in n for n in t["notes"])
    info = probe(client.get(t["files"][0]["download_url"]).content, tmp_path, "out.mp4")
    assert sorted(s["codec_name"] for s in info["streams"]) == ["aac", "h264"]


def test_unavailable_quality_fails_clearly(client, provider):
    job_id, token, _ = create(client, {"video": {"quality": 480}})
    done = wait_for_job(client, job_id, token)
    t = task(done, "video")
    assert t["status"] == "failed"
    assert t["error"]["code"] == "format_unavailable"
    assert "480p is not available" in t["error"]["message"]
    assert provider.download_calls == []


@pytest.mark.parametrize("fmt,codec,mime", [("mp3", "mp3", "audio/mpeg"), ("m4a", "aac", "audio/mp4"),
                                             ("wav", "pcm_s16le", "audio/wav")])
def test_audio_only_formats(client, provider, tmp_path, fmt, codec, mime):
    job_id, token, job = create(client, {"audio": {"format": fmt}})
    assert [t["kind"] for t in job["tasks"]] == ["audio"]
    done = wait_for_job(client, job_id, token)
    t = task(done, "audio")
    assert t["status"] == "completed", t
    f = t["files"][0]
    assert f["mime"] == mime and f["name"].endswith(f".{fmt}")
    info = probe(client.get(f["download_url"]).content, tmp_path, f"a.{fmt}")
    assert [s["codec_name"] for s in info["streams"]] == [codec]
    assert all(c in ("140", "251") for c in provider.download_calls)
    if fmt == "wav":
        assert any("does not restore" in n for n in t["notes"])


def test_shared_audio_source_downloaded_once(client, provider):
    job_id, token, _ = create(client, {"video": {"quality": 720, "format": "mp4"}, "audio": {"format": "m4a"}})
    done = wait_for_job(client, job_id, token)
    assert done["status"] == "completed", done
    assert sorted(provider.download_calls) == ["136", "140"]  # 140 shared by both outputs


def test_thumbnail_only(client, provider, monkeypatch, tmp_path):
    monkeypatch.setattr(thumbnails, "make_client", lambda: _thumb_client())
    job_id, token, job = create(client, {"thumbnail": {"format": "jpg"}})
    assert [t["kind"] for t in job["tasks"]] == ["thumbnail"]
    done = wait_for_job(client, job_id, token)
    t = task(done, "thumbnail")
    assert t["status"] == "completed", t
    f = t["files"][0]
    assert f["label"] == "JPG · 1280×720"
    assert provider.download_calls == []  # no media streams downloaded for a thumbnail
    resp = client.get(f["preview_url"])
    assert resp.headers["content-type"] == "image/jpeg"
    assert "inline" in resp.headers["content-disposition"]


def _thumb_client():
    import httpx
    from PIL import Image

    def jpeg(w, h):
        buf = io.BytesIO()
        Image.new("RGB", (w, h), (200, 30, 30)).save(buf, "JPEG")
        return buf.getvalue()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/maxresdefault.jpg"):
            return httpx.Response(200, content=jpeg(1280, 720), headers={"content-type": "image/jpeg"})
        if request.url.path.endswith("/hqdefault.jpg"):
            return httpx.Response(200, content=jpeg(480, 360), headers={"content-type": "image/jpeg"})
        return httpx.Response(404)

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- transcripts


def _with_captions(provider):
    provider.info = make_info(captions=[CaptionTrack("en", "en", "English", "manual", True, ["json3", "vtt"])])
    provider.captions["en"] = ("json3", SAMPLE_JSON3)


def test_transcript_from_captions_in_all_formats(client, provider):
    _with_captions(provider)
    job_id, token, _ = create(client, {"transcript": {"formats": ["txt", "srt", "vtt"]}})
    done = wait_for_job(client, job_id, token)
    t = task(done, "transcript")
    assert t["status"] == "completed", t
    files = {f["name"].rsplit(".", 1)[1]: f for f in t["files"]}
    assert set(files) == {"txt", "srt", "vtt"}
    assert all(f["meta"]["origin"] == "manual_captions" for f in files.values())
    srt = client.get(files["srt"]["download_url"]).text
    assert srt.startswith("1\n00:00:00,000 --> 00:00:01,500\nHello and welcome.\n")
    vtt = client.get(files["vtt"]["download_url"]).text
    assert vtt.startswith("WEBVTT") and "00:00:04.000 --> 00:00:06.000" in vtt
    txt = client.get(files["txt"]["download_url"]).text
    assert "Creator-provided captions" in txt and "Hello and welcome." in txt
    assert provider.download_calls == []


def test_transcript_without_captions_fails_honestly(client):
    job_id, token, _ = create(client, {"transcript": {"formats": ["txt"]}})
    t = task(wait_for_job(client, job_id, token), "transcript")
    assert t["status"] == "failed"
    assert t["error"]["code"] == "no_captions"
    assert t["files"] == []


def test_ai_fallback_without_stt_configured(client):
    job_id, token, _ = create(client, {"transcript": {"formats": ["txt"], "ai_fallback": True}})
    t = task(wait_for_job(client, job_id, token), "transcript")
    assert t["status"] == "failed" and t["error"]["code"] == "stt_unavailable"


def test_ai_fallback_is_labelled(make_client, provider):
    stt = FakeStt()
    client = make_client(stt=stt)
    job_id, token, _ = create(client, {"transcript": {"formats": ["srt", "txt"], "ai_fallback": True}})
    t = task(wait_for_job(client, job_id, token), "transcript")
    assert t["status"] == "completed", t
    assert stt.calls == 1
    assert all(f["meta"]["origin"] == "ai_transcript" for f in t["files"])
    assert all("AI transcript" in f["name"] for f in t["files"])
    txt = next(f for f in t["files"] if f["name"].endswith(".txt"))
    assert "AI-generated transcript" in client.get(txt["download_url"]).text


def test_ai_fallback_empty_result_is_failure(make_client):
    client = make_client(stt=FakeStt(segments=[]))
    job_id, token, _ = create(client, {"transcript": {"formats": ["srt"], "ai_fallback": True}})
    t = task(wait_for_job(client, job_id, token), "transcript")
    assert t["status"] == "failed" and t["error"]["code"] == "transcription_empty"


def test_unknown_caption_track_rejected(client, provider):
    _with_captions(provider)
    job_id, token, _ = create(client, {"transcript": {"track_id": "manual:de"}})
    t = task(wait_for_job(client, job_id, token), "transcript")
    assert t["status"] == "failed" and t["error"]["code"] == "caption_track_unavailable"


# --------------------------------------------------------------------------- job status & access


def test_job_requires_token(client):
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    assert client.get(f"/api/jobs/{job_id}").status_code == 404
    assert client.get(f"/api/jobs/{job_id}", headers={"X-Job-Token": "wrong"}).status_code == 404
    assert client.get("/api/jobs/doesnotexist", headers={"X-Job-Token": token}).status_code == 404
    wait_for_job(client, job_id, token)


def test_signed_download_urls_are_enforced(client):
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    f = task(wait_for_job(client, job_id, token), "audio")["files"][0]
    url = f["download_url"]
    assert client.get(url).status_code == 200
    parts = urlsplit(url)
    q = {k: v[0] for k, v in parse_qs(parts.query).items()}
    assert client.get(f"{parts.path}?exp={q['exp']}&sig={'0' * 64}").status_code == 403
    assert client.get(f"{parts.path}?exp={int(q['exp']) + 1}&sig={q['sig']}").status_code == 403
    assert client.get(f"{parts.path}").status_code == 422
    other = f"/api/files/{job_id}/..%2F..%2Fetc%2Fpasswd?exp={q['exp']}&sig={q['sig']}"
    assert client.get(other).status_code in (403, 404)


def test_expired_link_rejected(client):
    from app.security import sign_download

    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    f = task(wait_for_job(client, job_id, token), "audio")["files"][0]
    file_id = f["id"]
    exp = int(time.time()) - 10
    sig = sign_download(b"test-secret", job_id, file_id, exp)
    assert client.get(f"/api/files/{job_id}/{file_id}?exp={exp}&sig={sig}").status_code == 403


def test_cancel_running_task(client, provider):
    provider.slow = True
    job_id, token, _ = create(client, {"video": {"quality": 720}})
    time.sleep(0.3)
    resp = client.post(f"/api/jobs/{job_id}/cancel", json={"task": "video"}, headers={"X-Job-Token": token})
    assert resp.status_code == 200
    done = wait_for_job(client, job_id, token)
    assert done["status"] == "cancelled"
    assert task(done, "video")["files"] == []


def test_partial_failure_keeps_successful_outputs(client, provider):
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}, "transcript": {"formats": ["txt"]}})
    done = wait_for_job(client, job_id, token)
    assert done["status"] == "partial"
    assert task(done, "audio")["status"] == "completed"
    assert task(done, "transcript")["status"] == "failed"


def test_zip_of_completed_files(client, provider):
    _with_captions(provider)
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}, "transcript": {"formats": ["srt", "txt"]}})
    done = wait_for_job(client, job_id, token)
    resp = client.post(f"/api/jobs/{job_id}/zip", json={}, headers={"X-Job-Token": token})
    assert resp.status_code == 200, resp.text
    z = resp.json()["file"]
    data = client.get(z["download_url"]).content
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    expected = {f["name"] for t in done["tasks"] for f in t["files"]}
    assert set(names) == expected and len(names) == 3


def test_zip_requires_token(client):
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    wait_for_job(client, job_id, token)
    assert client.post(f"/api/jobs/{job_id}/zip", json={}).status_code == 404


def test_delete_job_removes_files(client, settings):
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    wait_for_job(client, job_id, token)
    job_dir = settings.temp_dir / "jobs" / job_id
    assert job_dir.exists()
    assert client.delete(f"/api/jobs/{job_id}", headers={"X-Job-Token": token}).status_code == 204
    assert not job_dir.exists()
    assert client.get(f"/api/jobs/{job_id}", headers={"X-Job-Token": token}).status_code == 404


def test_retention_cleanup(client, settings):
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    wait_for_job(client, job_id, token)
    manager = client.app.state.manager
    assert manager.cleanup_expired(now=time.time() + 10) == 0
    assert manager.cleanup_expired(now=time.time() + settings.retention_minutes * 60 + 5) == 1
    assert not (settings.temp_dir / "jobs" / job_id).exists()


# --------------------------------------------------------------------------- limits


def test_request_body_limit(client):
    resp = client.post("/api/info", content=b"x" * 100_000, headers={"content-type": "application/json"})
    assert resp.status_code == 413


def test_rate_limit(make_client):
    client = make_client(info_rate_limit_per_minute=2)
    assert client.post("/api/info", json={"url": URL}).status_code == 200
    assert client.post("/api/info", json={"url": URL}).status_code == 200
    resp = client.post("/api/info", json={"url": URL})
    assert resp.status_code == 429 and resp.json()["error"]["code"] == "rate_limited"


def test_per_client_active_job_limit(make_client, provider):
    provider.slow = True
    client = make_client(max_active_jobs_per_client=1)
    job_id, token, _ = create(client, {"audio": {"format": "mp3"}})
    resp = client.post("/api/jobs", json={"url": URL, "outputs": {"audio": {}}})
    assert resp.status_code == 429
    client.post(f"/api/jobs/{job_id}/cancel", json={}, headers={"X-Job-Token": token})
    wait_for_job(client, job_id, token)


def test_validation_errors_are_clean(client):
    resp = client.post("/api/jobs", json={"url": URL, "outputs": {"video": {"quality": "8k"}}})
    assert resp.status_code == 422
    body = resp.json()
    assert body["error"]["code"] == "validation_error"
    assert "Traceback" not in json.dumps(body)


def test_app_error_class_is_safe():
    err = AppError("x", "msg", 418)
    assert err.status_code == 418 and err.message == "msg"
