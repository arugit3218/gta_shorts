"""Music montage planner (Phase 3): source moments + song beats → a cut list.

- Moments: the most energetic parts of each source video (motion + scene structure), found from the
  same action curves as Phase 2. A long shot can give several moments; very short shots are skipped.
- Song window: positioned so the drop lands at ``drop_at`` of the montage (e.g. 40% in), starting
  on a bar. A campaign sound "cut" can pin the start instead.
- Cut grid: slower cuts while the song builds, faster after the drop (style = chill / normal / hype),
  and always a cut exactly on the drop.
- Assignment: the single strongest moment hits the drop (with a white flash); the next strongest
  fill the post-drop beats; calmer moments build up before it. Consecutive shots alternate sources
  when possible. Variants use different seeds and avoid moments other variants already used.
"""

from __future__ import annotations

import random
from dataclasses import asdict, dataclass

import numpy as np

from ..analysis.action import STEP, ActionCurves
from ..analysis.beats import SongAnalysis

STYLES = {
    # beats per cut: (before the drop, last bar before the drop, after the drop)
    "chill": (4, 2, 2),
    "normal": (2, 1, 1),
    "hype": (2, 1, 1),
}
MIN_SLOT = 0.3


@dataclass
class Moment:
    id: str
    file: str
    label: str
    start: float
    end: float
    peak: float
    energy: float
    mx: float  # horizontal centre of the action (0..1) for the 9:16 crop
    file_dur: float

    def as_dict(self) -> dict:
        return asdict(self)


def _norm01(x: np.ndarray) -> np.ndarray:
    if x.size == 0:
        return x
    lo, hi = np.percentile(x, 10), np.percentile(x, 98)
    return np.clip((x - lo) / (hi - lo + 1e-6), 0, 1)


def moments_from_curves(curves: ActionCurves, *, file: str, label: str, file_dur: float, max_moments: int = 60) -> list[Moment]:
    n = curves.motion.size
    if n == 0:
        # no video analysis: evenly spaced moments so the montage still works
        k = max(1, int(file_dur // 4))
        return [Moment(f"{label}:{i}", file, label, i * 4.0, min(file_dur, i * 4.0 + 4), i * 4.0 + 2, 0.3, 0.5, file_dur) for i in range(k)]
    motion = _norm01(curves.motion.astype(np.float32))
    flux = _norm01(curves.flux.astype(np.float32)) if curves.flux.size == n else np.zeros(n, np.float32)
    energy = np.convolve(0.75 * motion + 0.25 * flux, np.ones(3) / 3, mode="same")
    cuts = [0, *[int(i) for i in np.nonzero(curves.cuts > 0.5)[0]], n]
    out: list[Moment] = []
    for a, b in zip(cuts, cuts[1:]):
        if (b - a) * STEP < 0.6:
            continue
        seg = energy[a:b]
        # one moment per ~4 s of shot, at its local peaks
        k = max(1, int((b - a) * STEP / 4))
        idx = sorted(range(seg.size), key=lambda i: seg[i], reverse=True)
        picked: list[int] = []
        for i in idx:
            if len(picked) >= k:
                break
            if all(abs(i - j) * STEP >= 3 for j in picked):
                picked.append(i)
        for i in picked:
            p = (a + i) * STEP
            s, e = a * STEP, b * STEP
            mx = float(curves.mx[a + i]) if curves.mx.size > a + i else 0.5
            out.append(Moment(f"{label}:{a + i}", file, label, round(s, 2), round(min(e, file_dur), 2), round(p, 2), round(float(seg[i]), 3), round(mx, 3), file_dur))
    out.sort(key=lambda m: m.energy, reverse=True)
    return out[:max_moments]


def song_window(song: SongAnalysis, length: float, drop_at: float, start: float | None = None) -> tuple[float, float]:
    length = min(length, song.duration)
    if start is None:
        if song.drop is not None:
            target = song.drop - drop_at * length
            bars = [d for d in song.downbeats if d <= target + 0.01] or song.downbeats[:1] or [0.0]
            start = bars[-1]
        else:
            start = song.downbeats[0] if song.downbeats else 0.0
    start = max(0.0, min(start, song.duration - length))
    return round(start, 3), round(start + length, 3)


def cut_times(song: SongAnalysis, s0: float, s1: float, style: str) -> tuple[list[float], float | None]:
    """Cut times relative to the montage start, plus the drop time (relative) if it's inside."""
    pre, last_bar, post = STYLES.get(style, STYLES["normal"])
    beats = [b - s0 for b in song.beats if s0 + 0.05 < b < s1 - 0.2]
    drop = (song.drop - s0) if song.drop is not None and s0 < song.drop < s1 - 0.5 else None
    period = 60.0 / song.tempo if song.tempo else 0.5
    cuts = [0.0]
    count = 0
    for b in beats:
        count += 1
        if drop is not None and abs(b - drop) < 0.02:
            cuts.append(b)
            count = 0
            continue
        if drop is None:
            step = post if b > (s1 - s0) * 0.5 else pre
        elif b > drop:
            step = post
        elif drop - b <= 4 * period + 0.01:
            step = last_bar
        else:
            step = pre
        if count >= step:
            cuts.append(b)
            count = 0
    if style == "hype" and drop is not None:
        # half-beat stutter cuts in the bar right after the drop
        extra = [drop + period * (k + 0.5) for k in range(4) if drop + period * (k + 0.5) < (s1 - s0) - 0.2]
        cuts = sorted(set(cuts) | set(round(x, 3) for x in extra))
    clean = [cuts[0]]
    for c in cuts[1:]:
        if c - clean[-1] >= MIN_SLOT:
            clean.append(c)
    return clean, drop


def plan(song: SongAnalysis, moments: list[Moment], *, length: float, drop_at: float = 0.4, style: str = "normal", seed: int = 0, song_start: float | None = None, avoid: set[str] | None = None) -> dict:
    if not moments:
        raise ValueError("No usable shots in the source videos")
    s0, s1 = song_window(song, length, drop_at, song_start)
    L = s1 - s0
    cuts, drop = cut_times(song, s0, s1, style)
    bounds = [*cuts, L]
    slots = [{"at": round(a, 3), "dur": round(b - a, 3)} for a, b in zip(bounds, bounds[1:]) if b - a > 0.05]
    rng = random.Random(seed)
    avoid = avoid or set()
    # order by energy with a little seeded shuffling inside close-energy groups
    pool = sorted(moments, key=lambda m: (m.id in avoid, -(m.energy + rng.uniform(0, 0.12))))
    impact_idx = next((i for i, s in enumerate(slots) if drop is not None and abs(s["at"] - drop) < 0.02), None)
    order: list[int] = []
    if impact_idx is not None:
        order.append(impact_idx)
        order += [i for i in range(impact_idx + 1, len(slots))]
        order += list(range(impact_idx - 1, -1, -1))  # build-up gets progressively calmer going back
    else:
        order = list(range(len(slots)))
    assigned: dict[int, Moment] = {}
    used: list[str] = []
    for i in order:
        prev_file = assigned.get(i - 1).file if (i - 1) in assigned else None
        next_file = assigned.get(i + 1).file if (i + 1) in assigned else None
        choice = None
        if i == impact_idx:
            # the drop always gets the strongest shot this variant may use (no shuffling)
            fresh = [m for m in moments if m.id not in avoid and m.file_dur >= slots[i]["dur"] + 0.1] or [m for m in moments if m.file_dur >= slots[i]["dur"] + 0.1] or moments
            choice = max(fresh, key=lambda m: m.energy)
        for m in ([] if choice is not None else pool):
            if m.id in used:
                continue
            if m.file in (prev_file, next_file) and any(x.id not in used and x.file not in (prev_file, next_file) for x in pool):
                continue
            if m.file_dur < slots[i]["dur"] + 0.1 and any(x.id not in used and x.file_dur >= slots[i]["dur"] + 0.1 for x in pool):
                continue
            choice = m
            break
        if choice is None:  # not enough distinct moments: reuse the strongest, shifted
            choice = pool[len(used) % len(pool)]
        used.append(choice.id)
        assigned[i] = choice
    out_slots = []
    for i, sl in enumerate(slots):
        m = assigned[i]
        dur = sl["dur"]
        impact = i == impact_idx
        t = m.peak - (0.12 if impact else dur / 2)
        if used.count(m.id) > 1:
            t += 1.5 * (used[: i + 1].count(m.id) - 1)
        t = max(0.0, min(t, max(0.0, m.file_dur - dur - 0.05)))
        out_slots.append({"at": sl["at"], "dur": dur, "file": m.file, "t": round(t, 3), "mx": m.mx, "label": m.label, "energy": m.energy, "impact": impact, "moment": m.id})
    return {
        "song_start": s0,
        "song_end": s1,
        "length": round(L, 3),
        "tempo": song.tempo,
        "drop": round(drop, 3) if drop is not None else None,
        "style": style,
        "seed": seed,
        "drop_at": drop_at,
        "slots": out_slots,
    }
