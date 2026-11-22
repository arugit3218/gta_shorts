"""Cheap per-window audio features from a 16 kHz mono PCM WAV (numpy only, streamed)."""

from __future__ import annotations

import json
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

HOP = 0.5  # seconds per analysis frame


@dataclass
class AudioProfile:
    hop: float
    db: np.ndarray  # RMS level per frame in dBFS
    peak: np.ndarray  # max abs sample per frame (0..1)

    @property
    def median_db(self) -> float:
        voiced = self.db[self.db > -60]
        return float(np.median(voiced)) if voiced.size else -60.0

    def window(self, start: float, end: float) -> tuple[np.ndarray, np.ndarray]:
        a, b = int(start / self.hop), max(int(start / self.hop) + 1, int(end / self.hop))
        return self.db[a:b], self.peak[a:b]

    def save(self, path: Path) -> None:
        path.write_text(json.dumps({"hop": self.hop, "db": np.round(self.db, 2).tolist(), "peak": np.round(self.peak, 4).tolist()}))

    @classmethod
    def load(cls, path: Path) -> "AudioProfile":
        d = json.loads(path.read_text())
        return cls(d["hop"], np.asarray(d["db"], dtype=np.float32), np.asarray(d["peak"], dtype=np.float32))

    @classmethod
    def empty(cls) -> "AudioProfile":
        return cls(HOP, np.zeros(0, np.float32), np.zeros(0, np.float32))


def analyse_wav(path: Path) -> AudioProfile:
    """Stream the WAV in 0.5 s frames; memory use is constant regardless of duration."""
    dbs: list[float] = []
    peaks: list[float] = []
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError("expected 16-bit PCM")
        n = int(w.getframerate() * HOP) * w.getnchannels()
        while True:
            raw = w.readframes(n // w.getnchannels())
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            dbs.append(20 * np.log10(max(rms, 1e-6)))
            peaks.append(float(np.max(np.abs(x))) if x.size else 0.0)
    return AudioProfile(HOP, np.asarray(dbs, np.float32), np.asarray(peaks, np.float32))
