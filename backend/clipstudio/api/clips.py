"""Clip endpoints: list, edit, re-render, preview, media, captions, music."""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, Response

from ..context import AppContext
from ..errors import InvalidInput, NotFound
from ..jobs.pipeline import clip_words, render_spec
from ..jobs.worker import Worker
from ..media.captions import PRESETS, CapWord, resolve_style
from ..media.render import render_preview_frame
from ..storage import GB, MB, ensure_space
from ..transcription.types import Transcript
from .deps import ctx, get_clip, get_project, worker
from .schemas import ClipPatch, PreviewFrame
from .sources import AUDIO_EXTS, safe_filename

router = APIRouter(prefix="/api", tags=["clips"])

RENDER_AFFECTING = {"start", "end", "edit", "caption_words", "reset_captions", "remove_music"}


def public_clip(c: dict) -> dict:
    out = {k: c[k] for k in ("id", "project_id", "rank", "start", "end", "score", "title", "hook", "description", "category", "status", "render_status", "render_error", "created_at", "updated_at")}
    out["duration"] = round(c["end"] - c["start"], 2)
    out["analysis"] = c["analysis"]
    out["hooks"] = c["meta"].get("hooks", [])
    out["hashtags"] = c["meta"].get("hashtags", [])
    edit = dict(c["edit"])
    edit.pop("caption_words", None)
    edit["has_caption_edits"] = bool(c["edit"].get("caption_words"))
    edit["music"] = Path(edit.pop("music_path")).name if edit.get("music_path") else None
    out["edit"] = edit
    out["has_video"] = bool(c["render_path"]) and Path(c["render_path"]).exists()
    out["has_thumb"] = bool(c["thumb_path"]) and Path(c["thumb_path"]).exists()
    out["version"] = int(c["updated_at"] * 1000)
    return out


@router.get("/projects/{project_id}/clips")
def list_clips(project_id: str, c: AppContext = Depends(ctx)):
    get_project(c, project_id)
    return [public_clip(x) for x in c.db.query("SELECT * FROM clips WHERE project_id = ? ORDER BY rank", (project_id,))]


@router.get("/clips/{clip_id}")
def clip_detail(clip_id: str, c: AppContext = Depends(ctx)):
    return public_clip(get_clip(c, clip_id))


@router.get("/clips/{clip_id}/words")
def clip_words_api(clip_id: str, context: float = Query(15.0, ge=0, le=120), c: AppContext = Depends(ctx)):
    """Caption words (absolute seconds) plus transcript words around the clip for trimming."""
    clip = get_clip(c, clip_id)
    project = get_project(c, clip["project_id"])
    words = [{"w": w.w, "s": round(w.s + clip["start"], 3), "e": round(w.e + clip["start"], 3)} for w in clip_words(c, project, clip)]
    around: list[dict] = []
    tpath = c.paths.project_dir(project["id"]) / "transcript.json"
    if tpath.exists():
        t = Transcript.from_dict(json.loads(tpath.read_text()))
        lo, hi = clip["start"] - context, clip["end"] + context
        around = [{"w": w.w, "s": w.s, "e": w.e} for w in t.words if w.e >= lo and w.s <= hi]
    return {"start": clip["start"], "end": clip["end"], "words": words, "context_words": around, "edited": bool(clip["edit"].get("caption_words")), "source_duration": project["duration"]}


@router.patch("/clips/{clip_id}")
def patch_clip(clip_id: str, body: ClipPatch, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    project = get_project(c, clip["project_id"])
    upd: dict = {}
    for k in ("title", "hook", "description", "status"):
        v = getattr(body, k)
        if v is not None:
            upd[k] = v.strip() if isinstance(v, str) else v
    meta = dict(clip["meta"])
    if body.hashtags is not None:
        meta["hashtags"] = ["#" + re.sub(r"[^A-Za-z0-9_]", "", h.lstrip("#"))[:40] for h in body.hashtags if h.strip("# ")]
        upd["meta"] = meta
    start = body.start if body.start is not None else clip["start"]
    end = body.end if body.end is not None else clip["end"]
    if body.start is not None or body.end is not None:
        dur = project["duration"] or end
        if not (0 <= start < end <= dur + 0.01):
            raise InvalidInput("Start must be before end and within the video")
        if end - start < 3 or end - start > 180:
            raise InvalidInput("Clips must be between 3 seconds and 3 minutes")
        upd["start"], upd["end"] = round(start, 3), round(end, 3)
    edit = dict(clip["edit"])
    if body.edit is not None:
        e = body.edit.model_dump(exclude_none=True)
        if "caption_preset" in e and e["caption_preset"] not in PRESETS:
            raise InvalidInput("Unknown caption preset")
        if "caption_style" in e:
            e["caption_style"] = {k: v for k, v in e["caption_style"].items() if v is not None}
        if "caption_preset" in e and "caption_style" not in e:
            edit.pop("caption_style", None)  # picking a preset resets custom overrides
        edit |= e
    if body.reset_captions:
        edit.pop("caption_words", None)
    if body.caption_words is not None:
        ws = sorted((w.model_dump() for w in body.caption_words if w.w.strip()), key=lambda w: w["s"])
        edit["caption_words"] = [{"w": w["w"].strip(), "s": round(w["s"], 3), "e": round(max(w["e"], w["s"] + 0.05), 3)} for w in ws]
    if body.remove_music and edit.get("music_path"):
        Path(edit.pop("music_path")).unlink(missing_ok=True)
    if edit != clip["edit"]:
        upd["edit"] = edit
    if any(k in upd for k in ("start", "end", "edit")) and clip["render_status"] in ("done", "failed"):
        upd["render_status"] = "stale"
    if upd:
        c.db.update("clips", clip_id, upd)
    return public_clip(get_clip(c, clip_id))


@router.post("/clips/{clip_id}/render")
def rerender(clip_id: str, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    clip = get_clip(c, clip_id)
    job_id = w.enqueue(clip["project_id"], "render", {"clip_id": clip_id})
    return {"job_id": job_id}


@router.post("/clips/{clip_id}/preview-frame")
def preview_frame(clip_id: str, body: PreviewFrame, c: AppContext = Depends(ctx)):
    """Render one frame with the exact caption styling — lets users check styles before exporting."""
    clip = get_clip(c, clip_id)
    project = get_project(c, clip["project_id"])
    tmp_clip = dict(clip)
    edit = dict(clip["edit"])
    if body.edit:
        e = body.edit.model_dump(exclude_none=True)
        if "caption_style" in e:
            e["caption_style"] = {k: v for k, v in e["caption_style"].items() if v is not None}
        elif "caption_preset" in e:
            edit.pop("caption_style", None)
        edit |= e
    if body.caption_words is not None:
        edit["caption_words"] = [w.model_dump() for w in body.caption_words]
    tmp_clip["edit"] = edit
    spec = render_spec(c, project, tmp_clip)
    spec.layout = edit.get("layout", "center")
    t = min(body.t, max(0.0, spec.duration - 0.1))
    out_dir = c.paths.temp_dir(project["id"])
    fd, name = tempfile.mkstemp(suffix=".jpg", dir=out_dir)
    os.close(fd)
    out = Path(name)
    try:
        render_preview_frame(spec, t, out)
        data = out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


def _media(path: str | None, kind: str) -> Path:
    if not path or not Path(path).exists():
        raise NotFound(f"{kind} not available")
    return Path(path)


@router.get("/clips/{clip_id}/video")
def clip_video(clip_id: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    return FileResponse(_media(clip["render_path"], "Video"), media_type="video/mp4", headers={"Cache-Control": "no-cache"})


@router.get("/clips/{clip_id}/thumb")
def clip_thumb(clip_id: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    return FileResponse(_media(clip["thumb_path"], "Thumbnail"), media_type="image/jpeg", headers={"Cache-Control": "no-cache"})


@router.get("/clips/{clip_id}/download")
def clip_download(clip_id: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    path = _media(clip["render_path"], "Video")
    slug = re.sub(r"[^A-Za-z0-9]+", "-", (clip["title"] or f"clip-{clip['rank']}")).strip("-")[:60] or "clip"
    return FileResponse(path, media_type="video/mp4", filename=f"{clip['rank']:02d}-{slug}.mp4")


@router.post("/clips/{clip_id}/music")
async def upload_music(clip_id: str, request: Request, filename: str = Query(..., max_length=255), c: AppContext = Depends(ctx)):
    """Background music must be supplied by the user (their own or properly licensed audio)."""
    clip = get_clip(c, clip_id)
    name = safe_filename(filename, "music.mp3")
    if Path(name).suffix.lower() not in AUDIO_EXTS:
        raise InvalidInput(f"Unsupported audio type. Supported: {', '.join(sorted(AUDIO_EXTS))}")
    size = int(request.headers.get("content-length") or 0)
    if not 0 < size <= 200 * MB:
        raise InvalidInput("Music file must be under 200 MB")
    ensure_space(c.paths.work, size, int(c.settings.get().min_free_gb * GB), "the music file")
    mdir = c.paths.project_dir(clip["project_id"]) / "music"
    mdir.mkdir(exist_ok=True)
    dest = mdir / f"{clip_id}_{name}"
    with dest.open("wb") as f:
        async for chunk in request.stream():
            f.write(chunk)
    edit = dict(clip["edit"])
    if edit.get("music_path") and edit["music_path"] != str(dest):
        Path(edit["music_path"]).unlink(missing_ok=True)
    edit["music_path"] = str(dest)
    edit.setdefault("music_volume", 0.12)
    c.db.update("clips", clip_id, {"edit": edit, "render_status": "stale" if clip["render_status"] in ("done", "failed") else clip["render_status"]})
    return public_clip(get_clip(c, clip_id))


@router.get("/caption-presets")
def caption_presets():
    return {k: v.model_dump() for k, v in PRESETS.items()}


__all__ = ["router", "public_clip", "CapWord", "resolve_style"]
