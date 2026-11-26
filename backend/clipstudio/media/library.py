"""Local media library: background music sorted by mood, and sound-effect overrides.

Layout (inside the working directory, so it can live on an external SSD):
    library/music/<mood>/<track>.mp3
    library/sfx/<type>/<effect>.wav
Only audio the user adds is used. Nothing is downloaded.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

from . import ffmpeg as ff

MOODS = ("energetic", "calm", "dramatic")
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".ogg", ".flac"}


def music_dir(library: Path, mood: str) -> Path:
    if mood not in MOODS:
        raise ValueError(mood)
    d = library / "music" / mood
    d.mkdir(parents=True, exist_ok=True)
    return d


def audio_duration(path: Path) -> float:
    cmd = [ff.FFPROBE, "-v", "error", "-print_format", "json", "-show_format", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30, check=True).stdout
        return float(json.loads(out)["format"]["duration"])
    except Exception:
        return 0.0


def list_music(library: Path) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for mood in MOODS:
        d = library / "music" / mood
        items = []
        if d.is_dir():
            for p in sorted(d.iterdir()):
                if p.is_file() and p.suffix.lower() in AUDIO_EXTS and not p.name.startswith("."):
                    items.append({"name": p.name, "mood": mood, "mb": round(p.stat().st_size / 1e6, 1)})
        out[mood] = items
    return out


def track_path(library: Path, mood: str, name: str) -> Path | None:
    if mood not in MOODS or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = library / "music" / mood / name
    return p if p.is_file() else None


def mood_for(category: str, scores: dict) -> str:
    if scores.get("emotion", 0) >= 60 or category in ("Emotional", "Story"):
        return "dramatic"
    if category in ("Education", "Advice"):
        return "calm"
    return "energetic"


def pick_track(library: Path, mood: str | None, seed: str, clip_duration: float) -> tuple[Path, float] | None:
    """Deterministic track + start offset for a clip; falls back to any mood if the wanted one is empty."""
    lib = list_music(library)
    order = [mood] if mood in MOODS else []
    order += [m for m in MOODS if m not in order]
    for m in order:
        tracks = lib.get(m) or []
        if not tracks:
            continue
        h = int(hashlib.sha1(seed.encode()).hexdigest(), 16)
        t = tracks[h % len(tracks)]
        path = library / "music" / m / t["name"]
        dur = audio_duration(path)
        room = max(0.0, min(90.0, dur - clip_duration - 1.0))
        offset = float((h >> 8) % int(room)) if room >= 1 else 0.0
        return path, offset
    return None
