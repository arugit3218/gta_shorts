"""Library endpoints: background music by mood and sound-effect overrides."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse

from ..context import AppContext
from ..errors import InvalidInput, NotFound
from ..media import sfx as sfx_mod
from ..media.library import AUDIO_EXTS, MOODS, list_music, music_dir, track_path
from ..storage import GB, MB, ensure_space
from .deps import ctx
from .sources import safe_filename, save_raw_upload

router = APIRouter(prefix="/api", tags=["library"])


def _sfx_overrides(lib: Path) -> dict[str, list[dict]]:
    out = {}
    for k in sfx_mod.TYPES:
        d = lib / "sfx" / k
        out[k] = sorted(({"name": p.name, "mb": round(p.stat().st_size / 1e6, 2)} for p in d.iterdir() if p.is_file() and p.suffix.lower() in AUDIO_EXTS), key=lambda x: x["name"]) if d.is_dir() else []
    return out


@router.get("/library")
def library(c: AppContext = Depends(ctx)):
    return {"moods": list(MOODS), "music": list_music(c.paths.library), "sfx_types": list(sfx_mod.TYPES), "sfx": _sfx_overrides(c.paths.library), "path": str(c.paths.library)}


@router.post("/library/music/{mood}")
async def add_music(mood: str, request: Request, filename: str = Query(..., max_length=255), c: AppContext = Depends(ctx)):
    """Add a track you own or have a licence for. It is only ever used locally."""
    if mood not in MOODS:
        raise InvalidInput("Unknown mood")
    name = safe_filename(filename, "track.mp3")
    if Path(name).suffix.lower() not in AUDIO_EXTS:
        raise InvalidInput(f"Unsupported audio type. Supported: {', '.join(sorted(AUDIO_EXTS))}")
    ensure_space(c.paths.library, 60 * MB, int(c.settings.get().min_free_gb * GB), "the track")
    dest = music_dir(c.paths.library, mood) / name
    if dest.exists():
        dest = dest.with_name(f"{dest.stem}-{int(dest.stat().st_mtime) % 10000}{dest.suffix}")
    await save_raw_upload(request, dest, max_bytes=60 * MB)
    return library(c)


@router.delete("/library/music/{mood}/{name}")
def delete_music(mood: str, name: str, c: AppContext = Depends(ctx)):
    p = track_path(c.paths.library, mood, name)
    if p is None:
        raise NotFound("Track not found")
    p.unlink()
    return library(c)


@router.get("/library/music/{mood}/{name}")
def play_music(mood: str, name: str, c: AppContext = Depends(ctx)):
    p = track_path(c.paths.library, mood, name)
    if p is None:
        raise NotFound("Track not found")
    return FileResponse(p)


def _sfx_path(lib: Path, kind: str, name: str) -> Path | None:
    if kind not in sfx_mod.TYPES or "/" in name or "\\" in name or name.startswith("."):
        return None
    p = lib / "sfx" / kind / name
    return p if p.is_file() else None


@router.post("/library/sfx/{kind}")
async def add_sfx(kind: str, request: Request, filename: str = Query(..., max_length=255), c: AppContext = Depends(ctx)):
    if kind not in sfx_mod.TYPES:
        raise InvalidInput("Unknown sound-effect type")
    name = safe_filename(filename, f"{kind}.wav")
    if Path(name).suffix.lower() not in AUDIO_EXTS:
        raise InvalidInput(f"Unsupported audio type. Supported: {', '.join(sorted(AUDIO_EXTS))}")
    dest = c.paths.library / "sfx" / kind / name
    await save_raw_upload(request, dest, max_bytes=10 * MB)
    return library(c)


@router.delete("/library/sfx/{kind}/{name}")
def delete_sfx(kind: str, name: str, c: AppContext = Depends(ctx)):
    p = _sfx_path(c.paths.library, kind, name)
    if p is None:
        raise NotFound("Sound effect not found")
    p.unlink()
    return library(c)


@router.get("/sfx/{kind}")
def preview_sfx(kind: str, c: AppContext = Depends(ctx)):
    """The effect that would be used (your override if present, else the built-in one)."""
    if kind not in sfx_mod.TYPES:
        raise NotFound("Unknown sound effect")
    return FileResponse(sfx_mod.resolve_file(kind, cache=c.paths.cache, library=c.paths.library, seed="preview"), media_type="audio/wav")
