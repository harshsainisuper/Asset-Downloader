import threading

import anyio
import httpx
import pytest

from app.config import Settings
from app.transcripts.stt import OpenAICompatibleStt, SttError, get_stt_provider

from .conftest import requires_ffmpeg

pytestmark = requires_ffmpeg


def _settings(**kw) -> Settings:
    return Settings(_env_file=None, stt_provider="openai_compatible", stt_api_key="sk-test",
                    stt_api_base_url="https://stt.example/v1", stt_chunk_seconds=30, **kw)


def test_provider_factory():
    assert get_stt_provider(Settings(_env_file=None, stt_provider="none")) is None
    assert isinstance(get_stt_provider(_settings()), OpenAICompatibleStt)


def _run(stt, audio, tmp_path):
    async def go():
        return await stt.transcribe(audio, tmp_path, duration=2.0, language="en",
                                    on_progress=lambda p: None, cancel=threading.Event())

    return anyio.run(go)


def test_openai_compatible_request_and_segments(media_files, tmp_path):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        seen["auth"] = request.headers.get("authorization")
        body = request.content
        seen["has_verbose"] = b"verbose_json" in body
        return httpx.Response(200, json={"language": "english", "segments": [
            {"start": 0.0, "end": 1.0, "text": " Hello "}, {"start": 1.0, "end": 1.9, "text": "world"}]})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    result = _run(OpenAICompatibleStt(_settings(), client=client), media_files["opus"], tmp_path)
    assert seen == {"url": "https://stt.example/v1/audio/transcriptions", "auth": "Bearer sk-test",
                    "has_verbose": True}
    assert [(s.start, s.end, s.text) for s in result.segments] == [(0.0, 1.0, "Hello"), (1.0, 1.9, "world")]
    assert result.model == "whisper-1"


def test_openai_compatible_auth_error(media_files, tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
    with pytest.raises(SttError, match="API key"):
        _run(OpenAICompatibleStt(_settings(), client=client), media_files["opus"], tmp_path)


def test_openai_compatible_server_error(media_files, tmp_path):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500, text="boom")))
    with pytest.raises(SttError, match="HTTP 500"):
        _run(OpenAICompatibleStt(_settings(), client=client), media_files["opus"], tmp_path)
