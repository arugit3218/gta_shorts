"""Music montage endpoints (Phase 3)."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, Field

from ..context import AppContext
from ..db import new_id
from ..errors import InvalidInput
from ..jobs.worker import Worker
from ..media.library import AUDIO_EXTS, audio_duration
from ..montage import service as msvc
from ..storage import GB, MB, ensure_space
from .deps import ctx, get_clip, get_project, worker
from .sources import safe_filename, save_raw_upload

router = APIRouter(prefix="/api", tags=["montages"])


class SongChoice(BaseModel):
    kind: Literal["campaign", "upload"] = "upload"
    sound_id: str | None = Field(None, max_length=40)
    use_cut: bool = False  # keep the campaign sound's start ("cut") instead of placing the drop
    start: float | None = Field(None, ge=0)  # for uploads with use_cut


class MontageOptions(BaseModel):
    length: float = Field(15, ge=6, le=90)
    drop_at: float = Field(0.4, ge=0.1, le=0.8)
    style: Literal["chill", "normal", "hype"] = "normal"
    pov: str = Field("", max_length=200)
    variants: int = Field(3, ge=1, le=8)
    audio: Literal["song", "silent"] = "song"
    flash: bool = True
    sources: list[str] = Field(default_factory=list, max_length=40)
    song: SongChoice = SongChoice()


class CreateMontage(BaseModel):
    name: str = Field("Music edit", max_length=200)
    campaign_id: str | None = Field(None, max_length=40)
    options: MontageOptions = MontageOptions()
    start: bool = False


class MontageLinks(BaseModel):
    urls: list[str] = Field(min_length=1, max_length=20)
    campaign_id: str | None = Field(None, max_length=40)


class PatchMontage(BaseModel):
    name: str | None = Field(None, max_length=200)
    options: MontageOptions | None = None


class Replan(BaseModel):
    pov: str | None = Field(None, max_length=200)
    audio: Literal["song", "silent"] | None = None
    flash: bool | None = None
    style: Literal["chill", "normal", "hype"] | None = None
    drop_at: float | None = Field(None, ge=0.1, le=0.8)
    reshuffle: bool = False
    render: bool = True


def _check_sources(c: AppContext, ids: list[str]) -> None:
    for pid in ids:
        p = c.db.one("SELECT source_type FROM projects WHERE id = ?", (pid,))
        if p is None:
            raise InvalidInput("A selected video no longer exists")
        if p["source_type"] == "montage":
            raise InvalidInput("A montage can't use another montage as a source")


def _save_options(c: AppContext, p: dict, o: MontageOptions) -> None:
    old = (p.get("options") or {}).get("montage") or {}
    m = o.model_dump()
    if m["song"].get("kind") == "upload" and old.get("song", {}).get("path"):
        m["song"] = old["song"] | {k: v for k, v in m["song"].items() if v is not None and k != "kind"} | {"kind": "upload"}
    c.db.update("projects", p["id"], {"options": (p.get("options") or {}) | {"montage": m, "auto_render": True}, "duration": m["length"]})


@router.post("/montages")
def create_montage(body: CreateMontage, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    from .projects import _check_campaign, _public

    _check_sources(c, body.options.sources)
    now = time.time()
    pid = new_id()
    c.db.insert("projects", {"id": pid, "name": body.name.strip() or "Music edit", "source_type": "montage", "status": "new", "campaign_id": _check_campaign(c, body.campaign_id),
                             "options": {"montage": body.options.model_dump(), "auto_render": True}, "duration": body.options.length, "source_owned": 0, "created_at": now, "updated_at": now})
    if body.start:
        w.enqueue(pid, "process")
    return _public(c, get_project(c, pid))


@router.post("/montages/sources")
def add_links(body: MontageLinks, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Add video links as montage sources. Checks each link (title, length) but doesn't download yet:
    the montage job downloads them when it starts."""
    from ..media import download as dl
    from .projects import _check_campaign, _create_url, _public

    cid = _check_campaign(c, body.campaign_id)
    created, errors, seen = [], [], set()
    for raw in body.urls:
        u = raw.strip()
        if not u or u in seen:
            continue
        seen.add(u)
        try:
            info = dl.inspect(u, c.settings.get().max_download_height)
            p = _create_url(c, w, info["url"], info["title"], {}, cid, False)
            if not p["duration"] and info.get("duration"):
                c.db.update("projects", p["id"], {"duration": info["duration"]})
                p = _public(c, get_project(c, p["id"]))
            created.append(p)
        except Exception as e:
            errors.append({"url": u[:300], "error": str(e)})
    return {"created": created, "errors": errors}


@router.patch("/montages/{project_id}")
def patch_montage(project_id: str, body: PatchMontage, c: AppContext = Depends(ctx)):
    from .projects import _public

    p = get_project(c, project_id)
    if p["source_type"] != "montage":
        raise InvalidInput("Not a montage")
    if body.options is not None:
        _check_sources(c, body.options.sources)
        _save_options(c, p, body.options)
    if body.name:
        c.db.update("projects", project_id, {"name": body.name.strip()[:200]})
    return _public(c, get_project(c, project_id))


@router.post("/montages/{project_id}/song")
async def upload_song(project_id: str, request: Request, filename: str = Query(..., max_length=255), c: AppContext = Depends(ctx)):
    from .projects import _public

    p = get_project(c, project_id)
    if p["source_type"] != "montage":
        raise InvalidInput("Not a montage")
    name = safe_filename(filename, "song.mp3")
    if Path(name).suffix.lower() not in AUDIO_EXTS:
        raise InvalidInput(f"Unsupported audio type. Supported: {', '.join(sorted(AUDIO_EXTS))}")
    ensure_space(c.paths.work, 100 * MB, int(c.settings.get().min_free_gb * GB), "the song")
    d = c.paths.project_dir(project_id)
    for old in d.glob("song.*"):
        old.unlink(missing_ok=True)
    dest = d / f"song{Path(name).suffix.lower()}"
    await save_raw_upload(request, dest, max_bytes=100 * MB)
    if audio_duration(dest) <= 3:
        dest.unlink(missing_ok=True)
        raise InvalidInput("Couldn't read that song (or it's shorter than 3 seconds)")
    o = msvc.options(p)
    o["song"] = (o.get("song") or {}) | {"kind": "upload", "path": str(dest), "name": Path(filename).stem[:120]}
    c.db.update("projects", project_id, {"options": (p.get("options") or {}) | {"montage": o}})
    return _public(c, get_project(c, project_id))


@router.get("/montages/{project_id}/song-info")
def song_info(project_id: str, c: AppContext = Depends(ctx)):
    """Tempo, beats and drop of the montage's song (analysed and cached on first call)."""
    p = get_project(c, project_id)
    song, pinned, name = msvc.song_for(c, p)
    a = msvc.song_analysis(c, song)
    return {"name": name, "duration": a.duration, "tempo": a.tempo, "drop": a.drop, "drop_strength": a.drop_strength, "beats": len(a.beats), "pinned_start": pinned}


@router.post("/clips/{clip_id}/montage")
def replan_clip(clip_id: str, body: Replan, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    from .clips import clip_detail

    clip = get_clip(c, clip_id)
    p = get_project(c, clip["project_id"])
    if p["source_type"] != "montage":
        raise InvalidInput("Not a montage clip")
    msvc.replan(c, p, clip, body.model_dump(exclude_none=True))
    if body.render:
        w.enqueue(p["id"], "render", {"clip_id": clip_id})
    return clip_detail(clip_id, c)


@router.post("/montages/{project_id}/variant")
def new_variant(project_id: str, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    from .clips import clip_detail

    p = get_project(c, project_id)
    if p["source_type"] != "montage":
        raise InvalidInput("Not a montage")
    cid = msvc.add_variant(c, p)
    w.enqueue(project_id, "render", {"clip_id": cid})
    return clip_detail(cid, c)
