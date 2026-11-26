"""Find exactly where a downloaded video section starts in the source timeline.

Section downloads are cut at keyframes, so a section requested from 61.0 s may really start at
59.7 s. We cross-correlate the section's audio with the full audio track (already downloaded for
transcription) to get the true offset, so captions stay frame-accurate.
"""

from __future__ import annotations

import logging
import subprocess
from pathlib import Path

import numpy as np

from . import ffmpeg as ff

log = logging.getLogger(__name__)
SR = 8000


def decode_mono(path: Path, start: float, duration: float) -> np.ndarray:
    cmd = [ff.FFMPEG, "-v", "error", "-ss", f"{max(0.0, start):.3f}", "-t", f"{duration:.3f}", "-i", str(path), "-vn", "-ac", "1", "-ar", str(SR), "-f", "f32le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, timeout=120).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def find_offset(section: Path, reference: Path, expected_start: float, *, search: float = 10.0, probe_len: float = 12.0) -> tuple[float, float]:
    """(true start of ``section`` in the reference timeline, confidence 0..1)."""
    sec = decode_mono(section, 0.0, probe_len)
    ref_start = max(0.0, expected_start - search)
    ref = decode_mono(reference, ref_start, probe_len + 2 * search)
    if len(sec) < SR * 2 or len(ref) < len(sec):
        return expected_start, 0.0
    sec = sec - sec.mean()
    ref = ref - ref.mean()
    if float(np.std(sec)) < 1e-4:  # silence: nothing to match
        return expected_start, 0.0
    n = 1 << int(np.ceil(np.log2(len(ref) + len(sec))))
    corr = np.fft.irfft(np.fft.rfft(ref, n) * np.conj(np.fft.rfft(sec, n)), n)[: len(ref) - len(sec) + 1]
    # normalise by the reference energy under each window
    csum = np.concatenate([[0.0], np.cumsum(ref.astype(np.float64) ** 2)])
    win = np.sqrt(np.maximum(csum[len(sec):] - csum[: len(csum) - len(sec)], 1e-9))[: len(corr)]
    ncc = corr / (win * np.linalg.norm(sec) + 1e-9)
    best = float(ncc.max())
    # Repetitive audio (a steady tone, a looped beat) has many near-equal peaks: take the one
    # closest to where the section was requested (keyframe cuts only move it a little).
    near = np.nonzero(ncc >= best * 0.97)[0]
    expected_lag = (expected_start - ref_start) * SR
    lag = int(near[np.argmin(np.abs(near - expected_lag))])
    conf = float(ncc[lag])
    return ref_start + lag / SR, max(0.0, min(1.0, conf))
