"""Song analysis for music montages (Phase 3): tempo, beats, bars and the drop. numpy only.

- Onset strength: positive spectral flux of a log-magnitude spectrogram (22.05 kHz, hop 512).
- Tempo: autocorrelation of the onset curve (60–200 BPM) with a gentle preference for ~120 BPM.
- Beats: dynamic-programming beat tracker (Ellis 2007): beats land on strong onsets while keeping a
  steady spacing.
- Bars: the beat phase (1 of 4) whose beats carry the most low-frequency (kick/bass) onset.
- Drop: the bar where energy jumps the most (next 4 bars vs previous 4), favouring bass.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from ..media import ffmpeg as ff

SR = 22050
NFFT = 2048
HOP = 512
FPS = SR / HOP  # onset frames per second (~43)


@dataclass
class SongAnalysis:
    duration: float
    tempo: float
    beats: list[float] = field(default_factory=list)
    downbeats: list[float] = field(default_factory=list)
    drop: float | None = None
    drop_strength: float = 0.0
    energy: list[float] = field(default_factory=list)  # RMS per beat interval (0..1, normalised)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "SongAnalysis":
        return cls(**d)


def load_mono(path: Path, sr: int = SR) -> np.ndarray:
    cmd = [ff.FFMPEG, "-v", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "f32le", "pipe:1"]
    raw = subprocess.run(cmd, capture_output=True, timeout=300).stdout
    return np.frombuffer(raw, dtype=np.float32).copy()


def _stft_mag(x: np.ndarray) -> np.ndarray:
    if x.size < NFFT:
        x = np.pad(x, (0, NFFT - x.size))
    n = 1 + (x.size - NFFT) // HOP
    idx = np.arange(NFFT)[None, :] + HOP * np.arange(n)[:, None]
    win = np.hanning(NFFT).astype(np.float32)
    mags = np.empty((n, NFFT // 2 + 1), np.float32)
    for a in range(0, n, 2048):  # chunked to bound memory
        b = min(n, a + 2048)
        mags[a:b] = np.abs(np.fft.rfft(x[idx[a:b]] * win, axis=1))
    return mags


def _tempo(onset: np.ndarray) -> float:
    o = onset - onset.mean()
    ac = np.correlate(o, o, mode="full")[o.size - 1 :]
    lags = np.arange(ac.size)
    bpm = np.where(lags > 0, 60.0 * FPS / np.maximum(lags, 1), 0)
    ok = (bpm >= 60) & (bpm <= 200)
    prior = np.exp(-0.5 * (np.log2(np.maximum(bpm, 1) / 120.0) / 0.9) ** 2)
    score = np.where(ok, ac * prior, -np.inf)
    lag = int(np.argmax(score))
    # octave check: accents on every 2nd/4th beat often make the double period look strongest
    best_s = score[lag]
    for cand in (lag // 2, lag * 2):
        if 1 <= cand < ac.size and ok[cand] and ac[cand] >= 0.45 * ac[lag]:
            # local max near the candidate lag
            a, b = max(1, cand - 2), min(ac.size, cand + 3)
            c2 = a + int(np.argmax(ac[a:b]))
            s2 = ac[c2] * (prior[c2] ** 2)
            if s2 > best_s * prior[lag]:
                lag, best_s = c2, ac[c2] * prior[c2]
    # refine to sub-frame precision with a parabola through the peak
    if 1 <= lag < ac.size - 1:
        y0, y1, y2 = ac[lag - 1], ac[lag], ac[lag + 1]
        denom = y0 - 2 * y1 + y2
        lagf = lag + (0.5 * (y0 - y2) / denom if denom else 0.0)
    else:
        lagf = float(lag)
    return float(60.0 * FPS / lagf)


def _track_beats(onset: np.ndarray, tempo: float, tightness: float = 100.0) -> np.ndarray:
    period = 60.0 * FPS / tempo
    n = onset.size
    score = onset.astype(np.float64).copy()
    back = -np.ones(n, dtype=np.int64)
    lo, hi = int(round(period / 2)), int(round(period * 2))
    for t in range(n):
        a, b = max(0, t - hi), t - lo
        if b <= a:
            continue
        prev = np.arange(a, b)
        pen = -tightness * (np.log((t - prev) / period)) ** 2
        cand = score[prev] + pen
        k = int(np.argmax(cand))
        if cand[k] > 0:
            score[t] += cand[k]
            back[t] = prev[k]
    # start from the best-scoring frame in the last period
    tail = max(0, n - int(period))
    t = tail + int(np.argmax(score[tail:]))
    beats = []
    while t >= 0:
        beats.append(t)
        t = back[t]
    return np.array(beats[::-1], dtype=np.int64)


def analyse_song(path: Path) -> SongAnalysis:
    x = load_mono(path)
    duration = x.size / SR
    if duration < 3:
        return SongAnalysis(duration, 120.0)
    mag = _stft_mag(x)
    logm = np.log1p(10 * mag)
    # Band-wise flux, each band normalised, so a soft kick counts as much as a loud snare/hi-hat.
    edges_hz = [20, 120, 250, 500, 1000, 2000, 4000, 8000, 11025]
    freqs_all = np.fft.rfftfreq(NFFT, 1 / SR)
    d = np.maximum(np.diff(logm, axis=0), 0)
    flux = np.zeros(d.shape[0])
    for lo_hz, hi_hz in zip(edges_hz, edges_hz[1:]):
        band = (freqs_all >= lo_hz) & (freqs_all < hi_hz)
        if band.any():
            f = d[:, band].sum(axis=1)
            flux += f / (np.percentile(f, 98) + 1e-9)
    onset = np.concatenate([[0.0], flux])
    onset = np.convolve(onset, np.ones(3) / 3, mode="same")
    onset = onset / (onset.max() or 1)
    freqs = np.fft.rfftfreq(NFFT, 1 / SR)
    low = (freqs >= 20) & (freqs <= 160)
    low_onset = np.concatenate([[0.0], np.maximum(np.diff(logm[:, low], axis=0), 0).sum(axis=1)])
    rms = np.sqrt((mag**2).mean(axis=1))
    low_rms = np.sqrt((mag[:, low] ** 2).mean(axis=1))

    tempo = _tempo(onset)
    bf = _track_beats(onset, tempo)
    beats = bf / FPS + (NFFT / 2) / SR  # frame index → time of the analysis window's centre
    if beats.size < 8:
        period = 60.0 / tempo
        beats = np.arange(0, duration, period)
        bf = (beats * FPS).astype(int)
    # tempo from the tracked beats is more accurate than the autocorrelation estimate
    if beats.size >= 8:
        tempo = float(60.0 * (beats.size - 1) / (beats[-1] - beats[0]))
    phase = int(np.argmax([low_onset[np.clip(bf[k::4], 0, low_onset.size - 1)].sum() for k in range(4)]))
    downbeats = beats[phase::4]

    # energy per beat
    edges = np.concatenate([bf, [rms.size]])
    e = np.array([rms[edges[i] : max(edges[i] + 1, edges[i + 1])].mean() for i in range(bf.size)])
    le = np.array([low_rms[edges[i] : max(edges[i] + 1, edges[i + 1])].mean() for i in range(bf.size)])
    e_n = e / (e.max() or 1)
    le_n = le / (le.max() or 1)

    drop, strength = None, 0.0
    bar_idx = list(range(phase, beats.size, 4))
    best = -1.0
    span = 16 if beats.size >= 48 else 8  # compare 4 bars before/after (2 for short songs)
    for j in bar_idx:
        t = beats[j]
        if t < 6.0 or t > duration - 4.0 or j < span // 2:
            continue
        a, b = max(0, j - span), min(beats.size, j + span)
        before = 0.65 * e_n[a:j].mean() + 0.35 * le_n[a:j].mean()
        after = 0.65 * e_n[j:b].mean() + 0.35 * le_n[j:b].mean()
        jump = (after - before) / (before + 0.05)
        if jump > best:
            best, drop = jump, float(t)
    if drop is not None and best > 0.15:
        strength = float(min(1.0, best))
    else:
        drop = None
    return SongAnalysis(round(duration, 3), round(tempo, 2), [round(float(b), 3) for b in beats], [round(float(b), 3) for b in downbeats], drop, round(strength, 3), [round(float(v), 3) for v in e_n])


def cached_analysis(path: Path, cache_dir: Path, key: str) -> SongAnalysis:
    p = cache_dir / f"song_{key}.json"
    if p.exists():
        try:
            return SongAnalysis.from_dict(json.loads(p.read_text()))
        except Exception:
            pass
    a = analyse_song(path)
    p.write_text(json.dumps(a.to_dict()))
    return a
