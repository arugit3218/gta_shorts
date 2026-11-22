"""System endpoints: status, dashboard, settings, models, jobs, storage."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import psutil
from fastapi import APIRouter, Depends, Query

from ..config import MODE_PRESETS, Mode
from ..context import AppContext
from ..errors import InvalidInput, NotFound
from ..jobs.worker import Worker
from ..llm import ollama
from ..media import ffmpeg as ff
from ..storage import GB, MB, dir_size, free_bytes
from ..transcription.service import list_models
from .deps import ctx, worker
from .projects import _job_public

router = APIRouter(prefix="/api", tags=["system"])


@router.get("/health")
def health():
    return {"ok": True}


@router.get("/system")
def system(c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    vm = psutil.virtual_memory()
    du = shutil.disk_usage(c.paths.work)
    s = c.settings.get()
    cur = w.current_job_id()
    queued = c.db.one("SELECT COUNT(*) AS n FROM jobs WHERE status = 'queued'")
    return {
        "mode": s.mode.value,
        "memory": {"total_mb": round(vm.total / MB), "available_mb": round(vm.available / MB), "percent": vm.percent},
        "disk": {"path": str(c.paths.work), "total_mb": round(du.total / MB), "free_mb": round(du.free / MB), "min_free_mb": round(s.min_free_gb * GB / MB), "low": du.free < s.min_free_gb * GB * 1.5},
        "tools": ff.tools_available(),
        "worker": {"current_job": cur, "queued": queued["n"] if queued else 0},
        "work_dir": str(c.paths.work),
        "privacy": {"bind": c.env.host, "model_downloads": c.env.allow_model_downloads, "llm_enabled": s.llm_enabled},
    }


@router.get("/dashboard")
def dashboard(c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    from .projects import _public

    db = c.db
    projects = db.one("SELECT COUNT(*) AS n, SUM(status = 'ready') AS ready FROM projects") or {}
    clips = db.one("SELECT COUNT(*) AS n, SUM(render_status = 'done') AS rendered, SUM(status = 'approved') AS approved FROM clips") or {}
    minutes = db.one("SELECT SUM(duration) AS s FROM projects WHERE status = 'ready'") or {}
    recent = [_public(c, p) for p in db.query("SELECT * FROM projects ORDER BY updated_at DESC LIMIT 6")]
    active = [_job_public(j) | {"project_id": j["project_id"]} for j in db.query("SELECT * FROM jobs WHERE status IN ('running','queued') ORDER BY created_at")]
    return {
        "projects": projects.get("n") or 0,
        "videos_processed": projects.get("ready") or 0,
        "clips": clips.get("n") or 0,
        "clips_rendered": clips.get("rendered") or 0,
        "clips_approved": clips.get("approved") or 0,
        "minutes_processed": round((minutes.get("s") or 0) / 60, 1),
        "recent": recent,
        "active_jobs": active,
        "disk_free_mb": round(free_bytes(c.paths.work) / MB),
    }


@router.get("/settings")
def get_settings(c: AppContext = Depends(ctx)):
    return c.settings.get().model_dump(mode="json")


@router.put("/settings")
def put_settings(patch: dict, c: AppContext = Depends(ctx)):
    if "work_dir" in patch and patch["work_dir"]:
        p = Path(str(patch["work_dir"])).expanduser()
        if not p.is_absolute():
            raise InvalidInput("Working directory must be an absolute path")
        if not (p.resolve().is_relative_to(Path.home().resolve()) or p.resolve().is_relative_to(Path("/Volumes"))):
            raise InvalidInput("Working directory must be in your home folder or on an external drive (/Volumes)")
        try:
            p.mkdir(parents=True, exist_ok=True)
            probe = p / ".clipstudio_write_test"
            probe.write_text("ok")
            probe.unlink()
        except OSError as e:
            raise InvalidInput(f"Can't write to that folder: {e.strerror}") from e
    if patch.get("mode") in ("balanced", "high"):
        preset = MODE_PRESETS[Mode(patch["mode"])]
        patch = {"output_height": preset["output_height"], **patch}
    if patch.get("mode") == "low":
        patch = {"output_height": 720, "llm_enabled": False, **patch}
    try:
        return c.settings.update(patch).model_dump(mode="json")
    except ValueError as e:
        raise InvalidInput(str(e).split("\n")[-1] if "\n" in str(e) else str(e)) from e


@router.get("/models")
def models(c: AppContext = Depends(ctx)):
    s = c.settings.get()
    ok, why = ollama.available(s.llm_url, s.llm_model, timeout=1.0)
    vm = psutil.virtual_memory()
    return {
        "transcription": list_models(),
        "selected": s.transcription_model,
        "llm": {"enabled": s.llm_enabled, "available": ok, "status": why, "model": s.llm_model, "url": s.llm_url},
        "ram_total_mb": round(vm.total / MB),
        "downloads_allowed": c.env.allow_model_downloads,
    }


@router.get("/jobs")
def jobs(project_id: str | None = None, active: bool = False, limit: int = Query(50, le=200), c: AppContext = Depends(ctx)):
    sql = "SELECT * FROM jobs WHERE 1=1"
    args: list = []
    if project_id:
        sql += " AND project_id = ?"
        args.append(project_id)
    if active:
        sql += " AND status IN ('queued','running')"
    sql += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    return [_job_public(j) | {"project_id": j["project_id"]} for j in c.db.query(sql, tuple(args))]


@router.get("/jobs/{job_id}")
def job(job_id: str, c: AppContext = Depends(ctx)):
    j = c.db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
    if j is None:
        raise NotFound("Job not found")
    return _job_public(j) | {"project_id": j["project_id"]}


@router.post("/jobs/{job_id}/cancel")
def cancel_job(job_id: str, w: Worker = Depends(worker)):
    if not w.cancel(job_id):
        raise InvalidInput("Job is not running or queued")
    return {"cancelling": True}


# ---------------------------------------------------------------------------------- storage
def _storage_items(c: AppContext) -> dict:
    work = c.paths.work
    projects = []
    for p in c.db.query("SELECT id, name, source_path, source_owned FROM projects ORDER BY created_at DESC"):
        pdir = work / "projects" / p["id"]
        src = Path(p["source_path"]) if p["source_path"] else None
        projects.append(
            {
                "id": p["id"],
                "name": p["name"],
                "clips_mb": round(dir_size(pdir / "clips") / MB, 1),
                "other_mb": round((dir_size(pdir) - dir_size(pdir / "clips")) / MB, 1),
                "source_mb": round(src.stat().st_size / MB, 1) if src and src.exists() and p["source_owned"] else 0,
                "source_owned": bool(p["source_owned"]),
                "source_exists": bool(src and src.exists()),
            }
        )
    temp = sum(dir_size(d) for d in (work / "projects").glob("*/tmp")) if (work / "projects").exists() else 0
    partial = sum(f.stat().st_size for f in work.rglob("*.part*") if f.is_file())
    known = {str(Path(p["source_path"])) for p in c.db.query("SELECT source_path FROM projects WHERE source_path IS NOT NULL")}
    orphans = [f for d in (c.paths.downloads, c.paths.uploads) for f in d.iterdir() if f.is_file() and str(f) not in known and ".part" not in f.name]
    orphan_projects = [d for d in (work / "projects").iterdir() if d.is_dir() and not c.db.one("SELECT 1 FROM projects WHERE id = ?", (d.name,))] if (work / "projects").exists() else []
    return {
        "work_dir": str(work),
        "free_mb": round(free_bytes(work) / MB),
        "total_mb": round(dir_size(work) / MB, 1),
        "downloads_mb": round(dir_size(c.paths.downloads) / MB, 1),
        "uploads_mb": round(dir_size(c.paths.uploads) / MB, 1),
        "cache_mb": round(dir_size(c.paths.cache) / MB, 2),
        "temp_mb": round(temp / MB, 1),
        "partial_mb": round(partial / MB, 1),
        "orphans": [{"name": f.name, "mb": round(f.stat().st_size / MB, 1)} for f in orphans],
        "orphan_project_dirs": [{"name": d.name, "mb": round(dir_size(d) / MB, 1)} for d in orphan_projects],
        "projects": projects,
        "_orphan_paths": orphans,
        "_orphan_dirs": orphan_projects,
    }


@router.get("/storage")
def storage(c: AppContext = Depends(ctx)):
    return {k: v for k, v in _storage_items(c).items() if not k.startswith("_")}


@router.post("/storage/cleanup")
def cleanup(body: dict, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Removes only regenerable data. Never deletes user-owned originals or finished clips."""
    targets = set(body.get("targets") or ["temp", "partial"])
    if not targets <= {"temp", "partial", "cache", "orphans"}:
        raise InvalidInput("Unknown cleanup target")
    if w.current_job_id() and targets & {"temp", "partial", "cache"}:
        raise InvalidInput("A job is running; wait for it to finish before cleaning temporary files.")
    work = c.paths.work
    freed = 0
    items = _storage_items(c)
    if "temp" in targets and (work / "projects").exists():
        for d in (work / "projects").glob("*/tmp"):
            freed += dir_size(d)
            shutil.rmtree(d, ignore_errors=True)
    if "partial" in targets:
        for f in list(work.rglob("*.part*")):
            if f.is_file():
                freed += f.stat().st_size
                f.unlink(missing_ok=True)
        for d in work.rglob("render_*"):
            if d.is_dir():
                freed += dir_size(d)
                shutil.rmtree(d, ignore_errors=True)
    if "cache" in targets:
        for f in c.paths.cache.glob("*"):
            freed += f.stat().st_size
            f.unlink(missing_ok=True)
    if "orphans" in targets:
        # Files the app downloaded/uploaded that no project references anymore.
        for f in items["_orphan_paths"]:
            freed += f.stat().st_size
            f.unlink(missing_ok=True)
        for d in items["_orphan_dirs"]:
            if not (d / "clips").exists() or not any((d / "clips").glob("*.mp4")):
                freed += dir_size(d)
                shutil.rmtree(d, ignore_errors=True)
    return {"freed_mb": round(freed / MB, 1), "at": time.time()}
