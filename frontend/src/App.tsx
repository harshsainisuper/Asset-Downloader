import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Header } from "./components/Header";
import { OutputSelector } from "./components/OutputSelector";
import { ProcessPanel } from "./components/ProcessPanel";
import { ResultsPanel } from "./components/ResultsPanel";
import { SettingsModal } from "./components/SettingsModal";
import { UrlPanel } from "./components/UrlPanel";
import { ApiError, api } from "./lib/api";
import { loadDefaults, saveDefaults } from "./lib/prefs";
import { ALL_SELECTED, EMPTY_SELECTION, adaptSettings, buildOutputs, canProcess, selectedKinds } from "./lib/selection";
import type { Job, JobFile, OutputKind, OutputSettings, Selection, ServerConfig, VideoInfo } from "./lib/types";
import { checkYoutubeUrl } from "./lib/youtube";

const POLL_MS = 800;

function errorMessage(err: unknown): string {
  if (err instanceof ApiError) return err.message;
  return "Something went wrong. Please try again.";
}

function triggerDownload(href: string, name?: string) {
  const a = document.createElement("a");
  a.href = href;
  if (name) a.download = name;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

export default function App() {
  const [url, setUrl] = useState("");
  const [info, setInfo] = useState<VideoInfo | null>(null);
  const [infoUrl, setInfoUrl] = useState<string | null>(null);
  const [infoLoading, setInfoLoading] = useState(false);
  const [infoError, setInfoError] = useState<string | null>(null);

  const [defaults, setDefaults] = useState<OutputSettings>(() => loadDefaults());
  const [settings, setSettings] = useState<OutputSettings>(defaults);
  const [selection, setSelection] = useState<Selection>(EMPTY_SELECTION);

  const [job, setJob] = useState<Job | null>(null);
  const [jobToken, setJobToken] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [jobError, setJobError] = useState<string | null>(null);
  const [zipping, setZipping] = useState(false);
  const [zipError, setZipError] = useState<string | null>(null);

  const [settingsOpen, setSettingsOpen] = useState(false);
  const [config, setConfig] = useState<ServerConfig | null>(null);
  const [configError, setConfigError] = useState<string | null>(null);
  const infoAbort = useRef<AbortController | null>(null);

  const urlCheck = useMemo(() => checkYoutubeUrl(url), [url]);
  // Info is only valid for the URL it was fetched for.
  const currentInfo = info && infoUrl === url.trim() ? info : null;

  useEffect(() => {
    api.config().then(setConfig, (e) => setConfigError(errorMessage(e)));
  }, []);

  const fetchInfo = useCallback(async () => {
    if (!urlCheck.ok) return;
    infoAbort.current?.abort();
    const controller = new AbortController();
    infoAbort.current = controller;
    setInfoLoading(true);
    setInfoError(null);
    setInfo(null);
    try {
      const data = await api.info(url.trim(), controller.signal);
      setInfo(data);
      setInfoUrl(url.trim());
      setSettings((s) => adaptSettings(s, data));
    } catch (e) {
      if (!controller.signal.aborted) setInfoError(errorMessage(e));
    } finally {
      if (infoAbort.current === controller) setInfoLoading(false);
    }
  }, [url, urlCheck.ok]);

  // Poll the active job until every task reaches a final state.
  useEffect(() => {
    if (!job || !jobToken || (job.status !== "queued" && job.status !== "running")) return;
    let stopped = false;
    const timer = setTimeout(async () => {
      try {
        const next = await api.job(job.job_id, jobToken);
        if (!stopped) setJob(next);
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status === 404) {
          setJobError("This job no longer exists on the server (it expired or the server restarted). Please process again.");
          setJob((j) => (j ? { ...j, status: "failed" } : j));
        } else {
          // Transient error: keep polling by nudging state.
          setJob((j) => (j ? { ...j } : j));
        }
      }
    }, POLL_MS);
    return () => {
      stopped = true;
      clearTimeout(timer);
    };
  }, [job, jobToken]);

  const processCheck = canProcess(currentInfo, selection, settings, urlCheck.ok);

  const process = async () => {
    if (!processCheck.ok) return;
    setSubmitting(true);
    setJobError(null);
    setZipError(null);
    try {
      const created = await api.createJob(url.trim(), buildOutputs(selection, settings));
      setJobToken(created.token);
      setJob(created.job);
    } catch (e) {
      setJobError(errorMessage(e));
    } finally {
      setSubmitting(false);
    }
  };

  const cancel = async (task?: OutputKind) => {
    if (!job || !jobToken) return;
    try {
      await api.cancel(job.job_id, jobToken, task);
    } catch (e) {
      setJobError(errorMessage(e));
    }
  };

  const downloadAll = (files: JobFile[]) => {
    // Stagger clicks so browsers don't drop simultaneous downloads.
    files.forEach((f, i) => setTimeout(() => triggerDownload(f.download_url, f.name), i * 600));
  };

  const zip = async () => {
    if (!job || !jobToken) return;
    setZipping(true);
    setZipError(null);
    try {
      const { file } = await api.zip(job.job_id, jobToken);
      triggerDownload(file.download_url, file.name);
    } catch (e) {
      setZipError(errorMessage(e));
    } finally {
      setZipping(false);
    }
  };

  const busy = !!job && (job.status === "queued" || job.status === "running");

  return (
    <div className="min-h-screen">
      <Header onOpenSettings={() => setSettingsOpen(true)} />
      <main className="mx-auto max-w-5xl space-y-5 px-4 py-6 sm:px-6 sm:py-8">
        <UrlPanel
          url={url}
          onUrlChange={setUrl}
          onFetch={fetchInfo}
          loading={infoLoading}
          error={infoError}
          info={currentInfo}
        />
        <OutputSelector
          info={currentInfo}
          selection={selection}
          onToggle={(kind) => setSelection((s) => ({ ...s, [kind]: !s[kind] }))}
          onSelectAll={() => setSelection(ALL_SELECTED)}
          onDeselectAll={() => setSelection(EMPTY_SELECTION)}
          settings={settings}
          onSettingsChange={setSettings}
          disabled={!currentInfo || busy}
        />
        <ProcessPanel
          canProcess={processCheck}
          selectedCount={selectedKinds(selection).length}
          onProcess={process}
          submitting={submitting}
          job={job}
          jobError={jobError}
          onCancel={cancel}
        />
        {job && (
          <ResultsPanel
            job={job}
            onDownloadAll={downloadAll}
            onZip={zip}
            zipping={zipping}
            zipError={zipError}
            retentionMinutes={config?.limits.retention_minutes ?? null}
          />
        )}
        <footer className="px-1 pt-2 pb-8 text-xs leading-relaxed text-zinc-600">
          Only download content you own or have permission to use, and follow YouTube's Terms of Service and copyright
          law. Private, members-only, age-restricted, paid and DRM-protected videos are not supported.
        </footer>
      </main>
      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        config={config}
        configError={configError}
        defaults={defaults}
        onSaveDefaults={(s) => {
          saveDefaults(s);
          setDefaults(s);
          setSettings((cur) => (currentInfo ? adaptSettings({ ...s, transcript: { ...s.transcript, track_id: cur.transcript.track_id } }, currentInfo) : s));
        }}
      />
    </div>
  );
}
