import { Check } from "lucide-react";
import type { ReactNode } from "react";
import { outputProblem } from "../lib/selection";
import type {
  AudioSettings,
  OutputKind,
  OutputSettings,
  Quality,
  Selection,
  ThumbnailSettings,
  TranscriptFormat,
  TranscriptSettings,
  VideoInfo,
  VideoSettings,
} from "../lib/types";
import { OUTPUT_KINDS } from "../lib/types";
import { Chip, Note, OUTPUT_META, SectionTitle, cn } from "./ui";

interface Props {
  info: VideoInfo | null;
  selection: Selection;
  onToggle: (kind: OutputKind) => void;
  onSelectAll: () => void;
  onDeselectAll: () => void;
  settings: OutputSettings;
  onSettingsChange: (s: OutputSettings) => void;
  disabled: boolean;
}

export function OutputSelector({ info, selection, onToggle, onSelectAll, onDeselectAll, settings, onSettingsChange, disabled }: Props) {
  const allSelected = OUTPUT_KINDS.every((k) => selection[k]);
  const noneSelected = OUTPUT_KINDS.every((k) => !selection[k]);
  const update = <K extends keyof OutputSettings>(key: K, value: OutputSettings[K]) =>
    onSettingsChange({ ...settings, [key]: value });

  return (
    <section className="panel">
      <SectionTitle
        step={2}
        title="Choose outputs"
        right={
          <div className="flex gap-1">
            <button type="button" className="btn-ghost py-1.5 text-xs" onClick={onSelectAll} disabled={disabled || allSelected}>
              Select all
            </button>
            <button type="button" className="btn-ghost py-1.5 text-xs" onClick={onDeselectAll} disabled={disabled || noneSelected}>
              Deselect all
            </button>
          </div>
        }
      />
      <div className="grid gap-3 md:grid-cols-2">
        {OUTPUT_KINDS.map((kind) => {
          const problem = info && selection[kind] ? outputProblem(kind, info, settings) : null;
          return (
            <OutputCard key={kind} kind={kind} checked={selection[kind]} onToggle={() => onToggle(kind)} disabled={disabled} problem={problem}>
              {info && kind === "video" && <VideoOptions info={info} value={settings.video} onChange={(v) => update("video", v)} />}
              {info && kind === "audio" && <AudioOptions info={info} value={settings.audio} onChange={(v) => update("audio", v)} />}
              {info && kind === "thumbnail" && (
                <ThumbnailOptions info={info} value={settings.thumbnail} onChange={(v) => update("thumbnail", v)} />
              )}
              {info && kind === "transcript" && (
                <TranscriptOptions info={info} value={settings.transcript} onChange={(v) => update("transcript", v)} />
              )}
            </OutputCard>
          );
        })}
      </div>
      {!info && <p className="mt-3 text-xs text-zinc-500">Fetch a video to see the options available for it.</p>}
    </section>
  );
}

function OutputCard({
  kind,
  checked,
  onToggle,
  disabled,
  problem,
  children,
}: {
  kind: OutputKind;
  checked: boolean;
  onToggle: () => void;
  disabled: boolean;
  problem: string | null;
  children: ReactNode;
}) {
  const meta = OUTPUT_META[kind];
  const Icon = meta.icon;
  const id = `output-${kind}`;
  return (
    <div
      className={cn(
        "rounded-xl border transition",
        checked ? "border-accent-500/40 bg-accent-500/[0.04]" : "border-white/[0.07] bg-ink-850 hover:border-white/15",
        disabled && "opacity-50",
      )}
    >
      <label htmlFor={id} className={cn("flex items-start gap-3 p-4", disabled ? "cursor-not-allowed" : "cursor-pointer")}>
        <input id={id} type="checkbox" className="peer sr-only" checked={checked} onChange={onToggle} disabled={disabled} />
        <span
          aria-hidden="true"
          className={cn(
            "mt-0.5 grid size-5 shrink-0 place-items-center rounded-md border transition peer-focus-visible:ring-2 peer-focus-visible:ring-accent-400",
            checked ? "border-accent-500 bg-accent-500 text-white" : "border-white/20 bg-ink-900",
          )}
        >
          {checked && <Check className="size-3.5" strokeWidth={3} />}
        </span>
        <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg bg-white/[0.04]", meta.tint)}>
          <Icon className="size-[18px]" />
        </span>
        <span className="min-w-0">
          <span className="block text-sm font-medium text-zinc-100">{meta.title}</span>
          <span className="block text-xs text-zinc-500">{meta.description}</span>
        </span>
      </label>
      {checked && children && <div className="space-y-3 border-t border-white/[0.06] px-4 pt-3 pb-4">{children}</div>}
      {checked && problem && (
        <div className="px-4 pb-4">
          <Note tone="warn">{problem}</Note>
        </div>
      )}
    </div>
  );
}

const STANDARD: Exclude<Quality, "best">[] = [2160, 1440, 1080, 720, 480, 360];

function VideoOptions({ info, value, onChange }: { info: VideoInfo; value: VideoSettings; onChange: (v: VideoSettings) => void }) {
  const available = info.video.qualities.filter((q) => (STANDARD as number[]).includes(q.height));
  const unavailable = STANDARD.filter((h) => !info.video.qualities.some((q) => q.height === h));
  const best = info.video.qualities[0];
  const selected = value.quality === "best" ? best : info.video.qualities.find((q) => q.height === value.quality);
  const webmPossible = value.quality === "best" ? info.video.qualities.some((q) => q.webm === "native") : selected?.webm === "native";

  let codecNote: ReactNode = null;
  if (selected && value.format === "mp4") {
    if (selected.mp4 === "h264") codecNote = <Note>H.264 video + AAC audio, merged without re-encoding.</Note>;
    else if (selected.mp4 === "av1")
      codecNote = value.h264_compat ? (
        <Note tone="warn">The AV1 stream will be re-encoded to H.264 for compatibility. This takes longer.</Note>
      ) : (
        <Note tone="info">{selected.height}p is only offered as AV1 here. It is stored in MP4 without re-encoding; older players may not support it.</Note>
      );
    else codecNote = <Note tone="warn">{selected.height}p has no MP4-native stream, so it will be re-encoded to H.264. This is slower. Choose WebM to keep the original stream.</Note>;
  } else if (selected && value.format === "webm" && webmPossible) {
    codecNote = <Note>VP9/AV1 video + Opus audio in WebM, without re-encoding.</Note>;
  }

  return (
    <>
      <div>
        <label className="field-label" htmlFor="video-quality">Quality</label>
        <select
          id="video-quality"
          className="input-select"
          value={String(value.quality)}
          onChange={(e) => onChange({ ...value, quality: (e.target.value === "best" ? "best" : Number(e.target.value)) as Quality })}
        >
          <option value="best">Best available{best ? ` (${best.height}p${best.hdr ? " HDR" : ""})` : ""}</option>
          {available.map((q) => (
            <option key={q.height} value={q.height}>
              {q.label}
              {q.fps && q.fps > 30 ? ` · ${Math.round(q.fps)}fps` : ""}
              {q.webm === "unavailable" ? " · MP4 only" : ""}
            </option>
          ))}
          {unavailable.length > 0 && (
            <optgroup label="Not available for this video">
              {unavailable.map((h) => (
                <option key={h} value={h} disabled>
                  {h}p{h === 2160 ? " (4K)" : ""} — unavailable
                </option>
              ))}
            </optgroup>
          )}
        </select>
      </div>
      <div>
        <span className="field-label">Format</span>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Video format">
          <Chip value="mp4" current={value.format} onSelect={(f) => onChange({ ...value, format: f })}>MP4</Chip>
          <Chip
            value="webm"
            current={value.format}
            onSelect={(f) => onChange({ ...value, format: f })}
            disabled={!webmPossible}
            title={webmPossible ? undefined : "No WebM stream at this quality"}
          >
            WebM
          </Chip>
        </div>
      </div>
      {value.format === "mp4" && selected && selected.mp4 === "av1" && (
        <label className="flex cursor-pointer items-center gap-2 text-xs text-zinc-300">
          <input
            type="checkbox"
            className="size-4 accent-[#ef4444]"
            checked={value.h264_compat}
            onChange={(e) => onChange({ ...value, h264_compat: e.target.checked })}
          />
          H.264 compatibility mode (re-encode AV1 for older devices)
        </label>
      )}
      {codecNote}
    </>
  );
}

function AudioOptions({ info, value, onChange }: { info: VideoInfo; value: AudioSettings; onChange: (v: AudioSettings) => void }) {
  const source = info.audio.source_codec ? `${info.audio.source_codec} ~${info.audio.source_kbps ?? "?"} kbps` : "the best available stream";
  return (
    <>
      <div>
        <span className="field-label">Format</span>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Audio format">
          {(["mp3", "m4a", "wav"] as const).map((f) => (
            <Chip key={f} value={f} current={value.format} onSelect={(v) => onChange({ ...value, format: v })}>
              {f.toUpperCase()}
            </Chip>
          ))}
        </div>
      </div>
      {value.format === "mp3" && (
        <div>
          <label className="field-label" htmlFor="mp3-bitrate">MP3 bitrate</label>
          <select
            id="mp3-bitrate"
            className="input-select"
            value={value.mp3_bitrate}
            onChange={(e) => onChange({ ...value, mp3_bitrate: Number(e.target.value) as AudioSettings["mp3_bitrate"] })}
          >
            <option value={320}>320 kbps (best)</option>
            <option value={256}>256 kbps</option>
            <option value={192}>192 kbps</option>
            <option value={128}>128 kbps (smaller)</option>
          </select>
        </div>
      )}
      <Note>
        {value.format === "m4a"
          ? "Keeps the original AAC stream when YouTube provides one (no quality loss)."
          : `Converted from ${source}. `}
        {value.format !== "m4a" && "YouTube audio is already lossy: WAV or a higher MP3 bitrate cannot restore lost detail; WAV just stores it uncompressed."}
      </Note>
    </>
  );
}

function ThumbnailOptions({ info, value, onChange }: { info: VideoInfo; value: ThumbnailSettings; onChange: (v: ThumbnailSettings) => void }) {
  return (
    <>
      <div>
        <span className="field-label">Format</span>
        <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Thumbnail format">
          <Chip value="jpg" current={value.format} onSelect={(f) => onChange({ format: f })}>JPG</Chip>
          <Chip value="original" current={value.format} onSelect={(f) => onChange({ format: f })}>Original (JPG/WebP)</Chip>
        </div>
      </div>
      <Note>
        All known thumbnail sizes are checked and the largest real image is used (often 1280×720; some videos only have
        smaller versions). {info.is_short ? "Shorts may use a vertical thumbnail." : ""}
      </Note>
    </>
  );
}

function TranscriptOptions({ info, value, onChange }: { info: VideoInfo; value: TranscriptSettings; onChange: (v: TranscriptSettings) => void }) {
  const tracks = info.captions.tracks;
  const toggleFormat = (f: TranscriptFormat) => {
    const has = value.formats.includes(f);
    const formats = has ? value.formats.filter((x) => x !== f) : [...value.formats, f];
    onChange({ ...value, formats: (["txt", "srt", "vtt"] as const).filter((x) => formats.includes(x)) });
  };
  const stt = info.transcription;
  return (
    <>
      {tracks.length > 0 ? (
        <div>
          <label className="field-label" htmlFor="caption-track">Caption language</label>
          <select
            id="caption-track"
            className="input-select"
            value={value.track_id ?? info.captions.default_track_id ?? ""}
            onChange={(e) => onChange({ ...value, track_id: e.target.value })}
          >
            {tracks.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} — {t.kind === "manual" ? "creator captions" : "YouTube auto-generated"}
                {t.is_original ? " · original" : ""}
              </option>
            ))}
          </select>
        </div>
      ) : (
        <Note tone="warn">This video has no captions on YouTube.</Note>
      )}
      <div>
        <span className="field-label">Export formats</span>
        <div className="flex flex-wrap gap-2" role="group" aria-label="Transcript formats">
          {(["txt", "srt", "vtt"] as const).map((f) => (
            <Chip key={f} value={f} current={value.formats} onSelect={toggleFormat}>
              {f.toUpperCase()}
            </Chip>
          ))}
        </div>
      </div>
      <label className={cn("flex items-start gap-2 text-xs", stt.stt_available ? "cursor-pointer text-zinc-300" : "cursor-not-allowed text-zinc-600")}>
        <input
          type="checkbox"
          className="mt-0.5 size-4 accent-[#ef4444]"
          checked={value.ai_fallback && stt.stt_available}
          disabled={!stt.stt_available}
          onChange={(e) => onChange({ ...value, ai_fallback: e.target.checked })}
        />
        <span>
          If captions are unavailable, generate an AI transcript
          {stt.stt_available ? ` with ${stt.stt_label}` : " (not configured on this server)"}. This is slower, may contain
          errors, and the output is clearly labelled as AI-generated.
        </span>
      </label>
    </>
  );
}
