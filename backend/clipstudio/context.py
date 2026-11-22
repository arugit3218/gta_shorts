"""Application context: the single place where long-lived services are wired together."""

from __future__ import annotations

from dataclasses import dataclass

from .config import Env, Paths, SettingsStore
from .db import Database


@dataclass
class AppContext:
    env: Env
    settings: SettingsStore
    paths: Paths
    db: Database

    @classmethod
    def create(cls, env: Env | None = None) -> "AppContext":
        env = env or Env()
        env.home.mkdir(parents=True, exist_ok=True)
        settings = SettingsStore(env.home / "settings.json")
        paths = Paths(env, settings)
        db = Database(paths.db)
        return cls(env=env, settings=settings, paths=paths, db=db)
