#!/usr/bin/env bash
# Start ClipStudio: builds the UI if needed, then serves everything on http://127.0.0.1:8765
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found. Install it first (see README)."; exit 1; }
command -v uv >/dev/null || { echo "uv not found. Install it first (see README)."; exit 1; }

if [ ! -d "$ROOT/backend/.venv" ]; then
  echo "Installing Python dependencies (~560 MB)…"
  (cd "$ROOT/backend" && uv sync --python 3.12)
fi

if [ ! -f "$ROOT/frontend/dist/index.html" ]; then
  echo "Building the UI…"
  (cd "$ROOT/frontend" && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build)
fi

PORT="${CLIPSTUDIO_PORT:-8765}"
echo "ClipStudio → http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
( sleep 2; open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true ) &
cd "$ROOT/backend" && exec .venv/bin/python -m clipstudio
