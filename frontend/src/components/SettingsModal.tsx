import { useEffect, useRef, useState } from "react";
import { CircleAlert, CircleCheck, X } from "lucide-react";
import type { OutputSettings, Quality, ServerConfig, TranscriptFormat } from "../lib/types";
import { DEFAULT_SETTINGS } from "../lib/selection";
import { Chip, Note, cn } from "./ui";

interface Props {
  open: boolean;
  onClose: () => void;
  config: ServerConfig | null;
  configError: string | null;
  defaults: OutputSettings;
  onSaveDefaults: (s: OutputSettings) => void;
}

export function SettingsModal({ open, onClose, config, configError, defaults, onSaveDefaults }: Props) {
  const [draft, setDraft] = useState(defaults);
  const dialog = useRef<HTMLDialogElement>(null);

  useEffect(() => {
    if (open) setDraft(defaults);
  }, [open, defaults]);

  useEffect(() => {
    const el = dialog.current;
    if (!el) return;
    if (open && !el.open) el.showModal();
    if (!open && el.open) el.close();
  }, [open]);

  const toggleFormat = (f: TranscriptFormat) => {
    const has = draft.transcript.formats.includes(f);
    const formats = has ? draft.transcript.formats.filter((x) => x !== f) : [...draft.transcript.formats, f];
    if (formats.length) setDraft({ ...draft, transcript: { ...draft.transcript, formats } });
  };

  return (
    <dialog
      ref={dialog}
      onClose={onClose}
      onClick={(e) => e.target === dialog.current && onClose()}
      className="m-auto w-[min(560px,calc(100vw-2rem))] rounded-2xl border border-white/10 bg-ink-900 p-0 text-zinc-200 shadow-2xl backdrop:bg-black/70"
    >
      <div className="flex items-center justify-between border-b border-white/[0.06] px-5 py-4">
        <h2 className="text-base font-semibold">Settings</h2>
        <button type="button" className="rounded-md p-1.5 text-zinc-500 hover:bg-white/5 hover:text-zinc-200" onClick={onClose} aria-label="Close">
          <X className="size-4" />
        </button>
      </div>
      <div className="max-h-[70vh] space-y-6 overflow-y-auto px-5 py-5">
        <section>
          <h3 className="field-label">Server status</h3>
          {configError && <Note tone="error">{configError}</Note>}
          {config && (
            <ul className="space-y-2 text-sm">
              <Status ok={!!config.ffmpeg_version} label="FFmpeg" detail={config.ffmpeg_version ?? "Not found: video merging and audio conversion will fail."} />
              <Status ok label="Media provider" detail={`${config.provider.name}${config.provider.version ? ` ${config.provider.version}` : ""}`} />
              {config.provider.js_runtime_available !== undefined && (
                <Status
                  ok={config.provider.js_runtime_available}
                  label="JavaScript runtime"
                  detail={
                    config.provider.js_runtime_available
                      ? Object.entries(config.provider.js_runtimes ?? {}).filter(([, v]) => v).map(([k]) => k).join(", ")
                      : "None found (install Deno or Node.js 22+). YouTube downloads may fail."
                  }
                />
              )}
              {config.provider.pot_provider !== undefined && <YouTubeAccess provider={config.provider} />}
              <Status
                ok={config.stt.available}
                warn={!config.stt.available}
                label="AI transcription"
                detail={config.stt.available ? config.stt.label ?? config.stt.provider : "Not configured (set STT_PROVIDER in .env)"}
              />
              <li className="pt-1 text-xs text-zinc-500">
                Files kept for {config.limits.retention_minutes} min · max {Math.round(config.limits.max_duration_seconds / 60)} min per
                video · max {config.limits.max_filesize_mb} MB per file
              </li>
            </ul>
          )}
        </section>

        <section className="space-y-4">
          <h3 className="field-label">Default output options (this browser)</h3>
          <div className="grid gap-4 sm:grid-cols-2">
            <div>
              <label className="mb-1.5 block text-xs text-zinc-400" htmlFor="def-quality">Video quality</label>
              <select
                id="def-quality"
                className="input-select"
                value={String(draft.video.quality)}
                onChange={(e) =>
                  setDraft({ ...draft, video: { ...draft.video, quality: (e.target.value === "best" ? "best" : Number(e.target.value)) as Quality } })
                }
              >
                <option value="best">Best available</option>
                {[2160, 1440, 1080, 720, 480, 360].map((h) => (
                  <option key={h} value={h}>
                    {h}p
                  </option>
                ))}
              </select>
            </div>
            <div>
              <span className="mb-1.5 block text-xs text-zinc-400">Video format</span>
              <div className="flex gap-2">
                {(["mp4", "webm"] as const).map((f) => (
                  <Chip key={f} value={f} current={draft.video.format} onSelect={(v) => setDraft({ ...draft, video: { ...draft.video, format: v } })}>
                    {f.toUpperCase()}
                  </Chip>
                ))}
              </div>
            </div>
            <div>
              <span className="mb-1.5 block text-xs text-zinc-400">Audio format</span>
              <div className="flex gap-2">
                {(["mp3", "m4a", "wav"] as const).map((f) => (
                  <Chip key={f} value={f} current={draft.audio.format} onSelect={(v) => setDraft({ ...draft, audio: { ...draft.audio, format: v } })}>
                    {f.toUpperCase()}
                  </Chip>
                ))}
              </div>
            </div>
            <div>
              <span className="mb-1.5 block text-xs text-zinc-400">Transcript formats</span>
              <div className="flex gap-2">
                {(["txt", "srt", "vtt"] as const).map((f) => (
                  <Chip key={f} value={f} current={draft.transcript.formats} onSelect={toggleFormat}>
                    {f.toUpperCase()}
                  </Chip>
                ))}
              </div>
            </div>
          </div>
        </section>
      </div>
      <div className="flex justify-between gap-2 border-t border-white/[0.06] px-5 py-4">
        <button type="button" className="btn-ghost" onClick={() => setDraft(structuredClone(DEFAULT_SETTINGS))}>
          Reset
        </button>
        <div className="flex gap-2">
          <button type="button" className="btn-secondary" onClick={onClose}>
            Cancel
          </button>
          <button
            type="button"
            className="btn-primary"
            onClick={() => {
              onSaveDefaults(draft);
              onClose();
            }}
          >
            Save
          </button>
        </div>
      </div>
    </dialog>
  );
}

/** Remedies for YouTube bot checks on server IPs (see README: "YouTube bot checks"). */
function YouTubeAccess({ provider }: { provider: ServerConfig["provider"] }) {
  const pot = provider.pot_provider === "bgutil";
  const potOk = pot && !!provider.pot_server?.reachable && !!provider.pot_plugin_version;
  const potDetail = !pot
    ? "Off (POT_PROVIDER=none)"
    : !provider.pot_plugin_version
      ? "Plugin not installed (bgutil-ytdlp-pot-provider)"
      : provider.pot_server?.reachable
        ? `bgutil ${provider.pot_server.version ?? ""} running`.trim()
        : "Server not reachable yet (it may still be starting)";
  return (
    <>
      <Status ok={potOk} warn={!potOk} label="PO-token provider" detail={potDetail} />
      <Status
        ok={!!provider.cookies_configured}
        warn={!provider.cookies_configured}
        label="YouTube cookies"
        detail={
          provider.cookies_configured
            ? "Configured"
            : provider.cookies_file_missing
              ? "YTDLP_COOKIES_FILE is set but the file is missing or unreadable"
              : "Not configured (optional; helps with bot checks)"
        }
      />
      <Status
        ok={!!provider.proxy_configured}
        warn={!provider.proxy_configured}
        label="Outbound proxy"
        detail={provider.proxy_configured ? "Configured" : "Not configured (optional)"}
      />
    </>
  );
}

function Status({ ok, warn, label, detail }: { ok: boolean; warn?: boolean; label: string; detail: string }) {
  const Icon = ok ? CircleCheck : CircleAlert;
  return (
    <li className="flex items-start gap-2.5">
      <Icon className={cn("mt-0.5 size-4 shrink-0", ok ? "text-emerald-400" : warn ? "text-amber-400" : "text-red-400")} />
      <div className="min-w-0">
        <span className="text-zinc-200">{label}</span>
        <p className="truncate text-xs text-zinc-500" title={detail}>
          {detail}
        </p>
      </div>
    </li>
  );
}
