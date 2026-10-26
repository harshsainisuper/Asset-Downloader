import { Ban, CircleAlert, CircleCheck, CircleX, LoaderCircle, Play, X } from "lucide-react";
import type { Job, JobTask, OutputKind } from "../lib/types";
import { Note, OUTPUT_META, ProgressBar, SectionTitle, cn } from "./ui";

interface Props {
  canProcess: { ok: boolean; reason: string };
  selectedCount: number;
  onProcess: () => void;
  submitting: boolean;
  job: Job | null;
  jobError: string | null;
  onCancel: (task?: OutputKind) => void;
}

const STATUS_LABEL: Record<JobTask["status"], string> = {
  queued: "Queued",
  running: "Processing",
  completed: "Done",
  failed: "Failed",
  cancelled: "Cancelled",
};

export function ProcessPanel({ canProcess, selectedCount, onProcess, submitting, job, jobError, onCancel }: Props) {
  const active = job && (job.status === "queued" || job.status === "running");
  return (
    <section className="panel">
      <SectionTitle
        step={3}
        title="Process"
        right={
          active ? (
            <button type="button" className="btn-ghost py-1.5 text-xs text-red-300 hover:text-red-200" onClick={() => onCancel()}>
              <Ban className="size-3.5" /> Cancel all
            </button>
          ) : null
        }
      />
      <div className="flex flex-col items-start gap-2 sm:flex-row sm:items-center">
        <button type="button" className="btn-primary px-5 py-3" disabled={!canProcess.ok || submitting || !!active} onClick={onProcess}>
          {submitting ? <LoaderCircle className="size-4 animate-spin" /> : <Play className="size-4" />}
          Process selected items{selectedCount > 0 ? ` (${selectedCount})` : ""}
        </button>
        {!canProcess.ok && canProcess.reason && <span className="text-xs text-zinc-500">{canProcess.reason}</span>}
        {active && <span className="text-xs text-zinc-500">A job is running. You can start a new one when it finishes.</span>}
      </div>

      {jobError && (
        <div className="mt-4">
          <Note tone="error">{jobError}</Note>
        </div>
      )}

      {job && (
        <ul className="mt-5 space-y-3">
          {job.tasks.map((task) => (
            <TaskRow key={task.kind} task={task} onCancel={() => onCancel(task.kind)} />
          ))}
        </ul>
      )}
    </section>
  );
}

function TaskRow({ task, onCancel }: { task: JobTask; onCancel: () => void }) {
  const meta = OUTPUT_META[task.kind];
  const Icon = meta.icon;
  const running = task.status === "running" || task.status === "queued";
  const StatusIcon =
    task.status === "completed" ? CircleCheck : task.status === "failed" ? CircleX : task.status === "cancelled" ? Ban : LoaderCircle;
  return (
    <li className="rounded-xl border border-white/[0.07] bg-ink-850 p-4">
      <div className="flex items-center gap-3">
        <span className={cn("grid size-8 shrink-0 place-items-center rounded-lg bg-white/[0.04]", meta.tint)}>
          <Icon className="size-4" />
        </span>
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span className="text-sm font-medium text-zinc-100">{meta.title}</span>
            <span
              className={cn(
                "inline-flex items-center gap-1 text-xs",
                task.status === "completed" && "text-emerald-400",
                task.status === "failed" && "text-red-400",
                task.status === "cancelled" && "text-zinc-500",
                running && "text-zinc-400",
              )}
            >
              <StatusIcon className={cn("size-3.5", task.status === "running" && "animate-spin")} />
              {STATUS_LABEL[task.status]}
              {task.status === "running" && ` · ${Math.round(task.progress * 100)}%`}
            </span>
          </div>
          {running && (
            <p className="mt-0.5 truncate text-xs text-zinc-500" title={task.stage}>
              {task.stage}
            </p>
          )}
        </div>
        {running && (
          <button type="button" onClick={onCancel} className="rounded-md p-1.5 text-zinc-500 hover:bg-white/5 hover:text-red-300" aria-label={`Cancel ${meta.title}`} title="Cancel">
            <X className="size-4" />
          </button>
        )}
      </div>
      <div className="mt-3">
        <ProgressBar value={task.status === "completed" ? 1 : task.progress} status={task.status} />
      </div>
      {task.error && (
        <div className="mt-3 flex items-start gap-2 text-xs text-red-300">
          <CircleAlert className="mt-0.5 size-3.5 shrink-0" />
          <span>{task.error.message}</span>
        </div>
      )}
      {task.notes.length > 0 && (
        <ul className="mt-2 space-y-1">
          {task.notes.map((n, i) => (
            <li key={i} className="text-xs leading-relaxed text-zinc-500">
              {n}
            </li>
          ))}
        </ul>
      )}
    </li>
  );
}
