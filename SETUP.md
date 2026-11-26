# Setting up ClipStudio on your Mac

This guide takes you from a fresh clone to a running app. It takes about 15–20 minutes, most of it downloads.

## 1. Check your Mac

| Requirement | Why | How to check |
|---|---|---|
| **Apple Silicon** (M1, M2, M3, M4…) | The speech model runs on Apple's MLX framework | Apple menu → About This Mac → "Chip" |
| **macOS 13.5 or newer** | Required by MLX | Same window |
| **8 GB RAM or more** | 8 GB works (default Low resource mode) | Same window |
| **About 5 GB free disk** | App ~0.7 GB, speech model 2.3 GB, plus room for videos | `df -h ~` in Terminal |

Intel Macs, Windows and Linux are **not supported**, because the speech model needs Apple Silicon.

## 2. Install the tools

Open **Terminal** and run these one at a time. Skip any tool you already have.

**Homebrew** (package manager). Check with `brew --version`:
```bash
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"
```
When it finishes, run the two `echo … >> ~/.zprofile` / `eval …` lines it prints, so `brew` is on your PATH.

**FFmpeg, Node.js and Git:**
```bash
brew install ffmpeg node git
```
Node.js is also what lets ClipStudio download from YouTube (YouTube needs a JavaScript runtime since late 2025). Deno works too (`brew install deno`). **Settings** and the **Video link** tab warn you if neither is found.

**uv** (installs Python and the Python packages for you):
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```
Close and reopen Terminal afterwards.

**Check everything:**
```bash
ffmpeg -hide_banner -filters | grep " ass "   # must print a line containing "ass"
node --version                                 # v20 or newer
uv --version
git --version
```
If the `ffmpeg … grep` line prints nothing, your FFmpeg can't draw captions. Run `brew reinstall ffmpeg`.

## 3. Get the code

```bash
cd ~/Desktop
git clone https://github.com/arugit3218/gta_shorts.git
cd gta_shorts
```
The repository is private, so the owner must add you as a collaborator first. If `git clone` asks for a password, use a GitHub [personal access token](https://github.com/settings/tokens), not your account password. Or run `brew install gh && gh auth login` once.

## 4. Install the speech model (2.3 GB, one time)

ClipStudio never downloads models by itself. Check free space first, then download:

```bash
df -h ~                      # make sure ~3 GB+ is free
cd backend
uv sync --python 3.12        # installs Python 3.12 + packages (~560 MB)
uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/parakeet-tdt-0.6b-v2')"
cd ..
```
The model is saved in `~/.cache/huggingface` and shared by every project on your Mac.

## 5. Start the app

```bash
./start.sh
```
The first run also builds the web interface (about 1 minute). When you see

```
ClipStudio → http://127.0.0.1:8765  (Ctrl+C to stop)
```

your browser opens **http://127.0.0.1:8765**. If it doesn't, open that address yourself.

To stop the app, press **Ctrl+C** in Terminal. Next time, only step 5 is needed.

## 6. Check that it works (optional)

```bash
cd backend
uv run --group dev pytest -q          # expect: 33 passed (~20 s)
```

Then in the app:
1. Click **Create → Clips from a long video**.
2. Add a short video of someone talking (5–10 minutes is ideal), using one of:
   - **Use file in place:** paste the file's path. In Finder, select the file and press ⌥⌘C to copy it.
   - **Upload file:** drag the video in.
   - **Video link:** paste a public video URL.
3. Click **Find clips** and watch each stage run.
4. Review, edit and download your clips.

## 7. Optional extras

**More languages.** The default model is English-only. For 25 European languages, download Parakeet v3 (2.4 GB):
```bash
cd backend && uv run python -c "from huggingface_hub import snapshot_download; snapshot_download('mlx-community/parakeet-tdt-0.6b-v3')"
```
Then choose it in **Settings → Transcription model**.

**Local AI for titles and hooks.** Not needed; the app works fully without it. On 16 GB Macs it's worth trying:
1. Install [Ollama](https://ollama.com).
2. Run `ollama pull qwen2.5:1.5b` (~1 GB).
3. Turn on **Settings → Optional local AI**.

**Save internal disk space.** Set **Settings → Working directory** to an external drive, for example `/Volumes/MySSD/ClipStudio`. Keep **Settings → Downloads → Smart** on: links download audio first and then only the video sections your clips need.

**Background music.** Add tracks you own or have a licence for in **Library** (sorted by mood: energetic, calm, dramatic). Without tracks, clips simply have no music.

## Updating to the latest version

```bash
cd ~/Desktop/gta_shorts
git pull
cd backend && uv sync && cd ..
cd frontend && npm install && npm run build && cd ..
./start.sh
```

## Troubleshooting

| Problem | Fix |
|---|---|
| `./start.sh: Permission denied` | `chmod +x start.sh` |
| `ffmpeg not found` / `uv not found` | Redo step 2, then reopen Terminal |
| "Transcription model … not installed" | Redo step 4 |
| "Not enough disk space" | Free space, use **Storage → Clean up**, or use an external working directory |
| "Transcription was killed (likely out of memory)" | Close other apps; lower **Settings → Transcription chunk length** to 60 s |
| Port 8765 already in use | `CLIPSTUDIO_PORT=8770 ./start.sh` |
| A video link is refused | Only public, downloadable videos work. Download the video yourself (if allowed) and upload it. |
| YouTube links fail with a "JavaScript"/"challenge" error | Install Node.js or Deno (step 2), run `cd backend && uv sync`, and restart |
| A smart download falls back to the full video | Normal for sites that don't support partial downloads; or set **Settings → Downloads → Full video** |
| Anything else | See the log file `data/logs/clipstudio.jsonl`, and [README.md](README.md) for more |

## What stays on your Mac

Everything. The app only listens on your own computer (`127.0.0.1`), has no telemetry, and needs no API keys. It uses the internet in only two cases: downloading a video from a link you paste, and the model download in step 4.

Your videos, transcripts, clips and settings are stored in `data/` inside the project folder, or in your chosen working directory. That folder is never committed to Git.
