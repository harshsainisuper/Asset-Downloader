# Single image serving both the UI and the API (FastAPI + yt-dlp + FFmpeg + Deno).
# Build from the repository root:
#   docker build -t yt-media-toolkit .
#   docker run -p 8000:8000 -e SECRET_KEY=change-me yt-media-toolkit   → http://localhost:8000

# bgutil PO-token provider: server image version and yt-dlp plugin version must match.
ARG BGUTIL_VERSION=2.0.2

# --- Stage 1: build the React frontend ------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: prebuilt PO-token server (only files are copied from it) -----------------
FROM brainicism/bgutil-ytdlp-pot-provider:${BGUTIL_VERSION} AS potserver

# --- Stage 3: Python API that also serves the built UI ---------------------------------
FROM python:3.13-slim
ARG BGUTIL_VERSION

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# FFmpeg (with libx264, libmp3lame, libopus, libvpx, dav1d) from Debian.
# libatomic1 is needed by the Node.js runtime of the PO-token server.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates libatomic1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt .
# Includes yt-dlp[default,deno]: the Deno runtime yt-dlp needs for YouTube,
# plus the bgutil yt-dlp plugin that fetches PO tokens from the bundled server.
RUN pip install -r requirements.txt "bgutil-ytdlp-pot-provider==${BGUTIL_VERSION}"

# PO-token HTTP server + its Node.js runtime; started and supervised by the app on 127.0.0.1.
COPY --from=potserver /usr/local/bin/node /usr/local/bin/node
COPY --from=potserver /app /opt/bgutil

COPY backend/app ./app
COPY backend/docker-entrypoint.sh /app/docker-entrypoint.sh
# Tolerate Windows checkouts (CRLF line endings, lost executable bit).
RUN sed -i 's/\r$//' /app/docker-entrypoint.sh && chmod 0755 /app/docker-entrypoint.sh
# The API serves the UI from /app/frontend/dist (same origin, so no CORS setup needed).
COPY --from=frontend /src/frontend/dist /app/frontend/dist

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/backend/tmp \
    && chown -R app:app /app/backend/tmp
# No "USER app": the entrypoint starts as root only to copy root-owned Render Secret Files
# (cookies.txt) into an app-owned location, then drops to the app user for the server.

ENV APP_ENV=production \
    SERVE_FRONTEND=true \
    FRONTEND_DIST=/app/frontend/dist \
    POT_PROVIDER=bgutil \
    BGUTIL_SERVER_SCRIPT=/opt/bgutil/build/main.js \
    TEMP_DIR=/app/backend/tmp \
    PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=4)"

# Exactly one worker: jobs are kept in process memory.
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-server-header"]
