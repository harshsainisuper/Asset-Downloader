# Single image serving both the UI and the API (FastAPI + yt-dlp + FFmpeg + Deno).
# Build from the repository root:
#   docker build -t yt-media-toolkit .
#   docker run -p 8000:8000 -e SECRET_KEY=change-me yt-media-toolkit   → http://localhost:8000

# --- Stage 1: build the React frontend ------------------------------------------------
FROM node:22-slim AS frontend
WORKDIR /src/frontend
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
RUN npm run build

# --- Stage 2: Python API that also serves the built UI ---------------------------------
FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# FFmpeg (with libx264, libmp3lame, libopus, libvpx, dav1d) from Debian.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app/backend
COPY backend/requirements.txt .
# Includes yt-dlp[default,deno]: the Deno runtime yt-dlp needs for YouTube.
RUN pip install -r requirements.txt

COPY backend/app ./app
# The API serves the UI from /app/frontend/dist (same origin, so no CORS setup needed).
COPY --from=frontend /src/frontend/dist /app/frontend/dist

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/backend/tmp \
    && chown -R app:app /app/backend/tmp
USER app

ENV APP_ENV=production \
    SERVE_FRONTEND=true \
    FRONTEND_DIST=/app/frontend/dist \
    TEMP_DIR=/app/backend/tmp \
    PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=4)"

# Exactly one worker: jobs are kept in process memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-server-header"]
