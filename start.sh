#!/usr/bin/env bash
# Start ClipStudio: builds the UI if needed, then serves everything on http://127.0.0.1:8765
set -euo pipefail
ROOT="$(cd "$(dirname "$0")" && pwd)"

command -v ffmpeg >/dev/null || { echo "ffmpeg not found. Install it first (see README)."; exit 1; }
command -v uv >/dev/null || { echo "uv not found. Install it first (see README)."; exit 1; }

if [ ! -d "$ROOT/backend/.venv" ]; then
  echo "Installing Python dependencies (~560 MB)…"
  (cd "$ROOT/backend" && uv sync --python 3.12)
else
  # quick no-op when nothing changed; picks up new dependencies after a git pull
  (cd "$ROOT/backend" && uv sync --python 3.12 --quiet)
fi

# Build the UI if it's missing or any UI source is newer than the last build.
if [ ! -f "$ROOT/frontend/dist/index.html" ] || [ -n "$(find "$ROOT/frontend/src" "$ROOT/frontend/index.html" "$ROOT/frontend/package.json" -newer "$ROOT/frontend/dist/index.html" -print -quit 2>/dev/null)" ]; then
  echo "Building the UI…"
  (cd "$ROOT/frontend" && { [ -d node_modules ] || npm install --no-audit --no-fund; } && npm run build)
fi

command -v node >/dev/null || command -v deno >/dev/null || echo "Note: install Node.js or Deno so YouTube links can be downloaded (see SETUP.md)."

PORT="${CLIPSTUDIO_PORT:-8765}"
echo "ClipStudio → http://127.0.0.1:${PORT}  (Ctrl+C to stop)"
( sleep 2; open "http://127.0.0.1:${PORT}" >/dev/null 2>&1 || true ) &
cd "$ROOT/backend" && exec .venv/bin/python -m clipstudio
