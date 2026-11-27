"""Project endpoints: create (URL / upload / local path), process, inspect, delete."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse
from starlette.background import BackgroundTask

from ..context import AppContext
from ..db import new_id
from ..errors import InvalidInput, MediaError, NotFound
from ..jobs.pipeline import project_options
from ..jobs.worker import Worker
from ..media import download as dl
from ..media import ffmpeg as ff
from ..storage import GB, MB, dir_size, ensure_space, estimate_processing, free_bytes
from ..config import DURATION_RANGES
from .deps import ctx, get_project, worker
from .schemas import AiPicks, BulkClipEdit, BulkUrls, ClipActions, CreateUrlProject, ImportPath, InspectUrl, ManualClip, ProjectPatch, StartProcess
from .sources import VIDEO_EXTS, safe_filename, validate_local_video

router = APIRouter(prefix="/api", tags=["projects"])


def montage_refs(c: AppContext) -> dict[str, list[dict]]:
    """source project id → montages that use it."""
    out: dict[str, list[dict]] = {}
    for m in c.db.query("SELECT id, name, options FROM projects WHERE source_type = 'montage'"):
        for sid in ((m["options"] or {}).get("montage") or {}).get("sources") or []:
            out.setdefault(sid, []).append({"id": m["id"], "name": m["name"]})
    return out


def stage_of(p: dict, refs: dict[str, list[dict]]) -> str:
    """active: started (or a montage) · footage: never processed, only used by a montage · not_started: uploaded/added, never started."""
    if p["source_type"] == "montage" or p["status"] != "new":
        return "active"
    return "footage" if p["id"] in refs else "not_started"


def _public(c: AppContext, p: dict, refs: dict[str, list[dict]] | None = None) -> dict:
    refs = montage_refs(c) if refs is None else refs
    job = c.db.one("SELECT * FROM jobs WHERE project_id = ? AND kind = 'process' ORDER BY created_at DESC LIMIT 1", (p["id"],))
    counts = c.db.one(
        "SELECT COUNT(*) AS clips, SUM(render_status = 'done') AS rendered, SUM(status = 'approved') AS approved, MAX(score) AS best "
        "FROM clips WHERE project_id = ?",
        (p["id"],),
    ) or {}
    thumb = c.db.one("SELECT id FROM clips WHERE project_id = ? AND thumb_path IS NOT NULL ORDER BY rank LIMIT 1", (p["id"],))
    return {
        "id": p["id"],
        "name": p["name"],
        "stage": stage_of(p, refs),
        "used_in": refs.get(p["id"], []),
        "source_type": p["source_type"],
        "source_url": p["source_url"],
        "source_name": Path(p["source_path"]).name if p["source_path"] else None,
        "source_available": (bool(p["source_path"]) and Path(p["source_path"]).exists()) or _has_sections(c, p) or p["source_type"] == "montage",
        "video_mode": "montage" if p["source_type"] == "montage" else "full" if (p["source_path"] and Path(p["source_path"]).exists()) else ("sections" if _has_sections(c, p) else "missing"),
        "content_mode": (p.get("extra") or {}).get("mode"),
        "campaign_id": p.get("campaign_id"),
        "campaign_name": _campaign_name(c, p.get("campaign_id")),
        "insights": {
            "heatmap": bool((p.get("extra") or {}).get("heatmap")),
            "chapters": len((p.get("extra") or {}).get("chapters") or []),
            "creator_subtitles": bool((p.get("extra") or {}).get("creator_subtitles")),
        },
        "source_owned": bool(p["source_owned"]),
        "duration": p["duration"],
        "width": p["width"],
        "height": p["height"],
        "fps": p["fps"],
        "status": p["status"],
        "error": p["error"],
        "options": p["options"],
        "created_at": p["created_at"],
        "updated_at": p["updated_at"],
        "clip_count": counts.get("clips") or 0,
        "rendered_count": counts.get("rendered") or 0,
        "approved_count": counts.get("approved") or 0,
        "best_score": counts.get("best"),
        "thumb_clip_id": thumb["id"] if thumb else None,
        "job": _job_public(job) if job else None,
    }


def _has_sections(c: AppContext, p: dict) -> bool:
    idx = c.paths.work / "projects" / p["id"] / "sections.json"
    return idx.exists() and idx.stat().st_size > 2


def _campaign_name(c: AppContext, cid: str | None) -> str | None:
    if not cid:
        return None
    row = c.db.one("SELECT name FROM campaigns WHERE id = ?", (cid,))
    return row["name"] if row else None


def _check_campaign(c: AppContext, cid: str | None) -> str | None:
    if not cid:
        return None
    if not c.db.one("SELECT 1 FROM campaigns WHERE id = ?", (cid,)):
        raise InvalidInput("Campaign not found")
    return cid


def _job_public(j: dict) -> dict:
    return {k: j[k] for k in ("id", "kind", "status", "stage", "progress", "message", "stages", "error", "created_at", "started_at", "finished_at")} | {
        "payload": j["payload"]
    }


def _create(c: AppContext, **values) -> dict:
    now = time.time()
    pid = new_id()
    c.db.insert("projects", {"id": pid, "status": "new", "created_at": now, "updated_at": now, **values})
    return get_project(c, pid)


@router.get("/projects")
def list_projects(c: AppContext = Depends(ctx), limit: int = Query(100, le=500), stage: str | None = Query(None, pattern="^(active|not_started|footage)$")):
    refs = montage_refs(c)
    out = [_public(c, p, refs) for p in c.db.query("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,))]
    return [p for p in out if p["stage"] == stage] if stage else out


@router.get("/projects/{project_id}")
def project_detail(project_id: str, c: AppContext = Depends(ctx)):
    return _public(c, get_project(c, project_id))


@router.post("/sources/inspect-url")
def inspect_url(body: InspectUrl, c: AppContext = Depends(ctx)):
    """Network request: asks the video site for metadata (no download)."""
    info = dl.inspect(body.url, c.settings.get().max_download_height)
    s = c.settings.get()
    dmin, dmax = DURATION_RANGES[s.duration_preset]
    est = estimate_processing(info["duration"] or 600, source_bytes=info["estimated_bytes"] or int((info["duration"] or 600) * 1.2 * MB), clips=s.max_clips, clip_seconds=(dmin + dmax) / 2, output_height=s.output_height)
    same = _videos_with_link(c, info["url"])
    existing = {"id": same[0]["id"], "name": same[0]["name"]} if same else None
    return info | {"estimate": est.as_dict(), "free_mb": round(free_bytes(c.paths.work) / MB), "existing_project": existing, "existing_projects": same}


def _same_link_rows(c: AppContext, url: str) -> list[dict]:
    """Projects made from the same video link (youtu.be vs youtube.com, tracking parameters… ignored), newest first."""
    key = dl.same_video_key(url)
    rows = c.db.query("SELECT * FROM projects WHERE source_url IS NOT NULL ORDER BY created_at DESC")
    return [r for r in rows if dl.same_video_key(r["source_url"]) == key]


def _videos_with_link(c: AppContext, url: str) -> list[dict]:
    """Same-link projects the user knows as videos (not montage footage), with their campaign's name."""
    refs = montage_refs(c)
    names = {r["id"]: r["name"] for r in c.db.query("SELECT id, name FROM campaigns")}
    out = []
    for r in _same_link_rows(c, url):
        pub = _public(c, r, refs)
        if pub["stage"] == "footage":
            continue
        out.append({"id": pub["id"], "name": pub["name"], "status": pub["status"], "stage": pub["stage"], "clip_count": pub["clip_count"],
                    "campaign_id": pub.get("campaign_id"), "campaign_name": names.get(pub.get("campaign_id") or "")})
    return out[:5]


def _create_url(c: AppContext, w: Worker, url: str, name: str | None, options: dict, campaign_id: str | None, start: bool) -> dict:
    url = dl.validate_url(url)
    # Avoid re-downloading: reuse the downloaded files of an earlier project with the same video link.
    prev = next((r for r in _same_link_rows(c, url) if r["source_path"] or r["audio_path"]), None)
    src = prev["source_path"] if prev and prev["source_path"] and Path(prev["source_path"]).exists() else None
    aud = prev["audio_path"] if prev and prev["audio_path"] and Path(prev["audio_path"]).exists() else None
    reuse = {}
    if prev and (src or aud):
        reuse = {"extra": prev["extra"] or {}, "duration": prev["duration"], "width": prev["width"], "height": prev["height"], "fps": prev["fps"]}
    p = _create(c, name=(name or url)[:200], source_type="url", source_url=url, source_path=src, audio_path=aud, source_owned=1, options=options, campaign_id=campaign_id, **reuse)
    if start:
        w.enqueue(p["id"], "process")
    return _public(c, get_project(c, p["id"]))


@router.post("/projects")
def create_url_project(body: CreateUrlProject, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    cid = _check_campaign(c, body.campaign_id)
    if not body.allow_duplicate:
        dup = _duplicate_in(c, body.url, cid)
        if dup:
            raise InvalidInput(f"This video is already in {'this campaign' if cid else 'your videos'} as “{dup['name']}”. Open it, or choose to make another copy.")
    return _create_url(c, w, body.url, body.name, body.options.model_dump(exclude_none=True), cid, body.start)


def _duplicate_in(c: AppContext, url: str, campaign_id: str | None) -> dict | None:
    """A video with the same link already in this campaign (or, without one, among videos with no campaign)."""
    return next((v for v in _videos_with_link(c, url) if (v["campaign_id"] or None) == (campaign_id or None)), None)


@router.post("/projects/bulk")
def create_bulk(body: BulkUrls, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Queue many links at once; they are processed one after another."""
    cid = _check_campaign(c, body.campaign_id)
    created, errors, existing = [], [], []
    seen = set()
    for raw in body.urls:
        u = raw.strip()
        if not u or dl.same_video_key(u) in seen:
            continue
        seen.add(dl.same_video_key(u))
        dup = _duplicate_in(c, u, cid)
        if dup:  # already added here: don't make a second copy (and second set of clips)
            existing.append({"url": u[:300], "project": dup})
            continue
        try:
            # not started: no options stored, so starting later uses the campaign's settings as they are then
            created.append(_create_url(c, w, u, None, body.options.model_dump(exclude_none=True) if body.start else {}, cid, body.start))
        except Exception as e:
            errors.append({"url": u[:300], "error": str(e)})
    return {"created": created, "errors": errors, "existing": existing}


@router.patch("/projects/{project_id}")
def patch_project(project_id: str, body: ProjectPatch, c: AppContext = Depends(ctx)):
    p = get_project(c, project_id)
    upd: dict = {}
    if body.name is not None and body.name.strip():
        upd["name"] = body.name.strip()[:200]
    if body.campaign_id is not None:
        upd["campaign_id"] = _check_campaign(c, body.campaign_id) if body.campaign_id else None
    if body.stream_layout is not None or body.clear_stream_layout:
        opts = dict(p["options"] or {})
        if body.clear_stream_layout:
            opts.pop("stream_layout", None)
        else:
            opts["stream_layout"] = body.stream_layout.model_dump()
        upd["options"] = opts
        # clips framed by Auto/Stream will look different now
        for cl in c.db.query("SELECT id, edit FROM clips WHERE project_id = ? AND render_status IN ('done','failed')", (project_id,)):
            if (cl["edit"] or {}).get("layout", "auto") in ("auto", "stream"):
                c.db.update("clips", cl["id"], {"render_status": "stale"})
    if upd:
        c.db.update("projects", project_id, upd)
        if "campaign_id" in upd and upd["campaign_id"] != p.get("campaign_id"):
            c.db.execute("UPDATE clips SET render_status = 'stale' WHERE project_id = ? AND render_status IN ('done','failed')", (project_id,))
            if upd["campaign_id"]:
                # campaigns post to Reels/Shorts: vertical, at the campaign's resolution
                from ..campaigns.models import Brief

                brief = Brief.model_validate(c.db.one("SELECT brief FROM campaigns WHERE id = ?", (upd["campaign_id"],))["brief"])
                height = 1080 if brief.logo.mode == "template" else brief.output_height
                for cl in c.db.query("SELECT id, edit FROM clips WHERE project_id = ?", (project_id,)):
                    c.db.update("clips", cl["id"], {"edit": cl["edit"] | {"aspect_ratio": "9:16", "output_height": height}})
    return _public(c, get_project(c, project_id))


@router.post("/projects/upload")
async def upload_project(request: Request, filename: str = Query(..., max_length=255), campaign_id: str | None = Query(None, max_length=40), c: AppContext = Depends(ctx)):
    """Raw-body upload streamed straight to the working directory (no temp copy)."""
    name = safe_filename(filename)
    if Path(name).suffix.lower() not in VIDEO_EXTS:
        raise InvalidInput(f"Unsupported file type. Supported: {', '.join(sorted(VIDEO_EXTS))}")
    size = int(request.headers.get("content-length") or 0)
    if size <= 0:
        raise InvalidInput("Empty upload")
    settings = c.settings.get()
    ensure_space(c.paths.uploads, size, int(settings.min_free_gb * GB), "this upload")
    dest = c.paths.uploads / f"{new_id()}_{name}"
    part = dest.with_name(dest.name + ".part")
    written = 0
    try:
        with part.open("wb") as f:
            async for chunk in request.stream():
                written += len(chunk)
                if written > size + MB:
                    raise InvalidInput("Upload larger than declared size")
                f.write(chunk)
        os.replace(part, dest)
        info = ff.probe(dest)
    except Exception:
        part.unlink(missing_ok=True)
        dest.unlink(missing_ok=True)
        raise
    p = _create(c, name=Path(filename).stem[:200], source_type="upload", source_path=str(dest), source_owned=1, duration=info.duration, width=info.width, height=info.height, fps=info.fps, options={}, campaign_id=_check_campaign(c, campaign_id))
    return _public(c, p) | {"has_audio": info.has_audio}


@router.post("/projects/import-path")
def import_path(body: ImportPath, c: AppContext = Depends(ctx)):
    """Use a local file in place — nothing is copied, and the app never deletes it."""
    src = validate_local_video(body.path)
    info = ff.probe(src)
    p = _create(c, name=(body.name or src.stem)[:200], source_type="path", source_path=str(src), source_owned=0, duration=info.duration, width=info.width, height=info.height, fps=info.fps, options={}, campaign_id=_check_campaign(c, body.campaign_id))
    return _public(c, p) | {"has_audio": info.has_audio}


@router.get("/projects/{project_id}/estimate")
def estimate(project_id: str, c: AppContext = Depends(ctx)):
    p = get_project(c, project_id)
    s = c.settings.get()
    o = project_options(s, p["options"])
    dmin, dmax = DURATION_RANGES[o["duration_preset"]]
    est = estimate_processing(p["duration"] or 600, clips=o["max_clips"], clip_seconds=(dmin + dmax) / 2, output_height=o["output_height"])
    free = free_bytes(c.paths.work)
    return {"estimate": est.as_dict(), "free_mb": round(free / MB), "min_free_mb": round(s.min_free_gb * GB / MB), "ok": free - est.total > s.min_free_gb * GB}


@router.post("/projects/{project_id}/process")
def start_process(project_id: str, body: StartProcess, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    p = get_project(c, project_id)
    if p["source_type"] not in ("url", "montage") and not (p["source_path"] and Path(p["source_path"]).exists()):
        raise MediaError("Source video is missing; it may have been moved or deleted.")
    opts = p["options"] | body.options.model_dump(exclude_none=True)
    c.db.update("projects", project_id, {"options": opts})
    job_id = w.enqueue(project_id, "process", {"keep_clips": body.keep_clips} if body.keep_clips else None)
    return {"job_id": job_id, "project": _public(c, get_project(c, project_id))}


@router.get("/projects/{project_id}/source")
def project_source(project_id: str, c: AppContext = Depends(ctx)):
    p = get_project(c, project_id)
    if not p["source_path"] or not Path(p["source_path"]).exists():
        raise NotFound("Source video not available")
    return FileResponse(p["source_path"], media_type="video/mp4")


@router.get("/projects/{project_id}/transcript.{fmt}")
def transcript(project_id: str, fmt: str, c: AppContext = Depends(ctx)):
    get_project(c, project_id)
    if fmt not in ("json", "srt"):
        raise NotFound("Unknown format")
    path = c.paths.project_dir(project_id) / f"transcript.{fmt}"
    if not path.exists():
        raise NotFound("Transcript not available yet")
    if fmt == "srt":
        return PlainTextResponse(path.read_text(), headers={"Content-Disposition": f'attachment; filename="{project_id}.srt"'})
    return FileResponse(path, media_type="application/json")


@router.post("/projects/{project_id}/delete-source")
def delete_source(project_id: str, c: AppContext = Depends(ctx)):
    """Free disk by deleting a source the app downloaded/uploaded. Never touches user files."""
    p = get_project(c, project_id)
    if not p["source_owned"]:
        raise InvalidInput("This source is your own file; ClipStudio never deletes it.")
    freed = _drop_owned_files(c, p)
    sections = c.paths.project_dir(project_id) / "sections"
    if sections.exists():
        freed += dir_size(sections)
        shutil.rmtree(sections, ignore_errors=True)
        (c.paths.project_dir(project_id) / "sections.json").unlink(missing_ok=True)
    c.db.update("projects", project_id, {"source_path": None, "audio_path": None})
    return {"freed_mb": round(freed / MB)}


def _drop_owned_files(c: AppContext, p: dict) -> int:
    """Delete the downloaded video/audio of a project unless another project still uses them."""
    freed = 0
    for col in ("source_path", "audio_path"):
        path = p.get(col)
        if path and Path(path).exists():
            others = c.db.one(f"SELECT COUNT(*) AS n FROM projects WHERE {col} = ? AND id != ?", (path, p["id"]))
            if not (others and others["n"]):
                freed += Path(path).stat().st_size
                Path(path).unlink()
    return freed


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, delete_exports: bool = False, delete_source: bool = False, delete_footage: bool = False, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    p = get_project(c, project_id)
    footage_freed, footage_deleted = 0, 0
    if delete_footage and p["source_type"] == "montage":
        # source videos that were never processed and that no other montage uses
        refs = montage_refs(c)
        for sid in ((p["options"] or {}).get("montage") or {}).get("sources") or []:
            sp = c.db.one("SELECT * FROM projects WHERE id = ?", (sid,))
            if sp is None or stage_of(sp, refs) != "footage" or any(m["id"] != project_id for m in refs.get(sid, [])):
                continue
            sdir = c.paths.project_dir(sid)
            footage_freed += dir_size(sdir)
            shutil.rmtree(sdir, ignore_errors=True)
            if sp["source_owned"]:
                footage_freed += _drop_owned_files(c, sp)
            c.db.execute("DELETE FROM projects WHERE id = ?", (sid,))
            footage_deleted += 1
    for j in c.db.query("SELECT id FROM jobs WHERE project_id = ? AND status IN ('queued','running')", (project_id,)):
        w.cancel(j["id"])
    if w.current_job_id() and c.db.one("SELECT 1 FROM jobs WHERE id = ? AND project_id = ?", (w.current_job_id(), project_id)):
        raise InvalidInput("Project is still processing; cancel it and try again in a moment.")
    pdir = c.paths.project_dir(project_id)
    freed = 0
    if delete_exports:
        freed += dir_size(pdir)
        shutil.rmtree(pdir, ignore_errors=True)
    else:
        # keep rendered clips (exports); remove everything else
        for child in pdir.iterdir():
            if child.name != "clips":
                freed += dir_size(child)
                shutil.rmtree(child, ignore_errors=True) if child.is_dir() else child.unlink(missing_ok=True)
    if delete_source and p["source_owned"]:
        freed += _drop_owned_files(c, p)
    c.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    kept = None if delete_exports else str(pdir / "clips")
    return {"deleted": True, "freed_mb": round((freed + footage_freed) / MB), "exports_kept_at": kept, "footage_deleted": footage_deleted}


# ---------------------------------------------------------------------------------- A11: quick / manual / AI picks
def _new_clips(c: AppContext, w: Worker, project: dict, ranges: list[dict], *, origin: str, render: bool, replace: bool = False) -> list[dict]:
    from ..analysis.highlights import score_range, snap_to_words
    from ..jobs.pipeline import insert_clip, load_audio_profile, load_transcript, metadata_for, project_options
    from .clips import public_clip

    t = load_transcript(c, project["id"])
    if t is None:
        raise InvalidInput("Process the video first (the transcript is needed for captions).")
    settings = c.settings.get()
    opts = project_options(settings, project["options"])
    weights = settings.weights.normalised()
    audio = load_audio_profile(c, project)
    dur = project["duration"] or t.duration
    if replace:
        from ..campaigns.posting import posted_ids

        posted = posted_ids(c.db)  # posted clips keep their post history
        for old in c.db.query("SELECT id, render_path, thumb_path FROM clips WHERE project_id = ?", (project["id"],)):
            if old["id"] in posted:
                continue
            for pth in (old["render_path"], old["thumb_path"]):
                if pth:
                    Path(pth).unlink(missing_ok=True)
            c.db.execute("DELETE FROM clips WHERE id = ?", (old["id"],))
    rank0 = (c.db.one("SELECT MAX(rank) AS r FROM clips WHERE project_id = ?", (project["id"],)) or {}).get("r") or 0
    out = []
    for k, r in enumerate(ranges, 1):
        s, e = r["start"], r["end"]
        if r.get("snap", True):
            s, e = snap_to_words(t.words, s, e, dur)
        cand = score_range(t, audio, s, e, source_height=project.get("height") or 1080, weights=weights)
        m = metadata_for(c, project, t, cand) if cand.i1 > cand.i0 else {"title": "", "hooks": [], "description": "", "hashtags": []}
        if r.get("title"):
            m["title"] = r["title"]
        if r.get("hook"):
            m["hooks"] = [r["hook"], *[h for h in m.get("hooks", []) if h != r["hook"]]][:3]
        if r.get("hashtags"):
            m["hashtags"] = r["hashtags"]
        reasons = list(cand.score.reasons)
        if r.get("reason"):
            reasons.insert(0, f"AI pick: {r['reason']}")
        total = cand.score.total
        if r.get("score") is not None:
            # blend: the AI's opinion counts, but our transcript evidence keeps it honest
            total = round(0.5 * total + 0.5 * max(0.0, min(100.0, r["score"])), 1)
        cid = insert_clip(c, project, cand, rank0 + k, m, opts, weights, total=total, reasons=reasons, origin=origin)
        if render:
            w.enqueue(project["id"], "render", {"clip_id": cid})
        out.append(public_clip(c.db.one("SELECT * FROM clips WHERE id = ?", (cid,))))
    if project["status"] != "ready":
        c.db.update("projects", project["id"], {"status": "ready", "error": None})
    return out


@router.post("/projects/{project_id}/clips")
def add_manual_clip(project_id: str, body: ManualClip, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Quick clip: your own in/out points → campaign template → checker → export."""
    p = get_project(c, project_id)
    dur = p["duration"] or 0
    if not (0 <= body.start < body.end <= dur + 0.05):
        raise InvalidInput("Start must be before end and within the video")
    if body.end - body.start < 3 or body.end - body.start > 180:
        raise InvalidInput("Clips must be between 3 seconds and 3 minutes")
    return _new_clips(c, w, p, [{"start": body.start, "end": min(body.end, dur), "title": body.title, "snap": body.snap}], origin="manual", render=body.render)[0]


@router.get("/projects/{project_id}/ai-prompt")
def ai_prompt(project_id: str, c: AppContext = Depends(ctx)):
    from ..analysis.ai_picks import build_prompt
    from ..campaigns.service import campaign_brief
    from ..jobs.pipeline import load_transcript, project_options

    p = get_project(c, project_id)
    t = load_transcript(c, project_id)
    if t is None or not t.sentences:
        raise InvalidInput("Process the video first: the AI prompt is built from the transcript.")
    o = project_options(c.settings.get(), p["options"])
    dmin, dmax = DURATION_RANGES[o["duration_preset"]]
    _, brief = campaign_brief(c, p)
    notes = ""
    if brief:
        if brief.min_duration:
            dmin = max(dmin, brief.min_duration)
        if brief.max_duration:
            dmax = min(dmax, brief.max_duration)
        notes = "\n".join(x for x in [brief.content_notes, *brief.rules[:12]] if x)
    prompt = build_prompt(t, min_dur=dmin, max_dur=max(dmin + 5, dmax), max_clips=o["max_clips"], title=p["name"], campaign_notes=notes)
    return {"prompt": prompt, "chars": len(prompt), "sentences": len(t.sentences)}


@router.post("/projects/{project_id}/ai-picks")
def ai_picks(project_id: str, body: AiPicks, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    from ..analysis.ai_picks import parse_picks

    p = get_project(c, project_id)
    picks, problems = parse_picks(body.text, p["duration"] or 0)
    if not picks:
        raise InvalidInput(" ".join(problems) or "No clips found in the pasted answer.")
    clips = _new_clips(c, w, p, picks, origin="ai", render=body.render, replace=body.replace)
    return {"created": len(clips), "clips": clips, "problems": problems}


# ---------------------------------------------------------------------------------- framing (Phase 1.5)
def _frame_source(c: AppContext, p: dict, t: float | None) -> tuple[Path, float, float]:
    """(video file, time inside it, source-timeline time) for a still frame of the project."""
    from ..jobs.pipeline import _sections_index

    if p["source_path"] and Path(p["source_path"]).exists():
        dur = p["duration"] or 0
        tt = dur * 0.3 if t is None else max(0.0, min(dur - 0.1, t))
        return Path(p["source_path"]), tt, tt
    _, secs = _sections_index(c, p["id"])
    if not secs:
        raise NotFound("No video downloaded for this project yet")
    if t is not None:
        for sec in secs:
            if sec["start"] <= t <= sec["end"]:
                return Path(sec["path"]), t - sec["start"], t
    sec = secs[0]
    mid = (sec["end"] - sec["start"]) / 2
    return Path(sec["path"]), mid, sec["start"] + mid


@router.get("/projects/{project_id}/frame")
def project_frame(project_id: str, t: float | None = Query(None, ge=0), c: AppContext = Depends(ctx)):
    """A still frame of the source (640 px wide), for marking the webcam box."""
    import tempfile

    from fastapi.responses import Response

    p = get_project(c, project_id)
    path, tt, _ = _frame_source(c, p, t)
    fd, name = tempfile.mkstemp(suffix=".jpg", dir=c.paths.temp_dir(project_id))
    os.close(fd)
    out = Path(name)
    try:
        ff.run_ffmpeg(["-ss", f"{tt:.2f}", "-i", str(path), "-frames:v", "1", "-vf", "scale=640:-2", "-q:v", "4", "-update", "1", str(out)], timeout=60)
        data = out.read_bytes()
    finally:
        out.unlink(missing_ok=True)
    return Response(content=data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


@router.post("/projects/{project_id}/detect-facecam")
def detect_facecam(project_id: str, c: AppContext = Depends(ctx)):
    """Look for a streamer's webcam overlay in a few short samples of the video."""
    from ..jobs.pipeline import _sections_index
    from ..media.reframe import FaceTrack, detect_faces, facecam_rect

    p = get_project(c, project_id)
    windows: list[tuple[Path, float, float]] = []
    if p["source_path"] and Path(p["source_path"]).exists():
        dur = p["duration"] or 60
        for f in (0.2, 0.5, 0.8):
            s = max(0.0, dur * f - 6)
            windows.append((Path(p["source_path"]), s, min(dur, s + 12)))
    else:
        _, secs = _sections_index(c, p["id"])
        for sec in secs[:3]:
            length = sec["end"] - sec["start"]
            windows.append((Path(sec["path"]), max(0.0, length / 2 - 6), min(length, length / 2 + 6)))
    if not windows:
        raise NotFound("No video downloaded for this project yet")
    merged = FaceTrack()
    for path, s, e in windows:
        try:
            tr = detect_faces(path, s, e)
        except Exception:
            continue
        merged.times += tr.times
        merged.faces += tr.faces
    cam = facecam_rect(merged)
    return {"cam": cam, "samples": len(merged.faces), "found": cam is not None}


@router.post("/projects/{project_id}/clips/bulk")
def bulk_edit_clips(project_id: str, body: BulkClipEdit, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Set framing/format (or a whole recipe) for many clips at once and queue the re-renders."""
    where = {"all": "", "approved": " AND status = 'approved'", "unrejected": " AND status != 'rejected'"}[body.scope]
    patch = {k: v for k, v in {"layout": body.layout, "aspect_ratio": body.aspect_ratio, "output_height": body.output_height}.items() if v is not None}
    recipe = None
    if body.recipe_id:
        from .recipes import apply_to_edit, get_recipe

        recipe = get_recipe(c, body.recipe_id)["recipe"]
    project = get_project(c, project_id)
    n = 0
    rows = c.db.query(f"SELECT id, edit, render_status FROM clips WHERE project_id = ?{where} ORDER BY rank", (project_id,))
    if body.ids is not None:  # just the selected clips
        wanted = set(body.ids)
        rows = [r for r in c.db.query("SELECT id, edit, render_status FROM clips WHERE project_id = ? ORDER BY rank", (project_id,)) if r["id"] in wanted]
    for cl in rows:
        edit = (cl["edit"] or {}) | patch
        if recipe:
            edit = apply_to_edit(recipe, edit, campaign=bool(project.get("campaign_id")))
        if body.caption_position is not None or body.caption_position_auto:
            cs = dict(edit.get("caption_style") or {})
            if body.caption_position_auto:
                cs.pop("position", None)
            else:
                cs["position"] = round(body.caption_position, 3)
            edit["caption_style"] = cs
        if edit == (cl["edit"] or {}):
            continue  # nothing changes for this clip: don't mark it outdated or re-render it
        upd = {"edit": edit}
        if cl["render_status"] in ("done", "failed"):
            upd["render_status"] = "stale"
        c.db.update("clips", cl["id"], upd)
        if body.render:
            w.enqueue(project_id, "render", {"clip_id": cl["id"]})
        n += 1
    return {"updated": n, "rendering": n if body.render else 0}


@router.post("/projects/{project_id}/clips/actions")
def clip_actions(project_id: str, body: ClipActions, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Approve / reject / un-review / render several clips at once."""
    get_project(c, project_id)
    rows = c.db.query(f"SELECT id, status, render_status FROM clips WHERE project_id = ? AND id IN ({','.join('?' * len(body.ids))})", (project_id, *body.ids))
    status = {"approve": "approved", "reject": "rejected", "unreview": "candidate"}.get(body.action)
    rendering = 0
    for r in rows:
        if status:
            c.db.update("clips", r["id"], {"status": status})
        elif r["render_status"] not in ("queued", "rendering"):
            w.enqueue(project_id, "render", {"clip_id": r["id"]})
            rendering += 1
    return {"updated": len(rows), "rendering": rendering}


def _slug(text: str, n: int = 40) -> str:
    keep = "".join(ch if ch.isalnum() else "-" for ch in (text or "").lower())
    return "-".join(x for x in keep.split("-") if x)[:n] or "clip"


@router.get("/projects/{project_id}/clips.zip")
def clips_zip(project_id: str, ids: str = Query("", max_length=8000), c: AppContext = Depends(ctx)):
    """Rendered MP4s of the chosen clips (all rendered approved clips when no ids are given) in one zip."""
    import tempfile
    import zipfile

    p = get_project(c, project_id)
    wanted = [x for x in ids.split(",") if x]
    if wanted:
        rows = c.db.query(f"SELECT * FROM clips WHERE project_id = ? AND id IN ({','.join('?' * len(wanted))}) ORDER BY rank", (project_id, *wanted))
    else:
        rows = c.db.query("SELECT * FROM clips WHERE project_id = ? AND status = 'approved' ORDER BY rank", (project_id,))
    files = [(r, Path(r["render_path"])) for r in rows if r["render_status"] == "done" and r["render_path"] and Path(r["render_path"]).exists()]
    if not files:
        raise InvalidInput("None of these clips has a finished render yet. Render them first.")
    out_dir = c.paths.project_dir(project_id)
    ensure_space(out_dir, sum(f.stat().st_size for _, f in files) + 10 * MB, int(c.settings.get().min_free_gb * GB), "zipping clips")
    fd, tmp = tempfile.mkstemp(prefix="clips_", suffix=".zip", dir=out_dir)
    os.close(fd)
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:  # MP4 is already compressed
        for r, f in files:
            z.write(f, f"{r['rank']:02d}_{_slug(r['title'])}.mp4")
    name = f"{_slug(p['name'], 60)}_clips.zip"
    return FileResponse(tmp, media_type="application/zip", filename=name, background=BackgroundTask(lambda: Path(tmp).unlink(missing_ok=True)))
