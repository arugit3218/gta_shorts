"""Project endpoints: create (URL / upload / local path), process, inspect, delete."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse, PlainTextResponse

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
from .schemas import CreateUrlProject, ImportPath, InspectUrl, StartProcess
from .sources import VIDEO_EXTS, safe_filename, validate_local_video

router = APIRouter(prefix="/api", tags=["projects"])


def _public(c: AppContext, p: dict) -> dict:
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
        "source_type": p["source_type"],
        "source_url": p["source_url"],
        "source_name": Path(p["source_path"]).name if p["source_path"] else None,
        "source_available": bool(p["source_path"]) and Path(p["source_path"]).exists(),
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
def list_projects(c: AppContext = Depends(ctx), limit: int = Query(100, le=500)):
    return [_public(c, p) for p in c.db.query("SELECT * FROM projects ORDER BY created_at DESC LIMIT ?", (limit,))]


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
    existing = c.db.one("SELECT id, name FROM projects WHERE source_url = ? ORDER BY created_at DESC LIMIT 1", (info["url"],))
    return info | {"estimate": est.as_dict(), "free_mb": round(free_bytes(c.paths.work) / MB), "existing_project": existing}


@router.post("/projects")
def create_url_project(body: CreateUrlProject, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    url = dl.validate_url(body.url)
    # Avoid re-downloading: reuse the downloaded file of an earlier project with the same URL.
    prev = c.db.one("SELECT source_path FROM projects WHERE source_url = ? AND source_path IS NOT NULL ORDER BY created_at DESC LIMIT 1", (url,))
    src = prev["source_path"] if prev and Path(prev["source_path"]).exists() else None
    p = _create(c, name=(body.name or url)[:200], source_type="url", source_url=url, source_path=src, source_owned=1, options=body.options.model_dump(exclude_none=True))
    if body.start:
        w.enqueue(p["id"], "process")
    return _public(c, get_project(c, p["id"]))


@router.post("/projects/upload")
async def upload_project(request: Request, filename: str = Query(..., max_length=255), c: AppContext = Depends(ctx)):
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
    p = _create(c, name=Path(filename).stem[:200], source_type="upload", source_path=str(dest), source_owned=1, duration=info.duration, width=info.width, height=info.height, fps=info.fps, options={})
    return _public(c, p) | {"has_audio": info.has_audio}


@router.post("/projects/import-path")
def import_path(body: ImportPath, c: AppContext = Depends(ctx)):
    """Use a local file in place — nothing is copied, and the app never deletes it."""
    src = validate_local_video(body.path)
    info = ff.probe(src)
    p = _create(c, name=(body.name or src.stem)[:200], source_type="path", source_path=str(src), source_owned=0, duration=info.duration, width=info.width, height=info.height, fps=info.fps, options={})
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
    if p["source_type"] != "url" and not (p["source_path"] and Path(p["source_path"]).exists()):
        raise MediaError("Source video is missing; it may have been moved or deleted.")
    opts = p["options"] | body.options.model_dump(exclude_none=True)
    c.db.update("projects", project_id, {"options": opts})
    job_id = w.enqueue(project_id, "process")
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
    freed = 0
    if p["source_path"] and Path(p["source_path"]).exists():
        others = c.db.one("SELECT COUNT(*) AS n FROM projects WHERE source_path = ? AND id != ?", (p["source_path"], project_id))
        if not (others and others["n"]):
            freed = Path(p["source_path"]).stat().st_size
            Path(p["source_path"]).unlink()
    c.db.update("projects", project_id, {"source_path": None})
    return {"freed_mb": round(freed / MB)}


@router.delete("/projects/{project_id}")
def delete_project(project_id: str, delete_exports: bool = False, delete_source: bool = False, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    p = get_project(c, project_id)
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
    if delete_source and p["source_owned"] and p["source_path"] and Path(p["source_path"]).exists():
        others = c.db.one("SELECT COUNT(*) AS n FROM projects WHERE source_path = ? AND id != ?", (p["source_path"], project_id))
        if not (others and others["n"]):
            freed += Path(p["source_path"]).stat().st_size
            Path(p["source_path"]).unlink()
    c.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))
    kept = None if delete_exports else str(pdir / "clips")
    return {"deleted": True, "freed_mb": round(freed / MB), "exports_kept_at": kept}
