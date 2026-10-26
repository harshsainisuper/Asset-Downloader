import { Download, ExternalLink, FileArchive, LoaderCircle, Sparkles } from "lucide-react";
import { formatBytes } from "../lib/format";
import type { Job, JobFile } from "../lib/types";
import { Note, OUTPUT_META, SectionTitle, cn } from "./ui";

interface Props {
  job: Job;
  onDownloadAll: (files: JobFile[]) => void;
  onZip: () => void;
  zipping: boolean;
  zipError: string | null;
  retentionMinutes: number | null;
}

export function ResultsPanel({ job, onDownloadAll, onZip, zipping, zipError, retentionMinutes }: Props) {
  const files = job.tasks.flatMap((t) => t.files);
  if (files.length === 0) return null;
  const active = job.status === "queued" || job.status === "running";

  return (
    <section className="panel">
      <SectionTitle
        step={4}
        title="Results"
        right={
          <div className="flex flex-wrap gap-2">
            <button type="button" className="btn-secondary py-2 text-xs" onClick={() => onDownloadAll(files)}>
              <Download className="size-3.5" /> Download all completed
            </button>
            <button type="button" className="btn-secondary py-2 text-xs" onClick={onZip} disabled={zipping}>
              {zipping ? <LoaderCircle className="size-3.5 animate-spin" /> : <FileArchive className="size-3.5" />}
              {zipping ? "Packaging…" : "Download all as ZIP"}
            </button>
          </div>
        }
      />
      {zipError && (
        <div className="mb-3">
          <Note tone="error">{zipError}</Note>
        </div>
      )}
      <ul className="divide-y divide-white/[0.06] overflow-hidden rounded-xl border border-white/[0.07] bg-ink-850">
        {files.map((file) => (
          <FileRow key={file.id} file={file} />
        ))}
      </ul>
      <p className="mt-3 text-xs text-zinc-500">
        {active ? "More files will appear here as other items finish. " : ""}
        {retentionMinutes ? `Files are deleted from the server ${retentionMinutes} minutes after processing finishes.` : ""}
      </p>
    </section>
  );
}

function FileRow({ file }: { file: JobFile }) {
  const kind = file.task === "zip" ? null : OUTPUT_META[file.task];
  const Icon = kind?.icon ?? FileArchive;
  const isAi = file.meta?.origin === "ai_transcript";
  return (
    <li className="flex flex-col gap-3 p-4 sm:flex-row sm:items-center">
      <div className="flex min-w-0 flex-1 items-center gap-3">
        <span className={cn("grid size-9 shrink-0 place-items-center rounded-lg bg-white/[0.04]", kind?.tint ?? "text-zinc-400")}>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0">
          <p className="truncate text-sm text-zinc-100" title={file.name}>
            {file.name}
          </p>
          <p className="mt-0.5 flex flex-wrap items-center gap-x-2 text-xs text-zinc-500">
            <span>{file.label}</span>
            <span aria-hidden="true">·</span>
            <span>{formatBytes(file.size)}</span>
            {isAi && (
              <span className="inline-flex items-center gap-1 rounded border border-violet-400/30 bg-violet-500/10 px-1.5 text-[10px] font-medium tracking-wide text-violet-300 uppercase">
                <Sparkles className="size-3" /> AI-generated
              </span>
            )}
          </p>
        </div>
      </div>
      <div className="flex shrink-0 gap-2">
        {file.preview_url && (
          <a className="btn-ghost py-2 text-xs" href={file.preview_url} target="_blank" rel="noopener noreferrer">
            <ExternalLink className="size-3.5" /> Open
          </a>
        )}
        <a className="btn-secondary py-2 text-xs" href={file.download_url} download={file.name}>
          <Download className="size-3.5" /> Download
        </a>
      </div>
    </li>
  );
}
