"""Transcription service: model availability checks, caching, and subprocess orchestration."""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable

from ..cancel import NEVER, CancelToken
from ..errors import Cancelled, ClipStudioError, ModelUnavailable
from .types import Transcript

log = logging.getLogger(__name__)

# Known models. Sizes are on-disk download sizes; RAM is approximate peak during transcription.
MODELS: dict[str, dict] = {
    "mlx-community/parakeet-tdt-0.6b-v2": {"label": "Parakeet TDT 0.6B v2 (English)", "disk_gb": 2.3, "ram_gb": 2.5, "languages": ["en"]},
    "mlx-community/parakeet-tdt-0.6b-v3": {"label": "Parakeet TDT 0.6B v3 (25 European languages)", "disk_gb": 2.4, "ram_gb": 2.5, "languages": ["multi"]},
}


def _hf_cache_dir() -> Path:
    if os.environ.get("HF_HUB_CACHE"):
        return Path(os.environ["HF_HUB_CACHE"])
    return Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub"


def model_cached(model_id: str) -> bool:
    d = _hf_cache_dir() / ("models--" + model_id.replace("/", "--")) / "snapshots"
    if not d.exists():
        return False
    return any((snap / "config.json").exists() and any(snap.glob("*.safetensors")) for snap in d.iterdir())


def list_models() -> list[dict]:
    return [{"id": k, **v, "installed": model_cached(k)} for k, v in MODELS.items()]


class TranscriptionService:
    def __init__(self, cache_dir: Path, allow_downloads: bool = False):
        self.cache_dir = cache_dir
        self.allow_downloads = allow_downloads

    def cache_path(self, fingerprint: str, model_id: str) -> Path:
        tag = model_id.split("/")[-1]
        return self.cache_dir / f"transcript_{fingerprint}_{tag}.json"

    def cached(self, fingerprint: str, model_id: str) -> Transcript | None:
        p = self.cache_path(fingerprint, model_id)
        if p.exists():
            try:
                return Transcript.from_dict(json.loads(p.read_text()))
            except Exception:
                log.warning("Corrupt transcript cache %s, ignoring", p)
        return None

    def transcribe(
        self,
        wav: Path,
        *,
        fingerprint: str,
        model_id: str,
        duration: float,
        chunk_seconds: int = 120,
        on_progress: Callable[[float], None] | None = None,
        cancel: CancelToken = NEVER,
    ) -> Transcript:
        if (hit := self.cached(fingerprint, model_id)) is not None:
            return hit
        if not model_cached(model_id) and not self.allow_downloads:
            info = MODELS.get(model_id, {})
            raise ModelUnavailable(
                f"Transcription model '{model_id}' is not installed (~{info.get('disk_gb', '?')} GB). "
                "Model downloads are disabled by default; see README → Model setup."
            )
        out = self.cache_path(fingerprint, model_id)
        env = os.environ | ({} if self.allow_downloads else {"HF_HUB_OFFLINE": "1"})
        cmd = [sys.executable, "-m", "clipstudio.transcription.parakeet_worker", model_id, str(wav), str(out), str(chunk_seconds)]
        cancel.check()
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env, cwd=Path(__file__).resolve().parents[2])
        cancel.attach(proc)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if "progress" in msg and on_progress:
                    on_progress(float(msg["progress"]))
            proc.wait()
            err = proc.stderr.read() if proc.stderr else ""
        finally:
            cancel.detach(proc)
            if proc.poll() is None:
                proc.kill()
        if cancel.cancelled:
            raise Cancelled()
        if proc.returncode != 0 or not out.exists():
            tail = " | ".join(err.strip().splitlines()[-4:])
            if proc.returncode in (-9, 137):
                raise ClipStudioError("Transcription was killed (likely out of memory). Close other apps or lower the chunk size in Settings.")
            raise ClipStudioError(f"Transcription failed: {tail or proc.returncode}")
        data = json.loads(out.read_text())
        data["duration"] = duration
        out.write_text(json.dumps(data))
        return Transcript.from_dict(data)
