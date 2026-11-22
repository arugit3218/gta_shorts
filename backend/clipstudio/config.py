"""Application configuration.

Two layers:
- ``Env``: process-level settings from environment variables (CLIPSTUDIO_*). Fixed at startup.
- ``UserSettings``: user-editable settings persisted as JSON in the app home. Editable from the UI.
"""

from __future__ import annotations

import json
import os
import threading
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Env(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CLIPSTUDIO_", env_file=".env", extra="ignore")

    home: Path = REPO_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8765
    log_level: str = "INFO"
    # Allow model downloads from Hugging Face. Off by default: we only use cached models.
    allow_model_downloads: bool = False
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"


class Mode(str, Enum):
    low = "low"
    balanced = "balanced"
    high = "high"


AspectRatio = Literal["9:16", "1:1", "16:9"]
Layout = Literal["auto", "center", "blur", "split"]
DurationPreset = Literal["15-30", "30-45", "45-60", "60-90", "30-60"]

DURATION_RANGES: dict[str, tuple[float, float]] = {
    "15-30": (15.0, 30.0),
    "30-45": (30.0, 45.0),
    "45-60": (45.0, 60.0),
    "60-90": (60.0, 90.0),
    "30-60": (30.0, 60.0),
}


class ScoringWeights(BaseModel):
    hook: float = 25
    context: float = 20
    emotion: float = 15
    value: float = 15
    story: float = 10
    visual: float = 10
    audio: float = 5

    def normalised(self) -> dict[str, float]:
        d = self.model_dump()
        total = sum(d.values()) or 1.0
        return {k: v / total for k, v in d.items()}


class ScoreThresholds(BaseModel):
    excellent: float = 85
    strong: float = 70
    review: float = 55


class UserSettings(BaseModel):
    mode: Mode = Mode.low
    # Where sources, renders and temp files live. Can be an external SSD.
    work_dir: str = ""
    transcription_model: str = "mlx-community/parakeet-tdt-0.6b-v2"
    transcription_chunk_seconds: int = 120
    output_height: Literal[720, 1080] = 720
    aspect_ratio: AspectRatio = "9:16"
    layout: Layout = "auto"
    caption_preset: str = "bold"
    duration_preset: DurationPreset = "30-60"
    max_clips: int = Field(default=12, ge=1, le=30)
    min_score: float = Field(default=45, ge=0, le=100)
    auto_render: bool = True
    keep_intermediates: bool = False
    normalise_audio: bool = True
    max_download_height: Literal[720, 1080] = 1080
    min_free_gb: float = Field(default=1.5, ge=0.2)
    weights: ScoringWeights = ScoringWeights()
    thresholds: ScoreThresholds = ScoreThresholds()
    # Optional local LLM (Ollama). Off by default; the app works fully without it.
    llm_enabled: bool = False
    llm_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:1.5b"

    @field_validator("work_dir")
    @classmethod
    def _expand(cls, v: str) -> str:
        return str(Path(v).expanduser()) if v else v

    @field_validator("llm_url")
    @classmethod
    def _local_only(cls, v: str) -> str:
        # Privacy: the LLM endpoint must be on this machine.
        from urllib.parse import urlparse

        host = urlparse(v).hostname or ""
        if host not in {"127.0.0.1", "localhost", "::1"}:
            raise ValueError("LLM URL must point to localhost (privacy: no remote processing)")
        return v


MODE_PRESETS: dict[Mode, dict] = {
    Mode.low: {"output_height": 720, "transcription_chunk_seconds": 120, "llm_enabled": False, "face_tracking": True, "x264_preset": None},
    Mode.balanced: {"output_height": 720, "transcription_chunk_seconds": 180, "face_tracking": True, "x264_preset": "veryfast"},
    Mode.high: {"output_height": 1080, "transcription_chunk_seconds": 240, "face_tracking": True, "x264_preset": "medium"},
}


class SettingsStore:
    """Thread-safe JSON-backed store for UserSettings."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._settings = self._load()

    def _load(self) -> UserSettings:
        if self.path.exists():
            try:
                return UserSettings.model_validate_json(self.path.read_text())
            except Exception:  # corrupt file: keep a backup, fall back to defaults
                self.path.rename(self.path.with_suffix(".corrupt.json"))
        return UserSettings()

    def get(self) -> UserSettings:
        with self._lock:
            return self._settings.model_copy(deep=True)

    def update(self, patch: dict) -> UserSettings:
        with self._lock:
            merged = self._settings.model_dump() | patch
            new = UserSettings.model_validate(merged)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(new.model_dump(mode="json"), indent=2))
            os.replace(tmp, self.path)
            self._settings = new
            return new.model_copy(deep=True)


class Paths:
    """Resolves all on-disk locations. Everything the app writes lives under app home or work dir."""

    def __init__(self, env: Env, settings: SettingsStore):
        self.env = env
        self.settings = settings
        self.home = env.home
        self.home.mkdir(parents=True, exist_ok=True)

    @property
    def db(self) -> Path:
        return self.home / "clipstudio.db"

    @property
    def logs(self) -> Path:
        p = self.home / "logs"
        p.mkdir(exist_ok=True)
        return p

    @property
    def work(self) -> Path:
        wd = self.settings.get().work_dir
        p = Path(wd) if wd else self.home / "work"
        p.mkdir(parents=True, exist_ok=True)
        return p

    def project_dir(self, project_id: str) -> Path:
        p = self.work / "projects" / project_id
        p.mkdir(parents=True, exist_ok=True)
        return p

    def temp_dir(self, project_id: str) -> Path:
        p = self.project_dir(project_id) / "tmp"
        p.mkdir(exist_ok=True)
        return p

    @property
    def downloads(self) -> Path:
        p = self.work / "downloads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads(self) -> Path:
        p = self.work / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def cache(self) -> Path:
        p = self.work / "cache"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def assets(self) -> Path:
        return Path(__file__).parent / "assets"
