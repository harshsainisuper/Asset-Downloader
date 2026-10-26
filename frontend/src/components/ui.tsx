import type { ReactNode } from "react";
import { AudioLines, Captions, Film, ImageIcon, type LucideIcon } from "lucide-react";
import type { OutputKind } from "../lib/types";

export function cn(...parts: (string | false | null | undefined)[]): string {
  return parts.filter(Boolean).join(" ");
}

export const OUTPUT_META: Record<OutputKind, { title: string; description: string; icon: LucideIcon; tint: string }> = {
  video: { title: "Video", description: "MP4 or WebM, up to the best available quality", icon: Film, tint: "text-sky-400" },
  audio: { title: "Audio only", description: "MP3, M4A or WAV extracted from the best stream", icon: AudioLines, tint: "text-emerald-400" },
  thumbnail: { title: "Thumbnail", description: "Highest-resolution image actually available", icon: ImageIcon, tint: "text-amber-400" },
  transcript: { title: "Transcript", description: "Captions as TXT, SRT or VTT, with AI fallback", icon: Captions, tint: "text-violet-400" },
};

export function SectionTitle({ step, title, right }: { step: number; title: string; right?: ReactNode }) {
  return (
    <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
      <h2 className="flex items-center gap-3 text-base font-semibold text-zinc-100">
        <span className="grid size-6 place-items-center rounded-full border border-white/10 bg-ink-800 text-xs text-zinc-400">
          {step}
        </span>
        {title}
      </h2>
      {right}
    </div>
  );
}

export function Chip<T extends string | number>({
  value,
  current,
  onSelect,
  children,
  disabled,
  title,
}: {
  value: T;
  current: T | T[];
  onSelect: (value: T) => void;
  children: ReactNode;
  disabled?: boolean;
  title?: string;
}) {
  const active = Array.isArray(current) ? current.includes(value) : current === value;
  return (
    <button
      type="button"
      role={Array.isArray(current) ? "checkbox" : "radio"}
      aria-checked={active}
      title={title}
      disabled={disabled}
      onClick={() => onSelect(value)}
      className={cn(active ? "chip-on" : "chip-off", disabled && "cursor-not-allowed opacity-40")}
    >
      {children}
    </button>
  );
}

export function Note({ children, tone = "muted" }: { children: ReactNode; tone?: "muted" | "warn" | "error" | "info" }) {
  const tones = {
    muted: "border-white/5 bg-white/[0.02] text-zinc-400",
    info: "border-sky-500/20 bg-sky-500/5 text-sky-200/90",
    warn: "border-amber-500/20 bg-amber-500/5 text-amber-200/90",
    error: "border-red-500/25 bg-red-500/5 text-red-200",
  };
  return <div className={cn("rounded-lg border px-3 py-2 text-xs leading-relaxed", tones[tone])}>{children}</div>;
}

export function ProgressBar({ value, status }: { value: number; status: string }) {
  const color =
    status === "failed"
      ? "bg-red-500"
      : status === "cancelled"
        ? "bg-zinc-600"
        : status === "completed"
          ? "bg-emerald-500"
          : "bg-accent-500";
  return (
    <div
      className="h-1.5 w-full overflow-hidden rounded-full bg-white/5"
      role="progressbar"
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(value * 100)}
    >
      <div className={cn("h-full rounded-full transition-[width] duration-500", color)} style={{ width: `${Math.max(2, value * 100)}%` }} />
    </div>
  );
}
