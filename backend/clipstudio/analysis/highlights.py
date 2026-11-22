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
    """~1 clip per 4 minutes of source, at least 3, capped by settings."""
    return max(1, min(max_clips, max(3, round(duration / 240))))


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
) -> tuple[list[Candidate], dict]:
    sentences = normalise_sentences(t)
    if not sentences:
        return [], {"sentences": 0, "windows": 0, "reason": "No speech detected in the video."}
    topics = topic_boundaries(sentences)
    scorer = Scorer(sentences, t.words, topics, audio, source_height=source_height, weights=weights, target=(min_dur, max_dur))

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
        if c.score.total < min_score and chosen:
            break
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
    if len(chosen) < want:
        stats["reason"] = (
            f"Only {len(chosen)} non-overlapping moments scored above the minimum ({min_score:.0f}); "
            "not padding with weak clips."
        )
    return chosen, stats


def _overlap(a: Candidate, b: Candidate) -> float:
    inter = max(0.0, min(a.end, b.end) - max(a.start, b.start))
    return inter / max(1e-6, min(a.duration, b.duration))


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
