import type { OutputKind, OutputSettings, Selection, VideoInfo } from "./types";
import { OUTPUT_KINDS } from "./types";

export const EMPTY_SELECTION: Selection = { video: false, audio: false, thumbnail: false, transcript: false };
export const ALL_SELECTED: Selection = { video: true, audio: true, thumbnail: true, transcript: true };

export const DEFAULT_SETTINGS: OutputSettings = {
  video: { quality: "best", format: "mp4", h264_compat: false },
  audio: { format: "mp3", mp3_bitrate: 320 },
  thumbnail: { format: "jpg" },
  transcript: { track_id: null, formats: ["srt"], ai_fallback: false },
};

export function selectedKinds(selection: Selection): OutputKind[] {
  return OUTPUT_KINDS.filter((k) => selection[k]);
}

/** Returns a reason the given output can't be produced, or null when it can. */
export function outputProblem(kind: OutputKind, info: VideoInfo, settings: OutputSettings): string | null {
  switch (kind) {
    case "video": {
      if (!info.video.available) return "No downloadable video streams.";
      const { quality, format } = settings.video;
      const options = info.video.qualities;
      if (quality !== "best" && !options.some((q) => q.height === quality)) return `${quality}p is not available.`;
      if (format === "webm") {
        const relevant = quality === "best" ? options : options.filter((q) => q.height === quality);
        if (!relevant.some((q) => q.webm === "native")) return "WebM is not available at this quality.";
      }
      return null;
    }
    case "audio":
      return info.audio.available ? null : "This video has no audio track.";
    case "thumbnail":
      return null;
    case "transcript": {
      if (settings.transcript.formats.length === 0) return "Choose at least one transcript format.";
      const hasCaptions = info.captions.tracks.length > 0;
      if (!hasCaptions && !settings.transcript.ai_fallback) {
        return info.transcription.stt_available
          ? "No captions available. Enable the AI transcription fallback."
          : "No captions available and AI transcription is not configured on the server.";
      }
      if (!hasCaptions && !info.transcription.stt_available) return "AI transcription is not configured on the server.";
      return null;
    }
  }
}

export function canProcess(info: VideoInfo | null, selection: Selection, settings: OutputSettings, urlValid: boolean) {
  if (!urlValid || !info) return { ok: false, reason: "Fetch a valid YouTube or Instagram video first." };
  const kinds = selectedKinds(selection);
  if (kinds.length === 0) return { ok: false, reason: "Select at least one output." };
  for (const kind of kinds) {
    const problem = outputProblem(kind, info, settings);
    if (problem) return { ok: false, reason: problem };
  }
  return { ok: true, reason: "" };
}

/** Build the request body; unselected outputs are omitted entirely. */
export function buildOutputs(selection: Selection, settings: OutputSettings): Record<string, unknown> {
  const outputs: Record<string, unknown> = {};
  if (selection.video) outputs.video = { ...settings.video };
  if (selection.audio) {
    outputs.audio = settings.audio.format === "mp3" ? { ...settings.audio } : { format: settings.audio.format };
  }
  if (selection.thumbnail) outputs.thumbnail = { ...settings.thumbnail };
  if (selection.transcript) {
    const t = settings.transcript;
    outputs.transcript = { track_id: t.track_id ?? "auto", formats: t.formats, ai_fallback: t.ai_fallback };
  }
  return outputs;
}

/** Pick sensible per-video defaults (e.g. a quality that actually exists). */
export function adaptSettings(settings: OutputSettings, info: VideoInfo): OutputSettings {
  const next = structuredClone(settings);
  const heights = info.video.qualities.map((q) => q.height);
  if (next.video.quality !== "best" && !heights.includes(next.video.quality)) next.video.quality = "best";
  const tracks = info.captions.tracks.map((t) => t.id);
  if (!next.transcript.track_id || !tracks.includes(next.transcript.track_id)) {
    next.transcript.track_id = info.captions.default_track_id;
  }
  if (tracks.length === 0 && info.transcription.stt_available) next.transcript.ai_fallback = true;
  return next;
}
