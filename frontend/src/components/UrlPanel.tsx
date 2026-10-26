import { type FormEvent, useMemo, useState } from "react";
import { ClipboardPaste, Clock, ImageOff, Link2, LoaderCircle, Search, Smartphone, User, X } from "lucide-react";
import { checkYoutubeUrl } from "../lib/youtube";
import { formatDuration } from "../lib/format";
import type { VideoInfo } from "../lib/types";
import { Note, SectionTitle, cn } from "./ui";

interface Props {
  url: string;
  onUrlChange: (url: string) => void;
  onFetch: () => void;
  loading: boolean;
  error: string | null;
  info: VideoInfo | null;
}

export function UrlPanel({ url, onUrlChange, onFetch, loading, error, info }: Props) {
  const check = useMemo(() => checkYoutubeUrl(url), [url]);
  const showInvalid = !check.ok && check.reason !== "";

  const submit = (e: FormEvent) => {
    e.preventDefault();
    if (check.ok && !loading) onFetch();
  };

  const paste = async () => {
    try {
      const text = await navigator.clipboard.readText();
      if (text) onUrlChange(text.trim());
    } catch {
      /* clipboard permission denied */
    }
  };

  return (
    <section className="panel">
      <SectionTitle step={1} title="Paste a YouTube link" />
      <form onSubmit={submit} className="flex flex-col gap-3 sm:flex-row">
        <div className="relative flex-1">
          <Link2 className="pointer-events-none absolute top-1/2 left-3.5 size-4 -translate-y-1/2 text-zinc-500" />
          <input
            type="url"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            maxLength={2048}
            value={url}
            onChange={(e) => onUrlChange(e.target.value)}
            placeholder="https://www.youtube.com/watch?v=…  ·  youtu.be/…  ·  /shorts/…"
            aria-label="YouTube URL"
            aria-invalid={showInvalid}
            className={cn(
              "w-full rounded-xl border bg-ink-850 py-3 pr-20 pl-10 text-sm text-zinc-100 outline-none placeholder:text-zinc-600",
              "focus:ring-2",
              showInvalid
                ? "border-red-500/50 focus:ring-red-500/20"
                : "border-white/10 focus:border-accent-500/60 focus:ring-accent-500/20",
            )}
          />
          <div className="absolute top-1/2 right-2 flex -translate-y-1/2 gap-1">
            {url && (
              <button type="button" className="rounded-md p-1.5 text-zinc-500 hover:bg-white/5 hover:text-zinc-200" onClick={() => onUrlChange("")} aria-label="Clear">
                <X className="size-4" />
              </button>
            )}
            <button type="button" className="rounded-md p-1.5 text-zinc-500 hover:bg-white/5 hover:text-zinc-200" onClick={paste} aria-label="Paste from clipboard" title="Paste">
              <ClipboardPaste className="size-4" />
            </button>
          </div>
        </div>
        <button type="submit" className="btn-primary py-3 sm:w-40" disabled={!check.ok || loading}>
          {loading ? <LoaderCircle className="size-4 animate-spin" /> : <Search className="size-4" />}
          {loading ? "Fetching…" : "Fetch video"}
        </button>
      </form>
      {showInvalid && <p className="mt-2 text-xs text-red-300">{check.reason}</p>}
      {error && (
        <div className="mt-4">
          <Note tone="error">{error}</Note>
        </div>
      )}
      {info && <VideoPreview key={info.video_id} info={info} />}
    </section>
  );
}

function VideoPreview({ info }: { info: VideoInfo }) {
  const best = info.video.qualities[0];
  const [imgFailed, setImgFailed] = useState(false);
  return (
    <div className="mt-5 flex flex-col gap-4 rounded-xl border border-white/[0.06] bg-ink-850 p-3 sm:flex-row">
      {info.thumbnail_url && (
        <div className={cn("relative shrink-0 overflow-hidden rounded-lg bg-black", info.is_short ? "aspect-[9/16] w-32 self-center sm:self-auto" : "aspect-video w-full sm:w-64")}>
          {imgFailed ? (
            <div className="grid size-full place-items-center text-zinc-600">
              <ImageOff className="size-6" />
            </div>
          ) : (
            <img
              src={info.thumbnail_url}
              alt=""
              referrerPolicy="no-referrer"
              className="size-full object-cover"
              onError={() => setImgFailed(true)}
            />
          )}
          {info.duration != null && (
            <span className="absolute right-1.5 bottom-1.5 rounded bg-black/80 px-1.5 py-0.5 text-[11px] font-medium text-white">
              {formatDuration(info.duration)}
            </span>
          )}
        </div>
      )}
      <div className="min-w-0 flex-1 py-1">
        <h3 className="line-clamp-2 text-[15px] leading-snug font-semibold text-zinc-100">{info.title}</h3>
        <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-zinc-400">
          {info.channel && (
            <span className="inline-flex items-center gap-1.5">
              <User className="size-3.5" /> {info.channel}
            </span>
          )}
          {info.duration != null && (
            <span className="inline-flex items-center gap-1.5">
              <Clock className="size-3.5" /> {formatDuration(info.duration)}
            </span>
          )}
          {info.is_short && (
            <span className="inline-flex items-center gap-1.5 text-accent-400">
              <Smartphone className="size-3.5" /> Short
            </span>
          )}
        </div>
        <div className="mt-3 flex flex-wrap gap-1.5 text-[11px]">
          <Tag ok={info.video.available}>{best ? `Video up to ${best.height}p${best.hdr ? " HDR" : ""}` : "No video streams"}</Tag>
          <Tag ok={info.audio.available}>
            {info.audio.available ? `Audio ${info.audio.source_codec ?? ""} ~${info.audio.source_kbps ?? "?"} kbps` : "No audio"}
          </Tag>
          <Tag ok>Thumbnail</Tag>
          <Tag ok={info.captions.tracks.length > 0 || info.transcription.stt_available}>
            {info.captions.tracks.length > 0
              ? `${info.captions.tracks.length} caption track${info.captions.tracks.length > 1 ? "s" : ""}`
              : info.transcription.stt_available
                ? "No captions · AI transcription available"
                : "No captions"}
          </Tag>
        </div>
      </div>
    </div>
  );
}

function Tag({ ok, children }: { ok: boolean; children: React.ReactNode }) {
  return (
    <span
      className={cn(
        "rounded-md border px-2 py-0.5",
        ok ? "border-white/10 bg-white/[0.03] text-zinc-300" : "border-white/5 text-zinc-600 line-through",
      )}
    >
      {children}
    </span>
  );
}
