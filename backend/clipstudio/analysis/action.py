"""Action analysis for gameplay, streams and edits (Phase 2), with no ML models.

Everything is computed on a 0.5 s grid ("steps"):
- audio:  loudness, *transients* (spectral flux: gunshots, explosions, hits), and *reaction*
          (loud bursts in the voice band: shouting or laughing)
- video:  *motion* (frame difference on a 96x54 grey thumbnail stream), scene cuts, *HUD flashes*
          (sudden change in the top-right corner, where most kill feeds live), and where the motion is
          horizontally (for action-following crops)
- words:  reaction phrases in the transcript ("let's go", "no way", "oh my god"…)

Each feature is compared with its local baseline, so a loud game and a quiet one are treated alike,
then combined into one *excitement* curve. Clips are built around its peaks: some build-up before,
the payoff and a short tail after.
"""

from __future__ import annotations

import json
import logging
import re
import subprocess
import wave
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np

from ..cancel import NEVER, CancelToken
from ..media import ffmpeg as ff
from ..transcription.types import Transcript
from .highlights import LEAD_IN, TAIL_OUT, Candidate, _jaccard, _overlap, target_count
from .scoring import ClipScore
from .segmentation import normalise_sentences

log = logging.getLogger(__name__)

STEP = 0.5
VIDEO_W, VIDEO_H = 96, 54
HUD_REGION = (0.70, 0.0, 1.0, 0.32)  # x0, y0, x1, y1
CROP_FRAC_916 = (9 / 16) / (16 / 9)  # width of a 9:16 crop from a 16:9 frame (0.316)

REACTION_PHRASES = (
    "let's go", "lets go", "oh my god", "oh my gosh", "no way", "what the", "holy", "omg", "oh no", "no no no",
    "yes yes", "insane", "clutch", "bro what", "are you kidding", "sheesh", "wow", "yooo", "nooo", "dude",
    "get him", "he's dead", "one shot", "ace", "gg", "lmao", "haha", "oh shit", "what was that", "did you see",
)

ACTION_CRITERIA_LABELS = {
    "hook": "Opening intensity",
    "context": "Clean cut points",
    "emotion": "Streamer reaction",
    "value": "Replay heat (YouTube)",
    "story": "Build-up and payoff",
    "visual": "On-screen action",
    "audio": "Audio impact",
}


@dataclass
class ActionWeights:
    audio: float = 30
    motion: float = 30
    reaction: float = 25
    hud: float = 15

    def normalised(self) -> dict[str, float]:
        d = {"audio": self.audio, "motion": self.motion, "reaction": self.reaction, "hud": self.hud}
        t = sum(d.values()) or 1.0
        return {k: v / t for k, v in d.items()}


@dataclass
class ActionCurves:
    step: float = STEP
    loud: np.ndarray = field(default_factory=lambda: np.zeros(0))  # dBFS
    flux: np.ndarray = field(default_factory=lambda: np.zeros(0))  # transient strength
    voice: np.ndarray = field(default_factory=lambda: np.zeros(0))  # voice-band loudness (dB)
    motion: np.ndarray = field(default_factory=lambda: np.zeros(0))  # mean abs frame diff (0..255)
    cuts: np.ndarray = field(default_factory=lambda: np.zeros(0))  # 1 where a new scene starts
    hud: np.ndarray = field(default_factory=lambda: np.zeros(0))  # corner change beyond global motion
    mx: np.ndarray = field(default_factory=lambda: np.zeros(0))  # best 9:16 crop centre (0..1) by motion
    conc: np.ndarray = field(default_factory=lambda: np.zeros(0))  # how concentrated motion is (≥1)

    @property
    def has_video(self) -> bool:
        return self.motion.size > 0

    @property
    def has_audio(self) -> bool:
        return self.loud.size > 0

    def n(self) -> int:
        return max(self.loud.size, self.motion.size)

    def to_dict(self) -> dict:
        return {k: (np.round(getattr(self, k), 4).tolist() if k != "step" else self.step) for k in ("step", "loud", "flux", "voice", "motion", "cuts", "hud", "mx", "conc")}

    @classmethod
    def from_dict(cls, d: dict) -> "ActionCurves":
        return cls(d.get("step", STEP), *(np.asarray(d.get(k, []), dtype=np.float32) for k in ("loud", "flux", "voice", "motion", "cuts", "hud", "mx", "conc")))

    def merged(self, other: "ActionCurves") -> "ActionCurves":
        """Audio from one analysis, video from another (same timeline)."""
        out = ActionCurves(self.step, self.loud, self.flux, self.voice, self.motion, self.cuts, self.hud, self.mx, self.conc)
        for k in ("loud", "flux", "voice"):
            if getattr(other, k).size and not getattr(out, k).size:
                setattr(out, k, getattr(other, k))
        for k in ("motion", "cuts", "hud", "mx", "conc"):
            if getattr(other, k).size and not getattr(out, k).size:
                setattr(out, k, getattr(other, k))
        return out


def save_curves(c: ActionCurves, path: Path) -> None:
    path.write_text(json.dumps(c.to_dict()))


def load_curves(path: Path) -> ActionCurves | None:
    try:
        return ActionCurves.from_dict(json.loads(path.read_text())) if path.exists() else None
    except Exception:
        return None


# ----------------------------------------------------------------------------- audio
def audio_curves(wav: Path) -> ActionCurves:
    """Loudness, transients and voice-band loudness per 0.5 s from a 16 kHz mono 16-bit WAV (streamed)."""
    loud, flux, voice = [], [], []
    nfft, hop = 1024, 512
    with wave.open(str(wav), "rb") as w:
        sr = w.getframerate()
        per_step = int(sr * STEP)
        freqs = np.fft.rfftfreq(nfft, 1 / sr)
        vband = (freqs >= 300) & (freqs <= 3400)
        win = np.hanning(nfft).astype(np.float32)
        prev = None
        carry = np.zeros(0, np.float32)
        while True:
            raw = w.readframes(per_step)
            if not raw:
                break
            x = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
            rms = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
            loud.append(20 * np.log10(max(rms, 1e-6)))
            buf = np.concatenate([carry, x])
            fl, ve = [], []
            k = 0
            while k + nfft <= buf.size:
                mag = np.abs(np.fft.rfft(buf[k : k + nfft] * win))
                logm = np.log1p(mag * 10)
                if prev is not None:
                    fl.append(float(np.maximum(logm - prev, 0).sum()))
                prev = logm
                ve.append(float(np.sqrt(np.mean(mag[vband] ** 2))))
                k += hop
            carry = buf[k:]
            flux.append(max(fl) if fl else 0.0)
            voice.append(20 * np.log10(max(np.mean(ve) if ve else 0.0, 1e-6)))
    return ActionCurves(STEP, np.asarray(loud, np.float32), np.asarray(flux, np.float32), np.asarray(voice, np.float32))


# ----------------------------------------------------------------------------- video
def video_curves(path: Path, duration: float, *, on_progress: Callable[[float], None] | None = None, cancel: CancelToken = NEVER) -> ActionCurves:
    """Motion, scene cuts, HUD flashes and motion position from a tiny grey frame stream.

    Decoding uses VideoToolbox (hardware) when possible and falls back to software decoding."""
    for hw in (True, False):
        try:
            return _video_curves(path, duration, hw, on_progress, cancel)
        except RuntimeError as e:
            if not hw:
                raise
            log.info("Hardware decode failed (%s); retrying in software", e)
    raise RuntimeError("unreachable")


def _video_curves(path: Path, duration: float, hw: bool, on_progress, cancel: CancelToken) -> ActionCurves:
    cmd = [ff.FFMPEG, "-v", "error", *(["-hwaccel", "videotoolbox"] if hw else []), "-i", str(path), "-an", "-vf", f"fps={1 / STEP:g},scale={VIDEO_W}:{VIDEO_H}:flags=area,format=gray", "-f", "rawvideo", "pipe:1"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    cancel.attach(proc)
    fsz = VIDEO_W * VIDEO_H
    motion, cuts, hud, mx, conc = [], [], [], [], []
    prev = None
    prev_hist = None
    hx0, hy0, hx1, hy1 = (int(HUD_REGION[0] * VIDEO_W), int(HUD_REGION[1] * VIDEO_H), int(HUD_REGION[2] * VIDEO_W), int(HUD_REGION[3] * VIDEO_H))
    rows = slice(int(0.12 * VIDEO_H), int(0.85 * VIDEO_H))  # ignore top HUD and bottom subtitles/HUD for position
    cw = max(3, int(round(CROP_FRAC_916 * VIDEO_W)))
    try:
        assert proc.stdout is not None
        i = 0
        while True:
            raw = proc.stdout.read(fsz)
            if len(raw) < fsz:
                break
            if i % 20 == 0:
                cancel.check()
                if on_progress and duration:
                    on_progress(min(0.99, i * STEP / duration))
            f = np.frombuffer(raw, np.uint8).reshape(VIDEO_H, VIDEO_W).astype(np.int16)
            hist = np.bincount((f // 8).ravel(), minlength=32).astype(np.float32)
            hist /= hist.sum() or 1
            if prev is None:
                motion.append(0.0)
                cuts.append(0.0)
                hud.append(0.0)
                mx.append(0.5)
                conc.append(1.0)
            else:
                d = np.abs(f - prev)
                m = float(d.mean())
                corr = float(np.corrcoef(hist, prev_hist)[0, 1]) if hist.std() and prev_hist.std() else 1.0
                is_cut = corr < 0.6 and m > 25
                motion.append(0.0 if is_cut else m)  # a cut isn't "action"
                cuts.append(1.0 if is_cut else 0.0)
                hud.append(max(0.0, float(d[hy0:hy1, hx0:hx1].mean()) - m) if not is_cut else 0.0)
                col = d[rows].sum(axis=0).astype(np.float32)
                win = np.convolve(col, np.ones(cw, np.float32), mode="valid")
                if win.size and win.mean() > 0:
                    j = int(np.argmax(win))
                    mx.append((j + cw / 2) / VIDEO_W)
                    conc.append(float(win[j] / win.mean()))
                else:
                    mx.append(mx[-1] if mx else 0.5)
                    conc.append(1.0)
            prev, prev_hist = f, hist
            i += 1
        proc.wait()
    finally:
        cancel.detach(proc)
        if proc.poll() is None:
            proc.kill()
            proc.wait()
    cancel.check()
    if not motion:
        raise RuntimeError("no frames decoded")
    a = lambda v: np.asarray(v, np.float32)  # noqa: E731
    return ActionCurves(STEP, motion=a(motion), cuts=a(cuts), hud=a(hud), mx=a(mx), conc=a(conc))


# ----------------------------------------------------------------------------- excitement
def _resample(x: np.ndarray, n: int) -> np.ndarray:
    if x.size == n:
        return x.astype(np.float32)
    if x.size == 0:
        return np.zeros(n, np.float32)
    out = np.zeros(n, np.float32)
    out[: min(n, x.size)] = x[:n]
    return out


def _rolling_median(x: np.ndarray, win: int) -> np.ndarray:
    """Rolling median via a coarse block median (fast; fine for a baseline)."""
    if x.size == 0:
        return x
    block = max(1, win // 4)
    nb = int(np.ceil(x.size / block))
    meds = np.array([np.median(x[i * block : (i + 1) * block]) for i in range(nb)], np.float32)
    k = max(1, win // block)
    pad = np.pad(meds, (k // 2, k - k // 2 - 1), mode="edge")
    smooth = np.array([np.median(pad[i : i + k]) for i in range(nb)], np.float32)
    return np.repeat(smooth, block)[: x.size]


def _norm(x: np.ndarray, baseline_win: int = 120) -> np.ndarray:
    """0..1 'how far above its local normal' score."""
    if x.size == 0 or not np.any(x):
        return np.zeros_like(x, dtype=np.float32)
    base = _rolling_median(x, baseline_win)
    dev = x - base
    mad = float(np.median(np.abs(x - np.median(x)))) * 1.4826 + 1e-6
    return np.clip(dev / (3.5 * mad), 0, 1).astype(np.float32)


def reaction_curve(t: Transcript | None, n: int) -> np.ndarray:
    out = np.zeros(n, np.float32)
    if t is None or not t.words:
        return out
    words = [(re.sub(r"[^\w' ]", "", w.w.lower()), w.s) for w in t.words]
    for i, (w, s) in enumerate(words):
        two = (w + " " + words[i + 1][0]) if i + 1 < len(words) else w
        three = (two + " " + words[i + 2][0]) if i + 2 < len(words) else two
        if any(p == w or p == two or p == three for p in REACTION_PHRASES) or w.endswith("!"):
            k = int(s / STEP)
            if 0 <= k < n:
                out[k] += 1.0
    return np.clip(out, 0, 2) / 2


@dataclass
class Excitement:
    total: np.ndarray
    parts: dict[str, np.ndarray]


def excitement(c: ActionCurves, t: Transcript | None, weights: dict[str, float], heat: list[dict] | None = None, duration: float | None = None) -> Excitement:
    n = c.n() or (int((duration or 0) / STEP) + 1)
    audio = np.maximum(_norm(_resample(c.flux, n)), 0.7 * _norm(_resample(c.loud, n)))
    voice = _norm(_resample(c.voice, n))
    react = np.clip(np.maximum(reaction_curve(t, n), voice * (_norm(_resample(c.loud, n)) > 0.3)), 0, 1)
    motion = _norm(_resample(c.motion, n))
    hud = _norm(_resample(c.hud, n))
    parts = {"audio": audio, "motion": motion, "reaction": react, "hud": hud}
    w = dict(weights)
    if not c.has_video:  # no video analysed: give its weight to audio/reaction
        w["audio"] += w.pop("motion", 0) * 0.6 + w.get("hud", 0) * 0.6
        w["reaction"] += 0.4 * (weights.get("motion", 0) + weights.get("hud", 0))
        w["hud"] = 0
        w["motion"] = 0
    tot = sum(w.values()) or 1
    e = sum(parts[k] * (w.get(k, 0) / tot) for k in parts)
    if heat:
        hv = np.zeros(n, np.float32)
        for h in heat:
            a, b = int(h["start"] / STEP), int(h["end"] / STEP) + 1
            hv[a:b] = h["value"]
        hv = (hv - np.median(hv)) / (np.percentile(hv, 95) - np.median(hv) + 1e-6)
        parts["heat"] = np.clip(hv, 0, 1).astype(np.float32)
        e = 0.85 * e + 0.15 * parts["heat"]
    e = np.convolve(e, np.ones(3) / 3, mode="same").astype(np.float32)
    return Excitement(e, parts)


# ----------------------------------------------------------------------------- highlights
def _peaks(e: np.ndarray, radius: int, floor: float) -> list[int]:
    out = []
    for i in range(e.size):
        a, b = max(0, i - radius), min(e.size, i + radius + 1)
        if e[i] >= floor and e[i] == e[a:b].max() and (not out or i - out[-1] > radius):
            out.append(i)
    return out


def _snap(t: float, words, cuts_t: list[float], edge: str) -> tuple[float, bool]:
    """Move a boundary so it doesn't split a word; prefer a nearby scene cut. Returns (t, clean)."""
    for c in cuts_t:
        if abs(c - t) <= 0.75:
            t = c
            break
    for w in words:
        if w.s - 0.05 < t < w.e + 0.05:
            return (max(0.0, w.s - LEAD_IN), True) if edge == "start" else (w.e + TAIL_OUT, True)
        if w.s > t + 2:
            break
    return t, True


def find_action_highlights(
    curves: ActionCurves,
    t: Transcript | None,
    *,
    duration: float,
    min_dur: float,
    max_dur: float,
    max_clips: int,
    min_score: float,
    weights: ActionWeights | None = None,
    heat: list[dict] | None = None,
) -> tuple[list[Candidate], dict]:
    w = (weights or ActionWeights()).normalised()
    ex = excitement(curves, t, w, heat=heat, duration=duration)
    e = ex.total
    if e.size < 4 or float(e.max()) <= 0:
        return [], {"windows": 0, "reason": "No clear action peaks found."}
    floor = max(0.18, float(np.percentile(e, 80)))
    peaks = _peaks(e, radius=int(6 / STEP), floor=floor)
    peaks.sort(key=lambda i: e[i], reverse=True)
    words = t.words if t else []
    sentences = normalise_sentences(t) if t and t.sentences else []
    cuts_t = [i * STEP for i in np.nonzero(_resample(curves.cuts, e.size))[0]]
    cand: list[Candidate] = []
    for p in peaks[: max(40, max_clips * 6)]:
        tp = p * STEP
        best: tuple[float, float, float, dict] | None = None
        for L in sorted({min_dur, (min_dur + max_dur) / 2, max_dur}):
            for lead in (0.55, 0.7):
                s0 = max(0.0, tp - lead * L)
                e0 = min(duration, s0 + L)
                s0 = max(0.0, e0 - L)
                sc, comp = _score_window(ex, s0, e0, tp, curves)
                if best is None or sc > best[0]:
                    best = (sc, s0, e0, comp)
        assert best is not None
        _, s0, e0, comp = best
        s1, _ = _snap(s0, words, cuts_t, "start")
        e1, _ = _snap(e0, words, cuts_t, "end")
        e1 = min(duration, e1)
        if e1 - s1 < min_dur * 0.8:
            continue
        cand.append(_candidate(ex, curves, s1, e1, tp, comp, words, sentences, heat is not None))
    cand.sort(key=lambda c: c.score.total, reverse=True)
    want = target_count(duration, max_clips)
    chosen: list[Candidate] = []
    toks: list[set[str]] = []
    for c in cand:
        if len(chosen) >= want:
            break
        if c.score.total < min_score and chosen:
            break
        if any(_overlap(c, o) > 0.15 for o in chosen):
            continue
        tk = set(c.text.lower().split())
        if tk and any(_jaccard(tk, o) > 0.6 for o in toks if o):
            continue
        chosen.append(c)
        toks.append(tk)
    stats = {"windows": len(cand), "peaks": len(peaks), "selected": len(chosen), "target": want}
    if len(chosen) < want:
        from .highlights import few_reason

        stats["reason"] = few_reason(len(chosen), want, min_score)
    return chosen, stats


def _score_window(ex: Excitement, s: float, e: float, tp: float, c: ActionCurves) -> tuple[float, dict]:
    tot = ex.total
    a, b = int(s / STEP), max(int(s / STEP) + 2, int(e / STEP))
    seg = tot[a:b]
    if seg.size == 0:
        return 0.0, {}
    k = min(seg.size - 1, max(0, int(tp / STEP) - a))
    peak = float(seg.max())
    mean = float(seg.mean())
    before = float(seg[: max(1, k)].mean())
    after = float(seg[k:].mean()) if k < seg.size else 0.0
    opening = float(seg[: int(3 / STEP)].max())
    dead = float((seg < 0.06).mean())
    build = float(np.clip((peak - before) * 1.5, 0, 1))
    shape = 0.6 * build + 0.4 * float(k / max(1, seg.size - 1) > 0.25)
    comp = {
        "peak": peak,
        "mean": mean,
        "opening": opening,
        "shape": shape,
        "dead": dead,
        "after": after,
        "audio": float(ex.parts["audio"][a:b].max()) if ex.parts["audio"].size else 0.0,
        "motion": float(ex.parts["motion"][a:b].mean() * 1.6) if ex.parts["motion"].size else 0.0,
        "reaction": float(ex.parts["reaction"][a:b].max()) if ex.parts["reaction"].size else 0.0,
        "hud": float(ex.parts["hud"][a:b].max()) if ex.parts["hud"].size else 0.0,
        "heat": float(ex.parts["heat"][a:b].mean()) if "heat" in ex.parts else None,
    }
    raw = 0.4 * peak + 0.25 * mean + 0.2 * shape + 0.15 * opening - 0.3 * dead
    return raw, comp


def _candidate(ex: Excitement, c: ActionCurves, s: float, e: float, tp: float, comp: dict, words, sentences, has_heat: bool) -> Candidate:
    scores = {
        "hook": round(min(100.0, comp["opening"] * 110), 1),
        "context": round(100.0 - 60 * comp["dead"], 1),
        "emotion": round(min(100.0, comp["reaction"] * 100), 1),
        "value": round(min(100.0, (comp["heat"] or 0) * 100), 1),
        "story": round(min(100.0, comp["shape"] * 100), 1),
        "visual": round(min(100.0, comp["motion"] * 100), 1),
        "audio": round(min(100.0, comp["audio"] * 100), 1),
    }
    wts = action_score_weights(has_heat, c.has_video)
    raw = sum(wts[k] * scores[k] for k in scores)
    peak_bonus = 25 * comp["peak"]
    total = round(float(np.clip(raw * 0.85 + peak_bonus, 0, 100)), 1)
    reasons, weak = [], []
    if comp["audio"] > 0.6:
        reasons.append(f"Big audio moment around {tp - s:.0f}s (gunfire, explosion or impact)")
    if comp["reaction"] > 0.5:
        reasons.append("Streamer reacts (shouting, laughing or a reaction phrase)")
    if comp["motion"] > 0.6:
        reasons.append("Fast on-screen action")
    if comp["hud"] > 0.6:
        reasons.append("Kill feed / HUD event in the corner")
    if comp["shape"] > 0.6:
        reasons.append("Builds up to a clear payoff")
    if comp["heat"] is not None and comp["heat"] > 0.5:
        reasons.append("Viewers replay this part often (YouTube “Most replayed”)")
    if comp["dead"] > 0.35:
        weak.append("Has quiet stretches with little happening")
    if comp["opening"] < 0.2:
        weak.append("Slow first 3 seconds; consider trimming the start")
    w0 = next((i for i, w in enumerate(words) if w.e > s), len(words))
    w1 = next((i for i, w in enumerate(words) if w.s >= e), len(words))
    inside = [k for k, x in enumerate(sentences) if x.end > s + 0.05 and x.start < e - 0.05]
    i0, i1 = (inside[0], inside[-1] + 1) if inside else (0, 0)
    text = " ".join(w.w for w in words[w0:w1])
    cat = "Reaction" if comp["reaction"] > 0.5 else "Action"
    cand = Candidate(round(s, 3), round(e, 3), i0, i1, w0, w1, ClipScore(total, scores, reasons or ["Peak of on-screen and audio intensity"], weak, cat), text=text)
    cand.extras["sentences"] = [{"start": x.start, "end": x.end, "text": x.text} for x in sentences[i0:i1]]
    cand.extras["criteria"] = ACTION_CRITERIA_LABELS
    cand.extras["weights"] = {k: round(v * 100, 1) for k, v in wts.items()}
    cand.extras["peak_t"] = round(tp, 2)
    cand.extras["mode"] = "action"
    return cand


def action_score_weights(has_heat: bool, has_video: bool) -> dict[str, float]:
    w = {"hook": 20, "context": 5, "emotion": 15, "value": 10 if has_heat else 0, "story": 15, "visual": 20 if has_video else 0, "audio": 20}
    t = sum(w.values())
    return {k: v / t for k, v in w.items()}


def action_bonus(ex: Excitement | None, start: float, end: float) -> float:
    """Up to +6 points for speech clips that also contain a big moment."""
    if ex is None or ex.total.size == 0:
        return 0.0
    a, b = int(start / STEP), max(int(start / STEP) + 1, int(end / STEP))
    seg = ex.total[a:b]
    return round(6.0 * float(np.clip((seg.max() if seg.size else 0) - 0.3, 0, 0.7) / 0.7), 1)
