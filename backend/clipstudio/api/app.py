"""FastAPI application factory."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from ..context import AppContext
from ..errors import ClipStudioError
from ..jobs.worker import Worker
from ..logging_setup import setup_logging
from . import clips, projects, system

log = logging.getLogger(__name__)


def create_app(context: AppContext | None = None, *, start_worker: bool = True) -> FastAPI:
    c = context or AppContext.create()
    setup_logging(c.paths.logs, c.env.log_level)
    w = Worker(c)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_worker:
            w.start()
        log.info("ClipStudio ready on http://%s:%s (work dir: %s)", c.env.host, c.env.port, c.paths.work)
        yield
        log.info("Shutting down: stopping worker")
        w.stop()
        c.db.close()

    app = FastAPI(title="ClipStudio", version="0.1.0", lifespan=lifespan, docs_url="/api/docs", openapi_url="/api/openapi.json")
    app.state.ctx = c
    app.state.worker = w

    # Local-only: reject requests whose Host isn't localhost (blocks DNS-rebinding attacks).
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"])
    # Vite dev server only (production UI is served from the same origin).
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(ClipStudioError)
    async def _domain_error(_: Request, exc: ClipStudioError):
        return JSONResponse({"error": str(exc), "type": exc.__class__.__name__}, status_code=exc.status_code)

    @app.exception_handler(Exception)
    async def _unexpected(_: Request, exc: Exception):
        log.exception("Unhandled error")
        return JSONResponse({"error": f"Internal error: {exc.__class__.__name__}"}, status_code=500)

    app.include_router(projects.router)
    app.include_router(clips.router)
    app.include_router(system.router)

    dist: Path = c.env.frontend_dist
    if dist.exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse({"error": "Not found"}, status_code=404)
            candidate = (dist / full_path).resolve()
            if full_path and candidate.is_file() and candidate.is_relative_to(dist.resolve()):
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
