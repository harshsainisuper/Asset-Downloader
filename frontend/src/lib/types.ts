export type OutputKind = "video" | "audio" | "thumbnail" | "transcript";
export const OUTPUT_KINDS: OutputKind[] = ["video", "audio", "thumbnail", "transcript"];

export type Quality = "best" | 2160 | 1440 | 1080 | 720 | 480 | 360;
export type VideoContainer = "mp4" | "webm";
export type AudioFormat = "mp3" | "m4a" | "wav";
export type ThumbnailFormat = "jpg" | "original";
export type TranscriptFormat = "txt" | "srt" | "vtt";

export interface QualityOption {
  height: number;
  label: string;
  fps: number | null;
  hdr: boolean;
  mp4: "h264" | "av1" | "transcode";
  webm: "native" | "unavailable";
  standard: boolean;
}

export interface CaptionTrackInfo {
  id: string;
  name: string;
  language: string;
  kind: "manual" | "auto";
  is_original: boolean;
}

export interface VideoInfo {
  video_id: string;
  title: string;
  channel: string | null;
  duration: number | null;
  is_short: boolean;
  thumbnail_url: string | null;
  webpage_url: string;
  video: { available: boolean; qualities: QualityOption[]; unavailable_standard: number[] };
  audio: { available: boolean; source_codec: string | null; source_kbps: number | null };
  captions: { tracks: CaptionTrackInfo[]; default_track_id: string | null };
  transcription: { stt_available: boolean; stt_label: string | null };
}

export interface VideoSettings {
  quality: Quality;
  format: VideoContainer;
  h264_compat: boolean;
}
export interface AudioSettings {
  format: AudioFormat;
  mp3_bitrate: 320 | 256 | 192 | 128;
}
export interface ThumbnailSettings {
  format: ThumbnailFormat;
}
export interface TranscriptSettings {
  track_id: string | null;
  formats: TranscriptFormat[];
  ai_fallback: boolean;
}

export interface OutputSettings {
  video: VideoSettings;
  audio: AudioSettings;
  thumbnail: ThumbnailSettings;
  transcript: TranscriptSettings;
}

export type Selection = Record<OutputKind, boolean>;

export interface JobFile {
  id: string;
  task: OutputKind | "zip";
  name: string;
  mime: string;
  size: number;
  label: string;
  meta: Record<string, unknown>;
  download_url: string;
  preview_url: string | null;
}

export type TaskStatus = "queued" | "running" | "completed" | "failed" | "cancelled";

export interface JobTask {
  kind: OutputKind;
  status: TaskStatus;
  progress: number;
  stage: string;
  error: { code: string; message: string } | null;
  notes: string[];
  files: JobFile[];
}

export type JobStatus = "queued" | "running" | "completed" | "partial" | "failed" | "cancelled";

export interface Job {
  job_id: string;
  status: JobStatus;
  created_at: number;
  finished_at: number | null;
  expires_at: number | null;
  video: { id: string; title: string };
  tasks: JobTask[];
}

export interface ServerConfig {
  ffmpeg_version: string | null;
  provider: { name: string; version?: string; js_runtimes?: Record<string, boolean>; js_runtime_available?: boolean };
  stt: { available: boolean; label: string | null; provider: string };
  limits: { retention_minutes: number; max_duration_seconds: number; max_filesize_mb: number; max_concurrent_jobs: number };
}
