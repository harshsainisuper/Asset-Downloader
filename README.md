# YT Media Toolkit

A self-hosted web app that takes a YouTube video or Shorts URL and produces exactly the outputs you select:

| Output | What you get |
|---|---|
| **Video** | MP4 or WebM at "Best available" or a specific 2160p / 1440p / 1080p / 720p / 480p / 360p tier. Only tiers that exist are selectable. Separate video and audio streams are merged with FFmpeg. |
| **Audio only** | MP3 (128 to 320 kbps), M4A (keeps the original AAC stream when possible) or WAV (PCM 16-bit). |
| **Thumbnail** | The largest thumbnail that actually exists, verified by decoding it. Output as JPG or the original format (JPG/WebP). |
| **Transcript** | Creator captions or YouTube's automatic captions as TXT, SRT and/or VTT, with a language picker. Optional, explicit AI fallback (Whisper-compatible) when no captions exist. |

Outputs are independent: a job only ever runs the processors you ticked. Each output shows its own progress, can be cancelled, and is downloadable as soon as it finishes. You can also download all files at once or as one ZIP.

---

## Architecture

```
┌──────────── Browser (React + TS + Vite + Tailwind) ─────────────┐
│ URL pre-check → /api/info → output cards → /api/jobs → polling  │
│ downloads via short-lived HMAC-signed links (streamed, Range OK)│
└──────────────────────────────┬──────────────────────────────────┘
                               │ same-origin /api (Vite proxy in dev)
┌──────────────────────────────▼──────────────────────────────────┐
│ FastAPI                                                         │
│  url_validation  → strict allowlist, ID extraction, canonical URL│
│  JobManager      → in-memory jobs, per-task asyncio workers,    │
│                    concurrency/time limits, retention cleanup   │
│  processors      → video | audio | thumbnail | transcript       │
│  SourceCache     → each source stream downloaded once per job   │
│  format_selection→ pure planning: which streams, copy/transcode │
│  ffmpeg          → argument-array subprocesses + progress       │
│  transcripts/    → parsers (json3/vtt/srt/srv3), writers, STT   │
│  MediaProvider   → interface; YtDlpProvider is one implementation│
└─────────────────────────────────────────────────────────────────┘
```

**Choice of YouTube integration.** YouTube has no official API for downloading media. [yt-dlp](https://github.com/yt-dlp/yt-dlp) is the most actively maintained open-source option. It is used **only** behind `app/media/provider.py::MediaProvider`, so it can be replaced with an authorized provider by implementing three methods (`fetch_info`, `download_format`, `download_captions`) without touching the job, FFmpeg or transcript code. yt-dlp breaks whenever YouTube changes, so keep it updated (see Troubleshooting).

**Shared work.** When several outputs need the same stream, it is fetched once. For example, MP4 video + M4A audio + AI transcript all reuse the single AAC audio download.

### Project layout

One repository holds everything. Vercel deploys the frontend from it, and a container
host builds the backend from the same repo root:

```
.
├── vercel.json                  Vercel: builds frontend/, serves frontend/dist, security headers
├── .vercelignore                keeps backend/ out of Vercel uploads
├── Dockerfile / .dockerignore   backend API image (FFmpeg + Deno + yt-dlp)
├── render.yaml                  optional Render blueprint for the backend
├── .env.example                 configuration template (no secrets)
├── scripts/dev.sh               start backend + frontend dev servers
├── backend/
│   ├── requirements.txt         runtime deps   (requirements-dev.txt: tests/lint)
│   ├── requirements-stt-local.txt  optional local Whisper (faster-whisper)
│   ├── app/
│   │   ├── main.py              app factory, error handlers, static frontend
│   │   ├── config.py            settings (env / .env)
│   │   ├── url_validation.py    YouTube URL allowlist + normalization
│   │   ├── security.py          job tokens, signed URLs, rate limit, body limit
│   │   ├── schemas.py           strict request models (output selection)
│   │   ├── errors.py            safe user-facing errors + provider error mapping
│   │   ├── filenames.py         safe download names
│   │   ├── api/                 routes + serializers
│   │   ├── jobs/                manager, per-output processors, shared sources
│   │   ├── media/               provider interface, yt-dlp provider, format
│   │   │                        selection, FFmpeg, thumbnails
│   │   └── transcripts/         caption parsing, TXT/SRT/VTT writers, STT
│   └── tests/                   pytest suite (real FFmpeg, no network needed)
└── frontend/
    ├── package.json
    └── src/
        ├── App.tsx              state, polling, downloads
        ├── components/          Header, UrlPanel, OutputSelector, ProcessPanel,
        │                        ResultsPanel, SettingsModal
        └── lib/                 API client, URL pre-check, selection logic (+ tests)
```

---

## Requirements

- **Python 3.11+**
- **Node.js 22.12+** (frontend tooling)
- **FFmpeg + FFprobe** with libx264, libmp3lame, libopus and libvpx (standard builds include them)
- The **Deno** JavaScript runtime that yt-dlp needs for YouTube is installed automatically by `requirements.txt` (`yt-dlp[default,deno]`). Node 22+ is used as a fallback.

### Installing FFmpeg

| OS | Command |
|---|---|
| macOS (Homebrew) | `brew install ffmpeg` |
| Ubuntu / Debian | `sudo apt update && sudo apt install ffmpeg` |
| Fedora | `sudo dnf install ffmpeg` (enable RPM Fusion first) |
| Windows | `winget install Gyan.FFmpeg` (or download from <https://www.gyan.dev/ffmpeg/builds/> and add `bin` to PATH) |

Verify with `ffmpeg -version` and `ffprobe -version`. If FFmpeg isn't on PATH, set `FFMPEG_PATH` and `FFPROBE_PATH` in `.env`.

---

## Setup & launch (local)

From the project root:

```bash
# 1. Configuration
cp .env.example .env
#    then set SECRET_KEY (python -c "import secrets; print(secrets.token_urlsafe(32))")

# 2. Backend
cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt        # Windows: .venv\Scripts\pip install -r requirements-dev.txt
cd ..

# 3. Frontend
cd frontend
npm install
cd ..
```

### Development (two processes, hot reload)

```bash
# terminal 1 — API on http://127.0.0.1:8000
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# terminal 2 — UI on http://127.0.0.1:5173 (proxies /api to the backend)
cd frontend && npm run dev
```

Open **<http://127.0.0.1:5173>**. Alternatively, `./scripts/dev.sh` starts both.

### Single-process mode (built UI served by the API)

```bash
cd frontend && npm run build && cd ..
cd backend && .venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open **<http://127.0.0.1:8000>**.

> Run **one** uvicorn worker. Jobs are held in memory, so multiple workers would not share them. To expose the app beyond localhost, put it behind a reverse proxy with TLS and authentication, set `APP_ENV=production`, and set `TRUST_PROXY_HEADERS=true` only if the proxy sets `X-Forwarded-For`.

### Tests and checks

```bash
cd backend  && .venv/bin/python -m pytest          # 166 tests: URLs, selection, formats, jobs, errors, FFmpeg, CORS
cd backend  && .venv/bin/ruff check app tests
cd frontend && npm test && npm run build           # vitest + type-check + production build
```

Backend tests need no network. They use a fake provider that serves FFmpeg-generated media through the real `MediaProvider` interface, so merging and conversion are exercised for real. `tests/test_ytdlp_provider.py` drives the real yt-dlp download path against a local HTTP server.

---

## Deploying: Vercel (frontend) + container host (backend)

Vercel serves the UI. The API runs as a Docker container elsewhere, because Vercel Functions can't run this backend:

- Responses are capped at 4.5 MB, so media files can't be downloaded through a function.
- A request may run 300 s (Hobby) or 800 s (Pro), and there are no background workers or shared in-memory state between requests.
- FFmpeg isn't available in the function runtime.

The browser calls the backend directly (`VITE_API_BASE_URL`), and downloads stream straight from it. Neither goes through Vercel, so there are no Vercel bandwidth or size limits on files.

```
Browser ──▶ https://your-app.vercel.app        (static UI from frontend/dist)
   └──────▶ https://your-api.onrender.com/api   (FastAPI container: jobs, FFmpeg, downloads)
```

### 1. Push the repository to GitHub

Both platforms deploy from the **repository root**. Don't set a "Root Directory".

### 2. Deploy the backend (Render shown; Railway, Fly.io or any Docker host work the same way)

**Render, with the included blueprint:** Dashboard → **New → Blueprint** → select the repo. This uses `render.yaml`, builds the root `Dockerfile`, and generates `SECRET_KEY`.
**Render, manually:** **New → Web Service** → the repo → Runtime **Docker** → health check path `/api/health`.

Backend environment variables:

| Variable | Value |
|---|---|
| `SECRET_KEY` | long random string (the blueprint generates one) |
| `CORS_ORIGINS` | your Vercel URL, e.g. `https://yt-media-toolkit.vercel.app`. Set after step 3 and redeploy. |
| `CORS_ORIGIN_REGEX` | optional, for preview deployments: `^https://yt-media-toolkit(-[a-z0-9-]+)?\.vercel\.app$` |
| `TRUST_PROXY_HEADERS` | `true` on Render, Railway or Fly.io (they set `X-Forwarded-For`). `false` if the container is exposed directly. |
| `STT_*` | optional, see Speech-to-text |

`PORT` is read automatically. Note the service's **https** URL, e.g. `https://yt-media-toolkit-api.onrender.com`. Check it with `curl https://…/api/health`.

Other hosts: `docker build -t ytmt-api .` then `docker run -p 8000:8000 -e SECRET_KEY=… -e CORS_ORIGINS=https://your-app.vercel.app ytmt-api`. Put HTTPS in front of it, because browsers block `http://` API calls from an `https://` page.

> Run a **single instance with a single worker** (the Dockerfile does this). Jobs live in memory, so don't scale horizontally. Render's free plan sleeps after 15 idle minutes and has 512 MB RAM, which is fine for trying it out. Use a paid plan for regular use and 4K/H.264 re-encodes.

### 3. Deploy the frontend on Vercel

1. Vercel → **Add New → Project** → import the repo. Leave Root Directory as the repo root. `vercel.json` already sets install, build and output (`frontend/dist`).
2. **Settings → Environment Variables:** `VITE_API_BASE_URL` = the backend URL from step 2, with no trailing `/api`. Enable it for Production (and Preview if you use previews).
3. Deploy. The build **fails on purpose** with a clear message if `VITE_API_BASE_URL` is missing or isn't `https://`. Changing it later needs a redeploy, because it's baked in at build time.
4. Add the Vercel URL to the backend's `CORS_ORIGINS` and redeploy the backend.

Open the Vercel URL. **Settings → Server status** should show FFmpeg, yt-dlp and the Deno runtime as available.

### YouTube and cloud IPs

YouTube often answers requests from datacenter IP ranges (Render, Railway, AWS, …) with "Sign in to confirm you're not a bot". The app reports this as `provider_blocked`. Thumbnails usually still work. For reliable video, audio and caption downloads, run the backend container from a residential connection, e.g. a home server or NAS exposed via Cloudflare Tunnel or Tailscale Funnel, and keep the UI on Vercel. Cookie or account-based bypasses are intentionally not implemented.

---

## Speech-to-text (AI transcript fallback)

The fallback is **off by default** and only runs when (a) no usable captions exist, or they cannot be retrieved, **and** (b) the user ticks "generate an AI transcript". AI output is labelled everywhere: the result badge, a `[… AI transcript]` filename suffix, and a `Source: AI-generated transcript (model: …)` header in TXT/VTT. Empty or failed transcriptions are reported as failures; nothing is ever invented.

**Option A: OpenAI-compatible API** (OpenAI Whisper, Groq, a self-hosted `speaches`/faster-whisper-server, LocalAI, …):

```ini
STT_PROVIDER=openai_compatible
STT_API_BASE_URL=https://api.openai.com/v1     # or e.g. http://localhost:8001/v1
STT_API_KEY=sk-...                              # server-side only, never sent to the browser
STT_MODEL=whisper-1                             # must support response_format=verbose_json for timestamps
STT_CHUNK_SECONDS=600                           # audio is split into 16 kHz mono MP3 chunks (~3.5 MB each)
```

Models that return no segment timestamps (e.g. some `gpt-4o-*-transcribe` models) still work, but SRT/VTT cues are then chunk-length.

**Option B: local faster-whisper** (no API cost; CPU works, a GPU is much faster):

```bash
cd backend && .venv/bin/pip install -r requirements-stt-local.txt
```
```ini
STT_PROVIDER=faster_whisper
STT_LOCAL_MODEL=small        # tiny | base | small | medium | large-v3 (downloaded on first use)
STT_LOCAL_DEVICE=auto        # cpu | cuda | auto
```

Check the status under **Settings → Server status**.

---

## Behaviour details & limitations

- **Supported URLs:** `youtube.com/watch?v=`, `m.`/`music.` subdomains, `youtu.be/`, `/shorts/`, `/embed/`, `/live/` (finished streams), `youtube-nocookie.com/embed/`. Playlists, channels and other domains are rejected.
- **Not supported, by design:** private, members-only, Premium-only, age-restricted (sign-in required), DRM-protected (purchased/rented) videos, active live streams and upcoming premieres. The app uses no cookies or accounts and never circumvents access restrictions. These cases produce clear error messages.
- **Video quality:** YouTube labels quality by the short side (a vertical 1080×1920 Short is "1080p"). Above 1080p, YouTube usually offers only VP9/AV1:
  - **WebM** keeps the original VP9/AV1 + Opus streams (no re-encoding).
  - **MP4** uses H.264 when available. If only AV1 exists, it is stored in MP4 without re-encoding and labelled "AV1". If only VP9 exists, it is **re-encoded to H.264** (slower, clearly flagged). "H.264 compatibility mode" forces re-encoding of AV1 too. Output files are probed after processing, and the label shows the real codecs. Nothing is relabelled.
  - WebM is disabled for tiers that only have H.264.
  - HDR is used only when no SDR stream exists at that tier. Re-encodes are SDR yuv420p.
- **Audio quality:** YouTube audio is lossy (AAC ~128 kbps or Opus ~130–160 kbps). M4A copies AAC losslessly. MP3 is a transcode. WAV is a decode to PCM and **does not restore lost detail**, nor does a higher MP3 bitrate. The UI says so.
- **Thumbnails:** `maxresdefault`, `hq720`, `sddefault`, `hqdefault`, … in JPG and WebP, plus any variants yt-dlp reports, are fetched from `i.ytimg.com` and decoded. YouTube's 120×90 "missing" placeholder is ignored. Many videos have no 1280×720 image; you get the largest that really exists.
- **Captions:** creator captions in every language, plus the original-language automatic captions. YouTube's ~100 machine-translated tracks are deliberately hidden. Timing and segmentation are preserved in SRT/VTT. Rolling auto-caption duplicates are removed.
- **Limits** (configurable): max video duration 4 h, max file 8 GB, 2 concurrent jobs, 3 active jobs per client, 3 h per job, and files deleted 60 min after a job finishes.
- **Persistence:** jobs live in memory. A server restart drops them (temp files are cleaned at startup), and the UI tells you to re-run.

## Security notes

- URLs are validated and normalized server-side. Only the 11-character video ID is kept, and yt-dlp only ever receives `https://www.youtube.com/watch?v=<id>` with its extractor restricted to `youtube`. Thumbnails are fetched only from `https://i*.ytimg.com` without following redirects. Together these block SSRF.
- No shell is used anywhere. FFmpeg and FFprobe run with argument arrays.
- Each job returns a random access token. Job status, cancel, ZIP and delete require it via the `X-Job-Token` header. Missing jobs and wrong tokens give the same 404.
- Files are only reachable through HMAC-signed, expiring links that reference random file IDs mapped server-side. Paths are re-checked to stay inside the job directory. Inline previews are served with `nosniff` and a sandboxing CSP.
- Request bodies are capped at 64 KB, with per-IP rate limits, concurrency limits, timeouts and strict schemas (unknown fields are rejected).
- Errors return stable codes and plain messages. Stack traces and raw tool output only go to server logs.
- Secrets (`SECRET_KEY`, `STT_API_KEY`) stay on the server.

## Troubleshooting

| Symptom | Fix |
|---|---|
| "YouTube could not provide this media" or many formats missing | Update yt-dlp: `backend/.venv/bin/pip install -U "yt-dlp[default]"`, then restart. YouTube changes often. |
| "YouTube temporarily refused the request (bot check or rate limit)" | Common from cloud/datacenter IPs. Run from a residential network, wait, or reduce traffic. Cookie or account-based bypasses are intentionally not implemented. |
| Settings shows "JavaScript runtime: None found" | Install Deno or Node 22+ and make sure it is on the backend's PATH. `YTDLP_JS_RUNTIMES` controls which runtimes are tried. |
| "FFmpeg is not installed on the server" | Install FFmpeg (see above) or set `FFMPEG_PATH`/`FFPROBE_PATH`. |
| UI says "Cannot reach the server" | Locally: start the backend on port 8000, or set `VITE_API_PROXY_TARGET` before `npm run dev`. On Vercel: check `VITE_API_BASE_URL` (then redeploy) and that the backend's `CORS_ORIGINS` contains the exact Vercel URL (scheme, no trailing slash). The browser console shows CORS errors. |
| Vercel build fails: "VITE_API_BASE_URL must be set" | Add the variable in Vercel → Settings → Environment Variables (https URL of the backend) and redeploy. |
| First request on Render is very slow | The free plan sleeps when idle. The first request wakes it (~30–60 s). |
| Transcript fails with "AI transcription is not configured" | Configure `STT_PROVIDER` (see Speech-to-text) and restart. |
| STT "rejected the API key" / HTTP 413 | Check `STT_API_KEY`, or lower `STT_CHUNK_SECONDS` for servers with smaller upload limits. |
| 4K/1440p MP4 is slow | That tier needs an H.264 re-encode. Choose WebM, or set a faster `X264_PRESET` (e.g. `ultrafast`). |
| "This job no longer exists" | The job expired (`RETENTION_MINUTES`) or the server restarted. Process again. |
| Download links return 403 | Links expire after `DOWNLOAD_LINK_TTL_MINUTES`, or the server restarted without a fixed `SECRET_KEY`. Refresh the page and re-run. |
| Debugging | `LOG_LEVEL=DEBUG` logs yt-dlp and FFmpeg details server-side. |

## Legal

This tool is for content you own, have licensed, or may otherwise lawfully download (e.g. Creative Commons or your own uploads). Downloading may violate YouTube's Terms of Service and copyright law in other cases; you are responsible for how you use it. The app does not bypass DRM, paywalls, sign-in requirements or private-content restrictions.
