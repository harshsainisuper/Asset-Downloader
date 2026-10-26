#!/usr/bin/env bash
# Start backend (port 8000) and frontend dev server (port 5173) together.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT/backend"
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000 &
BACKEND=$!
trap 'kill $BACKEND 2>/dev/null' EXIT INT TERM
cd "$ROOT/frontend"
npm run dev
