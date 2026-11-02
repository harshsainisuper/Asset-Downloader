import type { Job, JobFile, OutputKind, ServerConfig, VideoInfo } from "./types";

/**
 * Backend origin. Empty when the UI is served by the backend itself (Docker/Render, local
 * single-process mode) or via the Vite dev proxy. Set VITE_API_BASE_URL only if the UI is
 * hosted on a different origin than the API.
 */
export const API_BASE = (import.meta.env.VITE_API_BASE_URL ?? "").trim().replace(/\/+$/, "");

/** Resolve a server-relative path (such as a signed download link) against the backend origin. */
export function apiUrl(path: string): string {
  return path.startsWith("/") ? `${API_BASE}${path}` : path;
}

function withAbsoluteUrls(file: JobFile): JobFile {
  return {
    ...file,
    download_url: apiUrl(file.download_url),
    preview_url: file.preview_url ? apiUrl(file.preview_url) : null,
  };
}

function normalizeJob(job: Job): Job {
  return { ...job, tasks: job.tasks.map((t) => ({ ...t, files: t.files.map(withAbsoluteUrls) })) };
}

export class ApiError extends Error {
  constructor(
    public code: string,
    message: string,
    public status: number,
  ) {
    super(message);
  }
}

/** A 404 without the backend's JSON error body means the request never reached the API. */
function noBackendMessage(): string {
  if (!API_BASE) {
    return (
      "This site is not connected to the download server. If it is hosted on Vercel, set VITE_API_BASE_URL " +
      "to your backend address (e.g. https://your-service.onrender.com) and redeploy."
    );
  }
  return `${API_BASE} did not answer like the download server. Check that VITE_API_BASE_URL is the backend's address, without /api at the end.`;
}

async function request<T>(path: string, init: RequestInit = {}, token?: string): Promise<T> {
  const headers = new Headers(init.headers);
  if (init.body) headers.set("Content-Type", "application/json");
  if (token) headers.set("X-Job-Token", token);
  let resp: Response;
  try {
    resp = await fetch(apiUrl(path), { ...init, headers });
  } catch {
    throw new ApiError(
      "network_error",
      "Cannot reach the server. Check that the backend is running and that CORS_ORIGINS allows this site.",
      0,
    );
  }
  if (resp.status === 204) return undefined as T;
  let body: unknown = null;
  try {
    body = await resp.json();
  } catch {
    /* non-JSON response */
  }
  if (!resp.ok) {
    const err = (body as { error?: { code?: string; message?: string } } | null)?.error;
    if (!err && resp.status === 404) throw new ApiError("no_backend", noBackendMessage(), 404);
    throw new ApiError(err?.code ?? "http_error", err?.message ?? `Request failed (HTTP ${resp.status}).`, resp.status);
  }
  return body as T;
}

export const api = {
  config: () => request<ServerConfig>("/api/config"),
  info: (url: string, signal?: AbortSignal) =>
    request<VideoInfo>("/api/info", { method: "POST", body: JSON.stringify({ url }), signal }),
  createJob: async (url: string, outputs: Record<string, unknown>) => {
    const created = await request<{ job_id: string; token: string; job: Job }>("/api/jobs", {
      method: "POST",
      body: JSON.stringify({ url, outputs }),
    });
    return { ...created, job: normalizeJob(created.job) };
  },
  job: async (jobId: string, token: string) =>
    normalizeJob(await request<Job>(`/api/jobs/${encodeURIComponent(jobId)}`, {}, token)),
  cancel: (jobId: string, token: string, task?: OutputKind) =>
    request<{ cancelled: number }>(
      `/api/jobs/${encodeURIComponent(jobId)}/cancel`,
      { method: "POST", body: JSON.stringify(task ? { task } : {}) },
      token,
    ),
  zip: async (jobId: string, token: string, fileIds?: string[]) => {
    const { file } = await request<{ file: JobFile }>(
      `/api/jobs/${encodeURIComponent(jobId)}/zip`,
      { method: "POST", body: JSON.stringify(fileIds ? { file_ids: fileIds } : {}) },
      token,
    );
    return { file: withAbsoluteUrls(file) };
  },
  remove: (jobId: string, token: string) =>
    request<void>(`/api/jobs/${encodeURIComponent(jobId)}`, { method: "DELETE" }, token),
};
