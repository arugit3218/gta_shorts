"""Entry point: ``python -m clipstudio`` (or ``./start.sh`` from the repo root)."""

from __future__ import annotations

import uvicorn

from .api.app import create_app
from .config import Env


def main() -> None:
    env = Env()
    if env.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"WARNING: binding to {env.host} exposes ClipStudio to your network.")
    uvicorn.run(create_app(), host=env.host, port=env.port, log_level="warning", timeout_graceful_shutdown=10)


if __name__ == "__main__":
    main()
