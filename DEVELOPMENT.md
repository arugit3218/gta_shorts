# Development log

## Status (2026-11-20)

### Done and tested
- [x] Backend core: config and modes, SQLite, structured JSON logging, storage checks and estimates
- [x] Ingest: upload (streamed), local file in place, URL via yt-dlp (validation, dedup, progress, friendly errors)
- [x] Transcription: parakeet-mlx in a child process, chunked, word timestamps, cache, JSON and SRT. Includes a fix for numbers merged into the preceding word ("was22").
- [x] Highlight engine: sentence normalisation, TextTiling, candidate windows, 7-criteria explainable scoring, NMS and duplicate removal, no forced padding
- [x] Extractive hooks, titles, descriptions, hashtags and categories. Optional Ollama tier with grounding validation.
- [x] Rendering: 9:16, 1:1, 16:9 at 720p or 1080p. Layouts: tracked crop, centre, blurred fill, split. Loudness normalisation, optional music.
- [x] Face tracking: YuNet, scene cuts, a switch must be confirmed before the camera moves, dead-zone, two-pass smoothing
- [x] Captions: 6 ASS presets, per-word highlight, karaoke, pop, fade, balanced line wrapping, overrides, exact frame preview
- [x] Job system: sequential worker, per-stage progress, cancellation, restart recovery, atomic outputs
- [x] API: projects, clips, jobs, settings, models, storage and cleanup, with localhost and Host-header protection
- [x] UI: Dashboard, New project, Projects, Processing and gallery, Clip editor, Settings, Storage. Dark and light themes.
- [x] Tests: 33 passing (unit, integration through the API with real FFmpeg, and one real-model transcription test)
- [x] Docs: README, ARCHITECTURE

### Verified manually during development
- Parakeet loads offline from the cache. Peak RSS on short audio was about 550 MB.
- Renders were checked visually: caption fonts (Arial Black, Impact), word highlight, outline, 720×1280 output.
- URL inspection worked against a public-domain Wikimedia Commons video.

### Not yet verified with real footage (the user will test)
- Face tracking and split-screen layout on real multi-person video. The tracking logic is covered by unit tests on synthetic tracks only.
- Score quality and ranking on long real-world videos (30–90 minutes). Thresholds may need tuning.
- Peak RAM and time per hour of video on the M2 8 GB.
- The optional Ollama tier (Ollama isn't installed on this machine).

## Known limitations
1. **English only** by default (Parakeet v2). Parakeet v3 (multilingual, 2.4 GB) can be selected once downloaded. Language isn't detected automatically.
2. **No speaker diarisation.** Framing follows the largest face, keeping continuity. There is no audio-based active-speaker detection yet.
3. **Rule-based scoring** works from phrase lexicons. It favours talking-head content (podcasts, interviews, commentary). Gameplay without speech gets few candidates, because the engine is speech-driven.
4. The **Visual quality** criterion is based only on source resolution.
5. **The caption preview is a single still frame** (exact styling). Animation shows only in the rendered video.
6. **Split screen** needs two clearly separated faces of similar size in at least 20% of sampled frames. Otherwise it falls back to tracking or blurred fill.
7. **Re-processing a project replaces its clips**, including edits. Rendered MP4s of the old clips are deleted.

## Next steps (suggested)
- Speechless highlight seeding from audio peaks and scene motion (useful for gameplay and GTA footage)
- Active-speaker detection (mouth-region motion × audio energy) for interviews
- Calibrate `CALIBRATION()` from real engagement data. Export and import per-clip performance.
- Batch export of approved clips to a folder, with a description sidecar
- Optional Parakeet v3, with a model download flow in the UI that shows the size before downloading
