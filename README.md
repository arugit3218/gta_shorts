# ClipStudio

ClipStudio is a local AI video clipping studio for Apple Silicon Macs. Give it a long video (an uploaded file, a file on your disk, or a public link). It finds the most engaging moments, ranks them with a score it explains, and renders vertical clips with word-by-word animated captions, face-tracked framing, titles, hooks and hashtags.

It is built for a MacBook Air with an M2 chip and 8 GB of RAM:
- Everything runs locally and nothing is uploaded.
- You don't need any API keys.
- It processes one job at a time and keeps only one AI model in memory.

## Features

| Feature | Notes |
|---|---|
| Input | Drag-and-drop upload, a local file used in place (no copy), or a public video URL (yt-dlp). |
| Transcription | Parakeet TDT 0.6B v2 via MLX: word-level timestamps, English. Cached per video. JSON and SRT export. |
| Highlight detection | Sentence-aware windows, topic-change detection (TextTiling) and audio energy. Overlapping and duplicate clips are removed, and weak clips are never added just to reach a target count. |
| Scoring | 7 weighted criteria (hook 25, context 20, emotion 15, value 15, story 10, visual 10, audio 5). Each clip shows its reasons and weaknesses. Weights are configurable. |
| Framing | 9:16, 1:1 or 16:9. Modes: auto, face tracking (YuNet), centre crop, blurred fill, or split screen for two people. |
| Captions | 6 presets (classic, bold, highlighted word, karaoke, cinematic, minimal) with full customisation, burned in via ASS/libass. Exact frame preview before rendering. |
| Metadata | 3 hooks, a title, a description, hashtags and a category for each clip. Taken from the speaker's own words, so nothing is invented. Local AI (Ollama) can optionally suggest alternatives. |
| Editor | Trim on a timeline that snaps to word boundaries, edit caption text, change style, format and framing, choose a hook, edit the title, add your own background music, then re-render a single clip. |
| Audio | Loudness normalised to −14 LUFS with a limiter. Optional music mix with fades. |
| Storage | Disk checks before every heavy step, storage estimates, a storage page, cleanup, external-SSD working folder, and a retention setting. |
| Reliability | Persistent job queue, cancellation, resume after restart, atomic outputs, clear errors. |

## Requirements

- **macOS on Apple Silicon** (M1 or later). The speech model uses MLX.
- **FFmpeg with libass** (`ffmpeg -filters | grep ass` should list `ass`).
- **uv** (Python manager): `curl -LsSf https://astral.sh/uv/install.sh | sh`
- **Node.js 20 or later**, only to build the UI.
- **Disk:** about 0.7 GB for the app, plus the speech model (2.3 GB, see below), plus room for your videos. Each hour of source needs roughly the source file size plus about 200 MB.

## Model setup

ClipStudio never downloads models on its own. It uses the Hugging Face cache (`~/.cache/huggingface`).

- **Already installed on this Mac:** `mlx-community/parakeet-tdt-0.6b-v2` (2.3 GB).
- **To install it on another Mac** (check free space first):
  ```bash
  df -h ~                                  # need ~2.5 GB free
  cd backend && uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/parakeet-tdt-0.6b-v2')"
  ```
- **Multilingual (optional):** `mlx-community/parakeet-tdt-0.6b-v3` (2.4 GB, 25 European languages). Download it the same way, then select it in Settings.
- **Optional local AI:**
  1. Install [Ollama](https://ollama.com).
  2. Run `ollama pull qwen2.5:1.5b` (about 1 GB of disk, about 1.5 GB of RAM while running).
  3. Turn on "local AI" in Settings.

  It is off by default. In Low resource mode it isn't recommended on 8 GB.

To let the app download models itself, set `CLIPSTUDIO_ALLOW_MODEL_DOWNLOADS=true` (off by default).

## Start

```bash
./start.sh
```
On the first run it installs Python dependencies and builds the UI. It then opens **http://127.0.0.1:8765**.

For development, with hot reload:
```bash
cd backend && uv run python -m clipstudio        # API on :8765
cd frontend && npm run dev                       # UI on :5173 (proxies /api)
```

## Using it

1. **New project:** upload a file, use a file in place, or paste a link. Check the video details and storage estimate, pick clip length, count, aspect ratio, framing and caption style, then click **Find clips**.
2. **Processing:** watch each stage with real progress. You can cancel at any time. If you quit mid-job, it resumes on the next start and reuses finished steps.
3. **Clips:** sorted by score. Open **Why this score** for the breakdown. Approve or reject, download the MP4, or edit.
4. **Editor:** trim, fix captions, restyle with **Preview captions**, change format, choose a hook, add music, then **Save & render**.

### Saving disk space
- **Use file in place:** avoids copying videos you already have.
- **Settings → Working directory:** set this to an external SSD, for example `/Volumes/SSD/ClipStudio`.
- **Storage → Clean up:** removes temporary files, partial files and unused downloads. It never deletes your originals or finished clips.
- **Turn off "Render automatically":** this lets you review candidates first and render only the clips you want.

## Processing modes

| Mode | Transcription | Local AI | Output | Encoder | RAM at peak |
|---|---|---|---|---|---|
| **Low resource** (default) | 120 s chunks | off | 720p, 30 fps | VideoToolbox (hardware) | about 3 GB |
| Balanced | 180 s chunks | optional | 720p | x264 veryfast | about 3–4.5 GB with local AI |
| High quality | 240 s chunks | optional | 1080p, up to 60 fps | x264 medium | about 4–5 GB |

## Privacy and network use

The server listens on `127.0.0.1` only and rejects requests whose Host header isn't localhost. There is no telemetry. The only network requests are:
1. **Video links:** yt-dlp contacts the video site you entered, to read metadata and download the video.
2. **Model downloads:** only when you run the setup commands above, or turn on `CLIPSTUDIO_ALLOW_MODEL_DOWNLOADS`.
3. **Ollama**, if enabled: this runs on localhost, and the settings reject any non-localhost address.

Respect copyright and platform terms. Only download videos you're allowed to. Private, login-only and DRM-protected videos are refused.

## Tests

```bash
cd backend && uv run --group dev pytest            # 33 tests (~20 s, includes 1 real-model test)
uv run --group dev pytest -m "not slow"            # skip the speech-model test
```
These use small synthetic clips. They cover the analysis engine, captions, reframing maths, rendering (all layouts and aspect ratios), the full API pipeline, invalid input, insufficient storage, a missing model, cancellation and restart recovery.

## Troubleshooting

| Problem | Fix |
|---|---|
| "Not enough disk space" | Run Storage → Clean up, delete sources you no longer need, or move the working directory to an external drive. You can lower the reserve under Settings → Storage. |
| "Transcription model … not installed" | See Model setup. |
| "Transcription was killed (likely out of memory)" | Close other apps and lower Settings → Transcription chunk length to 60 s. |
| URL refused | Only public, downloadable videos work. Otherwise download the video yourself (if permitted) and upload it. |
| Captions show the wrong font | The font must be installed in macOS. Pick one from the list. |
| Port already in use | `CLIPSTUDIO_PORT=8770 ./start.sh` |
| Logs | `data/logs/clipstudio.jsonl` (JSON lines). |

More detail: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) · progress and limitations: [DEVELOPMENT.md](DEVELOPMENT.md).
