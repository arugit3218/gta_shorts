# Architecture

## Overview

```
Browser (React UI, served by FastAPI)
   │  JSON over HTTP, polled about once a second for progress
   ▼
FastAPI on 127.0.0.1:8765 ── SQLite (projects, jobs, clips)  ── settings.json
   │  enqueue
   ▼
Worker thread: one job at a time (persisted queue, cancel tokens, recovery)
   │
   ├─ 1. Source        yt-dlp download (dedup by URL) | raw upload | local file in place
   ├─ 2. Audio         ffmpeg → 16 kHz mono WAV (temporary) + per-0.5 s loudness profile (cached)
   ├─ 3. Transcribe    child process: parakeet-mlx, chunked, word timestamps → cache/transcript_<fp>.json
   │                   (the process exits, so all model memory is freed before the next step)
   ├─ 4–6. Analyse     sentences → TextTiling topics → candidate windows → 7-criteria score → NMS/dedup
   ├─ 7. Metadata      extractive hooks/title/description/hashtags; optional Ollama (keep_alive=0)
   ├─ 8. Captions      word tracks per clip (user edits override)
   └─ 9. Render        YuNet faces @4 fps → smoothed crop path (sendcmd) → ffmpeg: crop/blur/split + ASS + loudnorm
```

## Technology choices

| Area | Choice | Why |
|---|---|---|
| Speech-to-text | parakeet-mlx (Apache-2.0) with Parakeet TDT 0.6B v2 | Native on Apple Silicon, accurate word timestamps, chunked long audio. The model was already on this Mac. Whisper large or WhisperX need more RAM and disk, or CUDA. |
| Highlight detection | Own deterministic engine | Works with no LLM (a hard requirement), and every score can be explained. Ideas borrowed from Chopify (MIT): phrase lexicons and audio evidence. ClipsAI (MIT): TextTiling, here with lexical vectors instead of a 1.4 GB transformer. |
| Face detection | OpenCV YuNet (230 KB ONNX) | MediaPipe pulls in opencv-contrib and matplotlib, and recent wheels dropped the needed model. YOLO needs torch. YuNet runs on CPU at about 50 MB RAM. |
| Rendering | FFmpeg filtergraph | Crop positions are fed through `sendcmd`, so no frames pass through Python and memory stays flat. Uses VideoToolbox hardware encoding in Low mode. |
| Captions | Generated ASS + libass | Precise per-word timing and styling. The active word changes colour only (changing size would reflow the line). A whole page pops in once. |
| API / storage | FastAPI + SQLite (WAL) + filesystem | No Redis, Postgres or Docker needed. |
| UI | Vite + React + TypeScript | No SSR needed, so the build is static and FastAPI serves it from the same origin. No UI or icon libraries; the gzipped bundle is about 100 KB. |

## Repository layout

```
backend/clipstudio/
  config.py          env settings (CLIPSTUDIO_*), user settings (JSON), mode presets, paths
  db.py              SQLite schema + small repository helpers
  storage.py         free space, estimates, fingerprinting, size accounting
  cancel.py          CancelToken (cooperative + kills subprocesses)
  transcription/     types, service (cache, availability), parakeet_worker (child process)
  analysis/          lexicon, segmentation (TextTiling), scoring, highlights (+ heatmap/chapters, score_range),
                     metadata, audio_features, ai_picks (prompt builder + answer parser)
  campaigns/         models (Brief), presets (generic + Clipster), parser (paste-to-fill), safezones,
                     post (caption builder), compliance (checker), service (per-clip features, export)
  media/             ffmpeg (probe/run), download (yt-dlp: full/audio/section/subtitles), align (section
                     offset by audio correlation), reframe, captions, overlays (hook/disclosure), logo,
                     burned_text (detect + blur), sfx (synth + placement), library (music by mood), render
  llm/ollama.py      optional local LLM with grounding validation
  jobs/              pipeline, worker, progress
  api/               app factory, routes (projects, clips, system, campaigns, library), schemas, validation
backend/tests/       unit + integration tests
frontend/src/        pages (Dashboard, NewProject, Projects, ProjectView, ClipEditor, Campaigns, CampaignView,
                     Library, Settings, Storage), api.ts, components (ui, campaign)
```

## Scoring model

Each criterion is scored 0–100 by rules over per-sentence features, which are computed once:

- **Hook:** the opening sentence. Rewards a question, strong hook phrases, numbers, addressing "you", and a short punchy sentence. Penalises fillers, starting mid-thought ("and…", "but…"), and back-references to earlier context ("that…").
- **Context:** ends on a complete sentence, has a payoff in the last third, does not cross a topic boundary (with ±1 sentence tolerance), has no promotional or meta talk and no long pauses, and is close to the target length.
- **Emotion:** density of emotion words, intensifiers, laughter, exclamations and controversial stances, plus the share of loud audio frames (6 dB or more above the video's median).
- **Value:** density of advice phrases, imperative openers, explanation markers and numbers.
- **Story:** saturating count of narrative markers, plus a bonus when there is both setup and resolution.
- **Visual:** source resolution. The face coverage found during rendering is reported separately.
- **Audio:** level relative to the rest of the video, clipping, silence, speaking rate, filler ratio.

The total is a weighted sum, minus penalties for filler-heavy or repetitive windows. It passes through `CALIBRATION()` in `analysis/scoring.py`, which is the single place to plug in a model fitted to real engagement data later (for example, logistic regression on per-criterion scores against views or retention). Labels: 85+ Excellent, 70+ Strong, 55+ Needs review, otherwise Low priority (configurable).

## Memory and disk discipline (8 GB)

- Only one job runs at a time, and only one model is ever resident. The speech model runs in a child process that exits after transcription. Ollama is called with `keep_alive: 0`.
- Audio analysis is streamed in 0.5 s frames. Face detection holds one frame at a time at 480 px wide. FFmpeg does the crop work.
- The temporary WAV (about 115 MB per hour) is deleted after transcription unless "keep intermediates" is on. Renders write to `*.part.mp4` and then `os.replace`.
- Free space is checked before downloads, uploads, processing and each render, against a configurable reserve (1.5 GB by default).

## Security

- Binds to localhost. `TrustedHostMiddleware` blocks DNS-rebinding. CORS allows only the Vite dev origin.
- All subprocesses take argument lists (never `shell=True`). FFmpeg auxiliary files are referenced by relative name from a private temp working directory, so there is no filtergraph path injection.
- URLs: http(s) only. Hosts that resolve to private, loopback or link-local addresses are rejected. No cookies or credentials are passed to yt-dlp.
- Local-path import is allowed only for video files under `$HOME` or `/Volumes`. Media is served by ID, never by path.
- Uploads are streamed with size checks, and filenames are sanitised.

## Job lifecycle

`queued → running → done | failed | cancelled`. On startup, `running` becomes `queued` again. Every stage is cached by video fingerprint: download (by URL hash), transcript, audio profile. A resumed job therefore skips the work it already finished.

## Campaign layer (Phase 1)

```
Brief (any site) ──paste-to-fill──► campaigns table ◄── presets (generic, Clipster…)
      │
project.campaign_id ──► features(clip) = clip edit → project options → settings, then campaign rules win
      │                     (logo, disclosure, sound, hook title, music/SFX allowed?)
      ▼
render graph:  source → [burned-text blur] → reframe → captions → hook/disclosure → logo/template
               voice clean-up + ducked music + campaign sound + SFX → −14 LUFS
      ▼
compliance(clip) ─► pass / warn / fail / manual checks ─► export package (+ exports table = duplicate memory)
```

- **Platform-neutral:** nothing in the engine is site-specific. Site rules live in presets and the brief.
- **Smart downloads:** `audio_path` on the project, `sections.json` + `sections/` in the project folder. `media_for_clip()` returns the full source or the section covering a clip (with its offset). Rendering, preview frames and the editor's source player all use it.
- **Effective settings** are computed per clip (`campaigns/service.features`) and returned to the UI as `clip.features`, so the editor always shows what will actually render.
