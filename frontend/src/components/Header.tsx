import { Settings } from "lucide-react";

export function Logo() {
  return (
    <svg viewBox="0 0 32 32" className="size-9 shrink-0" aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="#1f2025" />
      <rect x="6" y="9" width="20" height="14" rx="4" fill="#ef4444" />
      <path d="M14 12.5v7l6-3.5z" fill="#fff" />
    </svg>
  );
}

export function Header({ onOpenSettings }: { onOpenSettings: () => void }) {
  return (
    <header className="border-b border-white/[0.06] bg-ink-950/80 backdrop-blur">
      <div className="mx-auto flex max-w-5xl items-center justify-between gap-4 px-4 py-4 sm:px-6">
        <div className="flex min-w-0 items-center gap-3">
          <Logo />
          <div className="min-w-0">
            <h1 className="text-base leading-tight font-semibold tracking-tight text-zinc-50">YT Media Toolkit</h1>
            <p className="truncate text-xs text-zinc-500">
              Download video, audio, thumbnails and transcripts from YouTube and Instagram
            </p>
          </div>
        </div>
        <button type="button" className="btn-ghost" onClick={onOpenSettings} aria-label="Settings">
          <Settings className="size-4" />
          <span className="hidden sm:inline">Settings</span>
        </button>
      </div>
    </header>
  );
}
