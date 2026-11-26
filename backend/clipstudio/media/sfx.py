"""Sound effects: built-in synthesized effects + automatic placement.

The built-in effects are generated with numpy (no third-party audio, so no licensing questions):
pop, whoosh, hit, riser, ding. Users can override any type by dropping files into
``<work>/library/sfx/<type>/``; one is picked per clip (deterministically, so re-renders match).

Placement is rule-based from what the analysis already knows:
- pop    when the hook title appears
- whoosh on hard camera cuts (from face tracking)
- ding   on the first number / money amount
- hit    on an emphatic line ("!" or a contrast like "but the truth is")
- riser  building into the final sentence (the payoff) of longer clips
Effects keep ≥ 2.5 s apart and at most 4 per clip, so they punctuate rather than clutter.
"""

from __future__ import annotations

import hashlib
import re
import wave
from dataclasses import dataclass
from pathlib import Path

import numpy as np

SR = 48000
TYPES = ("pop", "whoosh", "hit", "riser", "ding")
GAIN = {"pop": 0.55, "whoosh": 0.35, "hit": 0.5, "riser": 0.3, "ding": 0.35}
AUDIO_EXTS = {".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"}
MIN_GAP = 2.5
MAX_CUES = 4
CONTRAST = re.compile(r"\b(but|actually|the truth|here's the thing|turns out|instead|nobody|never|secret)\b", re.I)


@dataclass
class Cue:
    type: str
    t: float  # seconds from clip start (for a riser: when it *starts*)

    def as_dict(self) -> dict:
        return {"type": self.type, "t": round(self.t, 2)}


# ----------------------------------------------------------------------------- synthesis
def _env(n: int, attack: float, decay: float) -> np.ndarray:
    t = np.arange(n) / SR
    a = np.clip(t / max(attack, 1e-4), 0, 1)
    return a * np.exp(-t / max(decay, 1e-4))


def _lowpass(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """One-pole low-pass with a (possibly time-varying) cutoff in Hz."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=float), x.shape)
    alpha = 1 - np.exp(-2 * np.pi * c / SR)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):  # short signals (< 2 s): plain loop is fine
        acc += alpha[i] * (x[i] - acc)
        y[i] = acc
    return y


def synth(kind: str) -> np.ndarray:
    rng = np.random.default_rng(7)
    if kind == "pop":
        n = int(0.09 * SR)
        t = np.arange(n) / SR
        f = 950 * np.exp(-t * 28) + 220
        x = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.002, 0.025)
    elif kind == "whoosh":
        n = int(0.5 * SR)
        t = np.arange(n) / SR
        shape = np.sin(np.pi * t / t[-1]) ** 2
        cutoff = 400 + 3800 * shape
        x = _lowpass(rng.standard_normal(n), cutoff) * shape * 2.2
    elif kind == "hit":
        n = int(0.45 * SR)
        t = np.arange(n) / SR
        f = 110 * np.exp(-t * 9) + 45
        body = np.sin(2 * np.pi * np.cumsum(f) / SR) * _env(n, 0.001, 0.16)
        click = _lowpass(rng.standard_normal(n), 2500) * _env(n, 0.0005, 0.012) * 2
        x = body + click
    elif kind == "riser":
        n = int(1.6 * SR)
        t = np.arange(n) / SR
        grow = (t / t[-1]) ** 2.2
        f = 180 + 1100 * (t / t[-1]) ** 1.6
        tone = np.sin(2 * np.pi * np.cumsum(f) / SR) * 0.45
        noise = _lowpass(rng.standard_normal(n), 600 + 5000 * grow) * 0.9
        x = (tone + noise) * grow
        x[-int(0.01 * SR) :] *= np.linspace(1, 0, int(0.01 * SR))
    elif kind == "ding":
        n = int(1.0 * SR)
        t = np.arange(n) / SR
        x = (np.sin(2 * np.pi * 1318.5 * t) + 0.45 * np.sin(2 * np.pi * 2637 * t) + 0.2 * np.sin(2 * np.pi * 3955 * t)) * _env(n, 0.002, 0.28)
    else:
        raise ValueError(kind)
    peak = float(np.max(np.abs(x))) or 1.0
    return (x / peak * 0.89).astype(np.float32)


def _write_wav(path: Path, x: np.ndarray) -> None:
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    stereo = np.repeat(pcm[:, None], 2, axis=1)
    tmp = path.with_suffix(".part.wav")
    with wave.open(str(tmp), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(stereo.tobytes())
    tmp.replace(path)


def builtin_dir(cache: Path) -> Path:
    d = cache / "sfx_builtin_v1"
    d.mkdir(parents=True, exist_ok=True)
    for k in TYPES:
        p = d / f"{k}.wav"
        if not p.exists():
            _write_wav(p, synth(k))
    return d


def resolve_file(kind: str, *, cache: Path, library: Path, seed: str) -> Path:
    """User override from library/sfx/<kind>/ if any, else the built-in effect."""
    user_dir = library / "sfx" / kind
    if user_dir.is_dir():
        files = sorted(p for p in user_dir.iterdir() if p.suffix.lower() in AUDIO_EXTS and p.is_file())
        if files:
            h = int(hashlib.sha1(f"{seed}:{kind}".encode()).hexdigest(), 16)
            return files[h % len(files)]
    return builtin_dir(cache) / f"{kind}.wav"


# ----------------------------------------------------------------------------- placement
def plan_cues(words: list, duration: float, *, hook_overlay: bool, sentence_starts: list[float] | None = None) -> list[Cue]:
    """Automatic cues from caption words (clip-relative CapWord-like objects with .w/.s/.e)."""
    cand: list[tuple[int, Cue]] = []  # (priority, cue); lower priority value wins
    if hook_overlay:
        cand.append((0, Cue("pop", 0.05)))
    for w in words:
        if w.s < 1.0 or w.s > duration - 1.0:
            continue
        if re.search(r"[\d$£€%]", w.w):
            cand.append((2, Cue("ding", w.s)))
            break
    text_words = list(words)
    for i, w in enumerate(text_words):
        if w.s < 3.0 or w.s > duration - 1.5:
            continue
        prev_end = i > 0 and text_words[i - 1].w.endswith("!")
        if prev_end or CONTRAST.fullmatch(re.sub(r"[^\w' ]", "", w.w.lower())):
            cand.append((3, Cue("hit", w.s)))
            break
    if sentence_starts and duration >= 15:
        last = max((s for s in sentence_starts if 4.0 <= s <= duration - 3.0), default=None)
        if last is not None:
            cand.append((1, Cue("riser", max(0.5, last - 1.5))))
    chosen: list[Cue] = []
    for _, cue in sorted(cand, key=lambda pc: pc[0]):
        anchor = cue.t + (1.5 if cue.type == "riser" else 0.0)
        if all(abs(anchor - (c.t + (1.5 if c.type == "riser" else 0.0))) >= MIN_GAP for c in chosen):
            chosen.append(cue)
        if len(chosen) >= MAX_CUES:
            break
    return sorted(chosen, key=lambda c: c.t)


def add_cut_whooshes(cues: list[Cue], cut_times: list[float], duration: float) -> list[Cue]:
    out = list(cues)
    for t in cut_times:
        if len(out) >= MAX_CUES:
            break
        if 0.8 <= t <= duration - 0.8 and all(abs(t - (c.t + (1.5 if c.type == "riser" else 0))) >= MIN_GAP for c in out):
            out.append(Cue("whoosh", max(0.0, t - 0.25)))  # whoosh peaks at the cut
    return sorted(out, key=lambda c: c.t)


def cues_from_dicts(items: list[dict], duration: float) -> list[Cue]:
    out = []
    for it in items or []:
        k, t = it.get("type"), it.get("t")
        if k in TYPES and isinstance(t, (int, float)) and 0 <= t < duration:
            out.append(Cue(k, float(t)))
    return sorted(out, key=lambda c: c.t)[:12]
