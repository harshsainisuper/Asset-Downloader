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

One repository holds everything. The Dockerfile builds the UI and the API into a single
image, deployed as one Render web service:

```
.
├── Dockerfile / .dockerignore   one image: builds the UI (Node) + API (FFmpeg, Deno, yt-dlp)
├── render.yaml                  Render blueprint: a single web service
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
cd backend  && .venv/bin/python -m pytest          # 167 tests: URLs, selection, formats, jobs, errors, FFmpeg, CORS
cd backend  && .venv/bin/ruff check app tests
cd frontend && npm test && npm run build           # vitest + type-check + production build
```

Backend tests need no network. They use a fake provider that serves FFmpeg-generated media through the real `MediaProvider` interface, so merging and conversion are exercised for real. `tests/test_ytdlp_provider.py` drives the real yt-dlp download path against a local HTTP server.

---

## Deploying on Render (one service)

A single Render **web service** runs everything. The multi-stage `Dockerfile` builds the React UI with Node, then the Python image serves it together with the API on the same URL. No CORS settings, no second service, and no frontend environment variables are needed.

```
Browser ──▶ https://yt-media-toolkit.onrender.com
              ├── /        UI (built frontend/dist)
              └── /api/…   API: jobs, FFmpeg, signed downloads
```

### Steps

1. Push this repository to GitHub.
2. Render dashboard → **New → Blueprint** → connect the repository → **Apply**. `render.yaml` creates the `yt-media-toolkit` web service, builds the Dockerfile, generates `SECRET_KEY`, sets the health check to `/api/health`, and turns on `TRUST_PROXY_HEADERS`.
   *Manual alternative:* **New → Web Service** → the repo → Runtime **Docker** → health check path `/api/health` → add env vars `SECRET_KEY` (long random string) and `TRUST_PROXY_HEADERS=true`.
3. Wait for the first build (~5 min), then open the service URL. **Settings → Server status** should show FFmpeg, yt-dlp and the Deno runtime as available.

Every push to the deployed branch redeploys automatically (`autoDeploy: true`).

Optional environment variables (Render → service → **Environment**): `RETENTION_MINUTES`, `MAX_DURATION_SECONDS`, `MAX_FILESIZE_MB`, and the `STT_*` speech-to-text settings below. Set `STT_API_KEY` in the dashboard only; never commit it.

> Run **one instance with one worker** (the Dockerfile does this). Jobs live in memory, so don't scale horizontally. Render's **free** plan sleeps after 15 idle minutes (the first request then takes ~30–60 s and unfinished jobs are lost) and has 512 MB RAM. Use **Starter** or higher for regular use and 4K/H.264 re-encodes.

The same image runs anywhere Docker does: `docker build -t yt-media-toolkit . && docker run -p 8000:8000 -e SECRET_KEY=change-me yt-media-toolkit`, then open <http://localhost:8000>. Put HTTPS in front of it for public use.

### YouTube bot checks

On cloud servers (Render, Railway, AWS, …) YouTube often answers with "Sign in to confirm you're not a bot". It distrusts datacenter IP ranges. The app shows this as *"YouTube is blocking requests from this server's network"* (`provider_blocked`). Thumbnails usually still work. There are three remedies, in order of effort. Check which are active under **Settings → Server status**.

**1. PO-token provider (built in, on by default in Docker/Render).**
A real browser proves it is one by sending a proof-of-origin (PO) token generated by YouTube's BotGuard. The image bundles the [bgutil-ytdlp-pot-provider](https://github.com/Brainicism/bgutil-ytdlp-pot-provider) server (v2.0.2) and its yt-dlp plugin, the provider recommended in yt-dlp's [PO Token Guide](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide). The app starts the server on `127.0.0.1:4416`, restarts it if it crashes, and yt-dlp requests tokens from it automatically. Nothing to configure. Per the plugin's docs, a PO token **does not** lift IP-based sign-in checks, so on datacenter IPs you usually also need step 2.

**2. Cookies from a YouTube account (most effective on Render).**
1. Use a **secondary** Google account. YouTube may restrict accounts used for automated downloading. Never use your main account.
2. In a private/incognito window, sign in to youtube.com with that account. Export the cookies for `youtube.com` in **Netscape cookies.txt** format with a browser extension ("Get cookies.txt LOCALLY" or similar). Then close the private window without signing out, so YouTube doesn't rotate the session.
3. Render → your service → **Environment → Secret Files → Add**: filename `cookies.txt`, paste the file contents. `render.yaml` already sets `YTDLP_COOKIES_FILE=/etc/secrets/cookies.txt`. Save, which redeploys.

The cookie file never leaves the server and is not shown in the UI or API. Each download uses a private temporary copy, so the original stays untouched. Cookies expire eventually. When the bot check returns, export fresh ones. Signed-in requests can also play videos that account may watch (e.g. age-restricted ones on an age-verified account).

**3. Outbound proxy (most reliable).**
Set `YTDLP_PROXY` (Render → Environment) to route all YouTube traffic, including thumbnails and PO tokens, through a non-datacenter IP: a residential proxy service (`http://user:pass@host:port`), or your own home connection (e.g. a `socks5://` proxy reachable over Tailscale). Alternatively, run the whole container at home and expose it with Cloudflare Tunnel.

Optional: `YTDLP_PLAYER_CLIENTS` (e.g. `mweb`) overrides which YouTube client yt-dlp imitates. The PO Token Guide suggests `mweb` together with a PO-token provider if the defaults keep failing.

Also keep yt-dlp current. Each Render redeploy installs the latest `yt-dlp` allowed by `requirements.txt`, so **Manual Deploy → Clear build cache & deploy** picks up fixes.

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
- **Not supported:** private, members-only and Premium-only videos (unless the optional cookie account has access), DRM-protected (purchased/rented) videos, active live streams and upcoming premieres. Age-restricted videos need a signed-in, age-verified account via the optional cookies. The app never circumvents DRM, paywalls or private-content restrictions. These cases produce clear error messages.
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
| "YouTube is blocking requests from this server's network" | Datacenter-IP bot check. Follow **YouTube bot checks**: the PO-token provider is on by default; add cookies (Render Secret File) and/or `YTDLP_PROXY`. Check Settings → Server status. |
| Bot check returns after working for a while | The exported cookies expired or were rotated. Export fresh ones (private window) and replace the Secret File. |
| Settings shows "JavaScript runtime: None found" | Install Deno or Node 22+ and make sure it is on the backend's PATH. `YTDLP_JS_RUNTIMES` controls which runtimes are tried. |
| "FFmpeg is not installed on the server" | Install FFmpeg (see above) or set `FFMPEG_PATH`/`FFPROBE_PATH`. |
| UI says "Cannot reach the server" | Locally: start the backend on port 8000, or set `VITE_API_PROXY_TARGET` before `npm run dev`. On Render: check the service's **Logs** and **Events** for a failed deploy or crash. |
| Render build fails | Open the deploy log. Most often a transient network error during `npm ci`/`pip install`; use **Manual Deploy → Clear build cache & deploy**. |
| First request on Render is very slow | The free plan sleeps when idle. The first request wakes it (~30–60 s). |
| Transcript fails with "AI transcription is not configured" | Configure `STT_PROVIDER` (see Speech-to-text) and restart. |
| STT "rejected the API key" / HTTP 413 | Check `STT_API_KEY`, or lower `STT_CHUNK_SECONDS` for servers with smaller upload limits. |
| 4K/1440p MP4 is slow | That tier needs an H.264 re-encode. Choose WebM, or set a faster `X264_PRESET` (e.g. `ultrafast`). |
| "This job no longer exists" | The job expired (`RETENTION_MINUTES`) or the server restarted. Process again. |
| Download links return 403 | Links expire after `DOWNLOAD_LINK_TTL_MINUTES`, or the server restarted without a fixed `SECRET_KEY`. Refresh the page and re-run. |
| Debugging | `LOG_LEVEL=DEBUG` logs yt-dlp and FFmpeg details server-side. |

## Legal

This tool is for content you own, have licensed, or may otherwise lawfully download (e.g. Creative Commons or your own uploads). Downloading may violate YouTube's Terms of Service and copyright law in other cases; you are responsible for how you use it. The app does not bypass DRM, paywalls or private-content restrictions. With optional cookies it acts as that signed-in account and can only access what the account itself may watch. Automated access can lead YouTube to restrict the account, which is why a secondary account is recommended.
