import type { OutputSettings } from "./types";
import { DEFAULT_SETTINGS } from "./selection";

const KEY = "ytmt.defaults.v1";

/** Per-browser default output settings. Storage may be unavailable; never fail. */
export function loadDefaults(): OutputSettings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return structuredClone(DEFAULT_SETTINGS);
    const parsed = JSON.parse(raw) as Partial<OutputSettings>;
    return {
      video: { ...DEFAULT_SETTINGS.video, ...parsed.video },
      audio: { ...DEFAULT_SETTINGS.audio, ...parsed.audio },
      thumbnail: { ...DEFAULT_SETTINGS.thumbnail, ...parsed.thumbnail },
      transcript: { ...DEFAULT_SETTINGS.transcript, ...parsed.transcript, track_id: null },
    };
  } catch {
    return structuredClone(DEFAULT_SETTINGS);
  }
}

export function saveDefaults(settings: OutputSettings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify({ ...settings, transcript: { ...settings.transcript, track_id: null } }));
  } catch {
    /* storage unavailable */
  }
}

export function clearDefaults(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* storage unavailable */
  }
}
