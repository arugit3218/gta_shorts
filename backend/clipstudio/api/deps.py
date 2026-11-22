from __future__ import annotations

from fastapi import Request

from ..context import AppContext
from ..errors import NotFound
from ..jobs.worker import Worker


def ctx(request: Request) -> AppContext:
    return request.app.state.ctx


def worker(request: Request) -> Worker:
    return request.app.state.worker


def get_project(c: AppContext, project_id: str) -> dict:
    p = c.db.one("SELECT * FROM projects WHERE id = ?", (project_id,))
    if p is None:
        raise NotFound("Project not found")
    return p


def get_clip(c: AppContext, clip_id: str) -> dict:
    cl = c.db.one("SELECT * FROM clips WHERE id = ?", (clip_id,))
    if cl is None:
        raise NotFound("Clip not found")
    return cl
