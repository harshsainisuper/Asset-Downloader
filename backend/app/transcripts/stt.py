"""Configurable speech-to-text backends used only as an explicit fallback.

* ``openai_compatible``: any server implementing ``POST {base}/audio/transcriptions``
  with ``response_format=verbose_json`` (OpenAI Whisper, Groq, speaches /
  faster-whisper-server, LocalAI, ...).
* ``faster_whisper``: local inference with the optional ``faster-whisper`` package.

Results are always labelled as AI-generated. Failures raise errors; nothing is ever
fabricated.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from ..config import Settings
from ..errors import AppError, CancelledByUser
from ..media import ffmpeg
from .models import Segment

log = logging.getLogger(__name__)

Progress = Callable[[float], None]


class SttError(AppError):
    def __init__(self, message: str, code: str = "transcription_failed"):
        super().__init__(code, message, 502)


@dataclass
class SttResult:
    segments: list[Segment]
    language: str | None
    model: str


class SttProvider(ABC):
    label: str = "speech-to-text"

    @abstractmethod
    async def transcribe(
        self,
        audio_path: Path,
        work_dir: Path,
        *,
        duration: float | None,
        language: str | None,
        on_progress: Progress,
        cancel: threading.Event,
    ) -> SttResult: ...


class OpenAICompatibleStt(SttProvider):
    def __init__(self, settings: Settings, client: httpx.AsyncClient | None = None):
        self.settings = settings
        self.label = f"{settings.stt_model} via OpenAI-compatible API"
        self._client = client

    async def transcribe(self, audio_path, work_dir, *, duration, language, on_progress, cancel) -> SttResult:
        s = self.settings
        chunks = await ffmpeg.prepare_stt_chunks(
            audio_path, work_dir / "stt_chunks", s.stt_chunk_seconds, duration,
            lambda p: on_progress(p * 0.1),
        )
        if not chunks:
            raise SttError("Could not prepare audio for transcription.")

        url = s.stt_api_base_url.rstrip("/") + "/audio/transcriptions"
        headers = {"Authorization": f"Bearer {s.stt_api_key}"} if s.stt_api_key else {}
        client = self._client or httpx.AsyncClient(timeout=httpx.Timeout(s.stt_timeout_seconds))
        segments: list[Segment] = []
        detected: str | None = None
        try:
            for i, (chunk, offset) in enumerate(chunks):
                if cancel.is_set():
                    raise CancelledByUser()
                data = {"model": s.stt_model, "response_format": "verbose_json",
                        "timestamp_granularities[]": "segment"}
                if language:
                    data["language"] = language.split("-")[0]
                try:
                    with chunk.open("rb") as fh:
                        resp = await client.post(url, headers=headers, data=data,
                                                 files={"file": (chunk.name, fh, "audio/mpeg")})
                except httpx.HTTPError as exc:
                    log.warning("STT request failed: %s", exc)
                    raise SttError("Could not reach the transcription service.") from None
                if resp.status_code in (401, 403):
                    raise SttError("The transcription service rejected the API key (check STT_API_KEY).")
                if resp.status_code == 413:
                    raise SttError("The audio chunk was too large for the transcription service; "
                                   "lower STT_CHUNK_SECONDS.")
                if resp.status_code >= 400:
                    log.warning("STT error %s: %s", resp.status_code, resp.text[:500])
                    raise SttError(f"The transcription service returned an error (HTTP {resp.status_code}).")
                try:
                    payload = resp.json()
                except ValueError:
                    raise SttError("The transcription service returned an unexpected response.") from None

                detected = detected or payload.get("language")
                chunk_segments = payload.get("segments") or []
                if chunk_segments:
                    for seg in chunk_segments:
                        text = str(seg.get("text", "")).strip()
                        if text:
                            segments.append(Segment(offset + float(seg.get("start", 0)),
                                                    offset + float(seg.get("end", 0)), text))
                elif str(payload.get("text", "")).strip():
                    # Model without segment timestamps: keep chunk-level timing only.
                    chunk_len = (await ffmpeg.probe_duration(chunk)) or s.stt_chunk_seconds
                    segments.append(Segment(offset, offset + chunk_len, str(payload["text"]).strip()))
                on_progress(0.1 + 0.9 * (i + 1) / len(chunks))
        finally:
            if self._client is None:
                await client.aclose()
        return SttResult(segments=segments, language=detected or language, model=s.stt_model)


class FasterWhisperStt(SttProvider):
    _model = None
    _model_lock = threading.Lock()

    def __init__(self, settings: Settings):
        self.settings = settings
        self.label = f"faster-whisper ({settings.stt_local_model}, local)"

    def _load(self):
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-not-found]
        except ImportError:
            raise SttError("Local transcription requires the optional 'faster-whisper' package "
                           "(pip install faster-whisper).", code="stt_unavailable") from None
        with self._model_lock:
            if FasterWhisperStt._model is None:
                s = self.settings
                FasterWhisperStt._model = WhisperModel(
                    s.stt_local_model, device=s.stt_local_device, compute_type=s.stt_local_compute_type
                )
        return FasterWhisperStt._model

    async def transcribe(self, audio_path, work_dir, *, duration, language, on_progress, cancel) -> SttResult:
        def work() -> SttResult:
            model = self._load()
            seg_iter, info = model.transcribe(
                str(audio_path), language=(language.split("-")[0] if language else None), vad_filter=True
            )
            total = duration or getattr(info, "duration", None)
            segments: list[Segment] = []
            for seg in seg_iter:
                if cancel.is_set():
                    raise CancelledByUser()
                if seg.text.strip():
                    segments.append(Segment(float(seg.start), float(seg.end), seg.text.strip()))
                if total:
                    on_progress(min(0.99, float(seg.end) / total))
            return SttResult(segments, getattr(info, "language", language), self.settings.stt_local_model)

        try:
            return await asyncio.to_thread(work)
        except (AppError, CancelledByUser):
            raise
        except Exception as exc:  # noqa: BLE001 - model/runtime errors
            log.exception("faster-whisper transcription failed")
            raise SttError("Local transcription failed. Check the server logs.") from exc


def get_stt_provider(settings: Settings) -> SttProvider | None:
    if settings.stt_provider == "openai_compatible" and settings.stt_enabled:
        return OpenAICompatibleStt(settings)
    if settings.stt_provider == "faster_whisper":
        return FasterWhisperStt(settings)
    return None
