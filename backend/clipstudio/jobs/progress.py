"""Per-stage progress reporting, persisted to the jobs table (throttled)."""

from __future__ import annotations

import time

from ..db import Database

PROCESS_STAGES: list[tuple[str, str, float]] = [
    # key, label, weight in overall progress
    ("download", "Downloading video", 0.12),
    ("audio", "Extracting audio", 0.04),
    ("transcribe", "Transcribing", 0.34),
    ("analyse", "Analysing transcript", 0.03),
    ("highlights", "Identifying highlights", 0.03),
    ("score", "Scoring candidates", 0.02),
    ("metadata", "Generating titles & hooks", 0.04),
    ("captions", "Generating captions", 0.02),
    ("render", "Rendering clips", 0.36),
]
RENDER_STAGES: list[tuple[str, str, float]] = [("render", "Rendering clip", 1.0)]


class Progress:
    def __init__(self, db: Database, job_id: str, stages: list[tuple[str, str, float]]):
        self.db = db
        self.job_id = job_id
        self.defs = stages
        self.state = {k: {"label": label, "status": "pending", "progress": 0.0, "message": ""} for k, label, _ in stages}
        self.current: str | None = None
        self._last_write = 0.0
        self._flush(force=True)

    def overall(self) -> float:
        total = sum(w for _, _, w in self.defs)
        done = sum(w * (1.0 if self.state[k]["status"] in ("done", "skipped") else self.state[k]["progress"]) for k, _, w in self.defs)
        return round(done / total, 4)

    def start(self, key: str, message: str = "") -> None:
        self.current = key
        self.state[key].update(status="running", progress=0.0, message=message)
        self._flush(force=True)

    def update(self, key: str, progress: float, message: str | None = None) -> None:
        self.state[key]["progress"] = round(max(0.0, min(1.0, progress)), 4)
        if message is not None:
            self.state[key]["message"] = message
        self._flush()

    def done(self, key: str, message: str = "") -> None:
        self.state[key].update(status="done", progress=1.0, message=message or self.state[key]["message"])
        self._flush(force=True)

    def skip(self, key: str, message: str = "") -> None:
        self.state[key].update(status="skipped", progress=1.0, message=message)
        self._flush(force=True)

    def fail(self, message: str) -> None:
        if self.current:
            self.state[self.current].update(status="failed", message=message)
        self._flush(force=True)

    def _flush(self, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_write < 0.25:
            return
        self._last_write = now
        cur = self.current
        msg = self.state[cur]["message"] if cur else ""
        self.db.update("jobs", self.job_id, {"stages": self.state, "stage": cur, "progress": self.overall(), "message": msg})
