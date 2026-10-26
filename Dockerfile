# Backend API image (FastAPI + yt-dlp + FFmpeg + Deno). Build from the repository root:
#   docker build -t yt-media-toolkit-api .
#   docker run -p 8000:8000 -e SECRET_KEY=... -e CORS_ORIGINS=https://your-app.vercel.app yt-media-toolkit-api
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

RUN useradd --create-home --uid 10001 app \
    && mkdir -p /app/backend/tmp \
    && chown -R app:app /app/backend/tmp
USER app

ENV APP_ENV=production \
    SERVE_FRONTEND=false \
    TEMP_DIR=/app/backend/tmp \
    PORT=8000

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s \
    CMD python -c "import os,urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ[\"PORT\"]}/api/health', timeout=4)"

# Exactly one worker: jobs are kept in process memory.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port \"$PORT\" --workers 1 --no-server-header"]
