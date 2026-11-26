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
from ..campaigns import posting
from ..campaigns import service as campaign_service
from ..campaigns.service import campaign_brief, features
from ..jobs.pipeline import clip_words, find_section, media_for_clip, render_spec
from ..jobs.worker import Worker
from ..media.captions import PRESETS, CapWord, resolve_style
from ..media.render import render_preview_frame
from ..storage import GB, MB, ensure_space
from ..transcription.types import Transcript
from .deps import ctx, get_clip, get_project, worker
from .schemas import ClipPatch, PostMark, PreviewFrame
from .sources import AUDIO_EXTS, safe_filename

router = APIRouter(prefix="/api", tags=["clips"])

RENDER_AFFECTING = {"start", "end", "edit", "caption_words", "reset_captions", "remove_music"}


def public_clip(c: dict, feat: dict | None = None, *, posted: dict | None = None, platforms: list[str] | None = None) -> dict:
    out = {k: c[k] for k in ("id", "project_id", "rank", "start", "end", "score", "title", "hook", "description", "category", "status", "render_status", "render_error", "created_at", "updated_at")}
    out["duration"] = round(c["end"] - c["start"], 2)
    out["analysis"] = c["analysis"]
    out["hooks"] = c["meta"].get("hooks", [])
    out["hashtags"] = c["meta"].get("hashtags", [])
    edit = dict(c["edit"])
    edit.pop("caption_words", None)
    edit["has_caption_edits"] = bool(c["edit"].get("caption_words"))
    edit["music_file"] = Path(edit.pop("music_path")).name if edit.get("music_path") else None
    out["edit"] = edit
    out["has_video"] = bool(c["render_path"]) and Path(c["render_path"]).exists()
    out["has_thumb"] = bool(c["thumb_path"]) and Path(c["thumb_path"]).exists()
    out["version"] = int(c["updated_at"] * 1000)
    out["exported"] = c["meta"].get("exported")
    out["posted"] = posted or {}
    if platforms is not None:
        out["post_platforms"] = platforms
    if feat is not None:
        out["features"] = feat
    return out


def _with_features(c: AppContext, project: dict, clips: list[dict]) -> list[dict]:
    _, brief = campaign_brief(c, project)
    posted = posting.posted_map(c.db, [x["id"] for x in clips])
    plats = posting.platforms_of(brief)
    return [public_clip(x, features(c, project, x, brief), posted=posted.get(x["id"]), platforms=plats) for x in clips]


@router.get("/projects/{project_id}/clips")
def list_clips(project_id: str, c: AppContext = Depends(ctx)):
    p = get_project(c, project_id)
    return _with_features(c, p, c.db.query("SELECT * FROM clips WHERE project_id = ? ORDER BY rank", (project_id,)))


@router.get("/clips/{clip_id}")
def clip_detail(clip_id: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    return _with_features(c, get_project(c, clip["project_id"]), [clip])[0]


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
    # The editor plays either the full source or (smart downloads) the section around this clip.
    offset, lo_lim, hi_lim = 0.0, 0.0, project["duration"]
    if not (project["source_path"] and Path(project["source_path"]).exists()):
        sec = find_section(c, project, clip["start"], clip["end"])
        if sec:
            offset, lo_lim, hi_lim = sec["start"], sec["start"], sec["end"]
    return {"start": clip["start"], "end": clip["end"], "words": words, "context_words": around, "edited": bool(clip["edit"].get("caption_words")), "source_duration": project["duration"], "source_offset": offset, "source_range": [lo_lim, hi_lim]}


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
        if "post" in e:
            e["post"] = {k: v for k, v in (edit.get("post") or {}).items()} | {k: v for k, v in e["post"].items() if v is not None}
            e["post"] = {k: v for k, v in e["post"].items() if v}
        if "polish" in e:
            e["polish"] = (edit.get("polish") or {}) | {k: v for k, v in e["polish"].items() if v is not None}
        if "hook_overlay" in e:
            e["hook_overlay"] = (edit.get("hook_overlay") or {}) | {k: v for k, v in e["hook_overlay"].items() if v is not None}
        if "music" in e and e["music"] != "upload" and edit.get("music_path"):
            Path(edit.pop("music_path")).unlink(missing_ok=True)
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
    video_changed = "start" in upd or "end" in upd or ({k: v for k, v in edit.items() if k != "post"} != {k: v for k, v in clip["edit"].items() if k != "post"})
    if video_changed and clip["render_status"] in ("done", "failed"):
        upd["render_status"] = "stale"
    if upd:
        c.db.update("clips", clip_id, upd)
    return clip_detail(clip_id, c)


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
    spec = render_spec(c, project, tmp_clip, for_preview=True)
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
    edit["music"] = "upload"
    edit.setdefault("music_volume", 0.25)
    c.db.update("clips", clip_id, {"edit": edit, "render_status": "stale" if clip["render_status"] in ("done", "failed") else clip["render_status"]})
    return clip_detail(clip_id, c)


@router.get("/clips/{clip_id}/source-info")
def clip_source_info(clip_id: str, c: AppContext = Depends(ctx)):
    """Where the clip sits in the playable source file (to preview a clip before it's rendered)."""
    clip = get_clip(c, clip_id)
    project = get_project(c, clip["project_id"])
    try:
        _, offset, *_ = media_for_clip(c, project, clip["start"], clip["end"])
    except Exception as e:  # noqa: BLE001 - a missing section is a normal state here
        return {"available": False, "reason": str(e)}
    return {"available": True, "start": round(clip["start"] - offset, 3), "end": round(clip["end"] - offset, 3)}


@router.get("/clips/{clip_id}/source")
def clip_source(clip_id: str, c: AppContext = Depends(ctx)):
    """Video the editor plays for trimming: the full source, or the downloaded section around the clip."""
    clip = get_clip(c, clip_id)
    project = get_project(c, clip["project_id"])
    path, *_ = media_for_clip(c, project, clip["start"], clip["end"])
    return FileResponse(path, media_type="video/mp4")


@router.get("/clips/{clip_id}/compliance")
def clip_compliance(clip_id: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    return campaign_service.compliance(c, get_project(c, clip["project_id"]), clip)


@router.post("/clips/{clip_id}/export")
def export_clip(clip_id: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    return campaign_service.export_clip(c, get_project(c, clip["project_id"]), clip)


@router.get("/clips/{clip_id}/export.zip")
def export_zip(clip_id: str, c: AppContext = Depends(ctx)):
    from starlette.background import BackgroundTask

    clip = get_clip(c, clip_id)
    project = get_project(c, clip["project_id"])
    exp = (clip["meta"] or {}).get("exported")
    # rebuild when the clip (video, caption, title) changed after the last export, so the zip is never stale
    if not exp or not Path(exp["path"]).exists() or float(clip.get("updated_at") or 0) > float(exp.get("at") or 0) + 2:  # +2 s: saving the export itself touches the clip
        exp = campaign_service.export_clip(c, project, clip)
    folder = Path(exp["path"])
    tmp_dir = c.paths.temp_dir(project["id"])
    dest = tmp_dir / f"{folder.name}.zip"
    campaign_service.zip_export(folder, dest)
    return FileResponse(dest, media_type="application/zip", filename=f"{folder.name}.zip", background=BackgroundTask(lambda: dest.unlink(missing_ok=True)))


# ---------------------------------------------------------------------------------- posted
@router.put("/clips/{clip_id}/posts/{platform}")
def mark_posted(clip_id: str, platform: str, body: PostMark, c: AppContext = Depends(ctx)):
    """Mark the clip as posted on one platform (optionally with the post's link)."""
    clip = get_clip(c, clip_id)
    if platform not in posting.ALL_PLATFORMS:
        raise InvalidInput(f"Unknown platform: {platform}")
    url = (body.url or "").strip()
    if url and not re.match(r"^https?://\S+$", url):
        raise InvalidInput("The post link should start with http:// or https://")
    posting.mark(c.db, clip["id"], platform, url or None)
    return {"posted": posting.posted_map(c.db, [clip["id"]]).get(clip["id"], {})}


@router.delete("/clips/{clip_id}/posts/{platform}")
def unmark_posted(clip_id: str, platform: str, c: AppContext = Depends(ctx)):
    clip = get_clip(c, clip_id)
    posting.unmark(c.db, clip["id"], platform)
    return {"posted": posting.posted_map(c.db, [clip["id"]]).get(clip["id"], {})}


@router.get("/caption-presets")
def caption_presets():
    return {k: v.model_dump() for k, v in PRESETS.items()}


__all__ = ["router", "public_clip", "CapWord", "resolve_style"]
