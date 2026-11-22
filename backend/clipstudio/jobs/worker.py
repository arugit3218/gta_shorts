"""Sequential job worker: exactly one heavy job runs at a time.

- Jobs are persisted in SQLite, so the queue survives restarts.
- On startup, jobs left 'running' by a crash/shutdown are re-queued (stages are cached, so
  re-running skips finished work).
- Cancellation is cooperative (checked between steps) and kills running subprocesses.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Callable

from ..cancel import CancelToken
from ..context import AppContext
from ..db import new_id
from ..errors import Cancelled, ClipStudioError
from . import pipeline

log = logging.getLogger(__name__)

HANDLERS: dict[str, Callable] = {"process": pipeline.run_process, "render": pipeline.run_render}


class Worker:
    def __init__(self, ctx: AppContext):
        self.ctx = ctx
        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._current: tuple[str, CancelToken] | None = None
        self._lock = threading.Lock()

    # ---- lifecycle --------------------------------------------------------------------
    def start(self) -> None:
        self.recover()
        self._thread = threading.Thread(target=self._loop, name="clipstudio-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Graceful shutdown: cancel the running job (it'll be re-queued on next start)."""
        self._stop.set()
        self._wake.set()
        with self._lock:
            cur = self._current
        if cur:
            cur[1].cancel()
        if self._thread:
            self._thread.join(timeout)

    def recover(self) -> int:
        db = self.ctx.db
        n = db.execute("UPDATE jobs SET status = 'queued', message = 'Resumed after restart' WHERE status = 'running'")
        db.execute("UPDATE clips SET render_status = 'pending' WHERE render_status = 'rendering'")
        db.execute(
            "UPDATE projects SET status = 'queued' WHERE status = 'processing' AND id IN (SELECT project_id FROM jobs WHERE status = 'queued')"
        )
        if n:
            log.info("Recovered %d interrupted job(s)", n)
        return n

    # ---- queue API ----------------------------------------------------------------------
    def enqueue(self, project_id: str, kind: str, payload: dict | None = None) -> str:
        if kind not in HANDLERS:
            raise ValueError(kind)
        db = self.ctx.db
        if kind == "process":
            active = db.one("SELECT id FROM jobs WHERE project_id = ? AND kind = 'process' AND status IN ('queued','running')", (project_id,))
            if active:
                return active["id"]
            db.update("projects", project_id, {"status": "queued", "error": None})
        else:
            clip_id = (payload or {}).get("clip_id")
            active = db.one(
                "SELECT id FROM jobs WHERE kind = 'render' AND status = 'queued' AND json_extract(payload, '$.clip_id') = ?", (clip_id,)
            )
            if active:
                return active["id"]
            db.update("clips", clip_id, {"render_status": "queued"})
        job_id = new_id()
        db.insert("jobs", {"id": job_id, "project_id": project_id, "kind": kind, "payload": payload or {}, "status": "queued", "created_at": time.time()})
        self._wake.set()
        return job_id

    def cancel(self, job_id: str) -> bool:
        db = self.ctx.db
        job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        if job is None:
            return False
        if job["status"] == "queued":
            db.update("jobs", job_id, {"status": "cancelled", "finished_at": time.time(), "message": "Cancelled"})
            self._after_cancel(job)
            return True
        with self._lock:
            cur = self._current
        if cur and cur[0] == job_id:
            cur[1].cancel()
            return True
        return False

    def current_job_id(self) -> str | None:
        with self._lock:
            return self._current[0] if self._current else None

    # ---- loop -------------------------------------------------------------------------------
    def _next(self) -> dict | None:
        return self.ctx.db.one("SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1")

    def _loop(self) -> None:
        while not self._stop.is_set():
            job = self._next()
            if job is None:
                self._wake.wait(timeout=2.0)
                self._wake.clear()
                continue
            self._run(job)

    def _run(self, job: dict) -> None:
        db = self.ctx.db
        token = CancelToken()
        with self._lock:
            self._current = (job["id"], token)
        db.update("jobs", job["id"], {"status": "running", "started_at": time.time(), "error": None})
        log.info("Job %s (%s) started for project %s", job["id"], job["kind"], job["project_id"])
        try:
            HANDLERS[job["kind"]](self.ctx, job, token)
            db.update("jobs", job["id"], {"status": "done", "finished_at": time.time(), "progress": 1.0})
            log.info("Job %s done", job["id"])
        except Cancelled:
            if self._stop.is_set():
                # shutting down: leave it queued so it resumes on next start
                db.update("jobs", job["id"], {"status": "queued", "message": "Interrupted by shutdown"})
            else:
                db.update("jobs", job["id"], {"status": "cancelled", "finished_at": time.time(), "message": "Cancelled"})
                self._after_cancel(job)
            log.info("Job %s cancelled", job["id"])
        except ClipStudioError as e:
            self._fail(job, str(e))
        except Exception as e:  # unexpected: log full trace, show a concise message
            log.exception("Job %s crashed", job["id"])
            self._fail(job, f"Unexpected error: {e.__class__.__name__}: {e}")
        finally:
            with self._lock:
                self._current = None

    def _fail(self, job: dict, msg: str) -> None:
        db = self.ctx.db
        db.update("jobs", job["id"], {"status": "failed", "finished_at": time.time(), "error": msg, "message": msg})
        if job["kind"] == "process":
            db.update("projects", job["project_id"], {"status": "failed", "error": msg})
        log.warning("Job %s failed: %s", job["id"], msg)

    def _after_cancel(self, job: dict) -> None:
        db = self.ctx.db
        if job["kind"] == "process":
            has_clips = db.one("SELECT COUNT(*) AS n FROM clips WHERE project_id = ?", (job["project_id"],))
            db.update("projects", job["project_id"], {"status": "ready" if has_clips and has_clips["n"] else "cancelled"})
        else:
            clip_id = job["payload"].get("clip_id")
            if clip_id:
                clip = db.one("SELECT render_path FROM clips WHERE id = ?", (clip_id,))
                if clip:
                    db.update("clips", clip_id, {"render_status": "done" if clip["render_path"] else "pending"})
