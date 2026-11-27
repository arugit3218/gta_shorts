"""Highlight detection: sentence-aligned candidate windows → scoring → de-duplicated selection."""

from __future__ import annotations

from dataclasses import dataclass, field

from ..transcription.types import Sentence, Transcript, Word
from .audio_features import AudioProfile
from .lexicon import content_tokens
from .scoring import ClipScore, Scorer
from .segmentation import normalise_sentences, topic_boundaries

LEAD_IN = 0.15  # seconds of air before the first word
TAIL_OUT = 0.35  # seconds after the last word


@dataclass
class Candidate:
    start: float
    end: float
    i0: int  # sentence range [i0, i1)
    i1: int
    w0: int  # word range [w0, w1)
    w1: int
    score: ClipScore
    text: str = ""
    overlap_note: str = ""
    extras: dict = field(default_factory=dict)

    @property
    def duration(self) -> float:
        return self.end - self.start


def _bounds(sentences: list[Sentence], words: list[Word], i0: int, i1: int, total: float) -> tuple[float, float]:
    w0, w1 = sentences[i0].i0, sentences[i1 - 1].i1
    start = words[w0].s - LEAD_IN
    if w0 > 0:
        start = max(start, (words[w0 - 1].e + words[w0].s) / 2)
    end = words[w1 - 1].e + TAIL_OUT
    if w1 < len(words):
        end = min(end, (words[w1 - 1].e + words[w1].s) / 2)
    return max(0.0, round(start, 3)), min(total, round(end, 3))


def target_count(duration: float, max_clips: int) -> int:
    """How many clips to aim for: the number the user asked for (clips per video), limited only by how many
    can fit in the source. Weak moments (below the minimum score) are still skipped, never padded."""
    return max(1, min(max_clips, int(duration // 10) or 1))


HEAT_MAX_BONUS = 10.0
CHAPTER_PENALTY = 6.0


class HeatSignal:
    """YouTube "Most replayed" data: how often each part of the video is re-watched (0..1).

    A window whose average replay level is well above the video's typical level gets up to
    +10 points; clearly below-typical windows lose a little. It's real viewer behaviour, so it's
    a strong tie-breaker between transcript-equal moments (and helps low-speech videos)."""

    def __init__(self, heat: list[dict]):
        import numpy as np

        self.s = np.array([h["start"] for h in heat], dtype=float)
        self.e = np.array([h["end"] for h in heat], dtype=float)
        self.v = np.array([h["value"] for h in heat], dtype=float)
        self.median = float(np.median(self.v))
        self.p90 = float(np.percentile(self.v, 90))

    def bonus(self, start: float, end: float) -> float:
        import numpy as np

        inter = np.clip(np.minimum(self.e, end) - np.maximum(self.s, start), 0, None)
        if inter.sum() <= 0:
            return 0.0
        mean = float((self.v * inter).sum() / inter.sum())
        rel = (mean - self.median) / max(1e-6, self.p90 - self.median)
        return round(HEAT_MAX_BONUS * max(-0.4, min(1.0, rel)), 1)


def find_highlights(
    t: Transcript,
    audio: AudioProfile,
    *,
    source_height: int,
    weights: dict[str, float],
    min_dur: float,
    max_dur: float,
    max_clips: int,
    min_score: float,
    heat: list[dict] | None = None,
    chapters: list[float] | None = None,
) -> tuple[list[Candidate], dict]:
    sentences = normalise_sentences(t)
    if not sentences:
        return [], {"sentences": 0, "windows": 0, "reason": "No speech detected in the video."}
    topics = topic_boundaries(sentences)
    scorer = Scorer(sentences, t.words, topics, audio, source_height=source_height, weights=weights, target=(min_dur, max_dur))

    heat_sig = HeatSignal(heat) if heat and len(heat) >= 10 else None
    windows: list[Candidate] = []
    n = len(sentences)
    for i0 in range(n):
        for i1 in range(i0 + 1, n + 1):
            start, end = _bounds(sentences, t.words, i0, i1, t.duration)
            dur = end - start
            if dur > max_dur:
                break
            if dur < min_dur:
                continue
            sc = scorer.score(i0, i1, start, end)
            _adjust(sc, start, end, heat_sig, chapters)
            windows.append(Candidate(start, end, i0, i1, sentences[i0].i0, sentences[i1 - 1].i1, sc))

    # Short videos with no window long enough: fall back to the best window under min duration.
    if not windows and n:
        start, end = _bounds(sentences, t.words, 0, n, t.duration)
        if end - start <= max_dur:
            windows.append(Candidate(start, end, 0, n, sentences[0].i0, sentences[-1].i1, scorer.score(0, n, start, end)))

    windows.sort(key=lambda c: c.score.total, reverse=True)
    want = target_count(t.duration, max_clips)
    chosen: list[Candidate] = []
    chosen_tokens: list[set[str]] = []
    for c in windows:
        if len(chosen) >= want:
            break
        if c.score.total < min_score:
            break  # strict: no weak clip just to have one (lower the minimum to get more)
        if any(_overlap(c, o) > 0.15 for o in chosen):
            continue
        toks = set(content_tokens(" ".join(w.w for w in t.words[c.w0 : c.w1])))
        if any(_jaccard(toks, o) > 0.5 for o in chosen_tokens):
            continue
        chosen.append(c)
        chosen_tokens.append(toks)

    for c in chosen:
        c.text = " ".join(s.text for s in sentences[c.i0 : c.i1])
        c.extras["sentences"] = [{"start": s.start, "end": s.end, "text": s.text} for s in sentences[c.i0 : c.i1]]
    stats = {
        "sentences": n,
        "topic_boundaries": len(topics.boundaries),
        "windows": len(windows),
        "target": want,
        "selected": len(chosen),
    }
    if heat_sig is not None:
        stats["heatmap"] = True
    if len(chosen) < want:
        stats["reason"] = few_reason(len(chosen), want, min_score)
    return chosen, stats


def few_reason(got: int, want: int, min_score: float) -> str:
    if got == 0:
        return (
            f"No moment scored above your minimum quality ({min_score:.0f}/100), so no weak clip was made. "
            "Add a clip by hand, try AI picks, or lower “Minimum score” in Settings → Basics and re-process."
        )
    return (
        f"{got} of the {want} clips you asked for: only {got} separate moments scored above the minimum quality "
        f"({min_score:.0f}/100). Weak moments are skipped rather than padded. Lower “Minimum score” in Settings → Basics to get more."
    )


def _adjust(sc: ClipScore, start: float, end: float, heat_sig: HeatSignal | None, chapters: list[float] | None) -> None:
    if heat_sig is not None:
        b = heat_sig.bonus(start, end)
        if b:
            sc.total = round(max(0.0, min(100.0, sc.total + b)), 1)
            sc.scores["replay"] = b
            if b >= 3:
                sc.reasons.append(f"Viewers replay this part often (YouTube “Most replayed”, +{b:g})")
            elif b <= -2:
                sc.weaknesses.append("Viewers rarely replay this part (YouTube “Most replayed”)")
    if chapters and any(start + 2.0 < c < end - 2.0 for c in chapters):
        sc.total = round(max(0.0, sc.total - CHAPTER_PENALTY), 1)
        sc.weaknesses.append("Crosses a chapter boundary in the video (may mix two topics)")


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def score_range(t: Transcript, audio: AudioProfile, start: float, end: float, *, source_height: int, weights: dict[str, float]) -> Candidate:
    """Score an arbitrary [start, end] range (manual clips, quick mode, AI picks) with the same
    criteria as automatic highlights, so every clip gets an explanation."""
    sentences = normalise_sentences(t)
    inside = [k for k, s in enumerate(sentences) if s.end > start + 0.05 and s.start < end - 0.05]
    if not inside:
        empty = ClipScore(total=0.0, scores={}, reasons=[], weaknesses=["No speech in this range"], category="Highlight")
        return Candidate(start, end, 0, 0, 0, 0, empty, text="", extras={"sentences": []})
    i0, i1 = inside[0], inside[-1] + 1
    topics = topic_boundaries(sentences)
    lo, hi = sorted((max(1.0, end - start), max(1.0, end - start)))
    scorer = Scorer(sentences, t.words, topics, audio, source_height=source_height, weights=weights, target=(lo * 0.8, hi * 1.2))
    sc = scorer.score(i0, i1, start, end)
    c = Candidate(start, end, i0, i1, sentences[i0].i0, sentences[i1 - 1].i1, sc)
    c.text = " ".join(s.text for s in sentences[i0:i1])
    c.extras["sentences"] = [{"start": s.start, "end": s.end, "text": s.text} for s in sentences[i0:i1]]
    return c


def snap_to_words(words: list[Word], start: float, end: float, total: float) -> tuple[float, float]:
    """Move a rough [start, end] onto word boundaries (never cut a word in half)."""
    if not words:
        return max(0.0, start), min(total, end)
    starts = [w for w in words if w.s >= start - 0.6]
    s = starts[0].s - LEAD_IN if starts else start
    ends = [w for w in words if w.e <= end + 0.6]
    e = ends[-1].e + TAIL_OUT if ends else end
    if e - s < 1.0:
        s, e = start, end
    return max(0.0, round(s, 3)), min(total, round(e, 3))
