"""Explainable clip-quality scoring.

Each criterion produces a 0–100 sub-score plus human-readable reasons. The overall score is a
weighted sum (weights are user-configurable). These are heuristics for *ranking* candidates;
they are not a validated prediction of views or virality.

Calibration hook: ``CALIBRATION`` maps a raw weighted score to the displayed score. It is the
identity today and is the single place to plug in a model fitted on real engagement data later.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from ..transcription.types import Sentence, Word
from . import lexicon as L
from .audio_features import AudioProfile
from .segmentation import TopicBoundaries

CRITERIA = ("hook", "context", "emotion", "value", "story", "visual", "audio")
CRITERIA_LABELS = {
    "hook": "Hook strength",
    "context": "Context completeness",
    "emotion": "Emotional impact",
    "value": "Practical value",
    "story": "Storytelling",
    "visual": "Visual quality",
    "audio": "Audio quality",
}


def CALIBRATION(raw: float) -> float:  # noqa: N802 - intentionally a named hook
    return raw


def clamp(x: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return float(max(lo, min(hi, x)))


@dataclass
class SentenceFeatures:
    n_words: int
    norm: str
    first_word: str
    last_word: str
    question: bool
    terminal: bool  # ends with . or !
    hook_hits: int
    question_start: bool
    has_number: bool
    you: int
    filler: int
    emotion: int
    intensifiers: int
    laughter: int
    exclaim: bool
    controversy: int
    value: int
    imperative: bool
    education: int
    story: int
    payoff: int
    meta: int


def sentence_features(s: Sentence, words: list[Word]) -> SentenceFeatures:
    text = s.text
    n = L.norm(text)
    toks = L.tokens(text)
    return SentenceFeatures(
        n_words=len(toks),
        norm=n,
        first_word=toks[0] if toks else "",
        last_word=toks[-1] if toks else "",
        question=text.rstrip().endswith("?"),
        terminal=text.rstrip().endswith((".", "!")),
        hook_hits=L.count_phrases(n, L.HOOK_PHRASES),
        question_start=n.strip().startswith(L.QUESTION_STARTS),
        has_number=L.has_number(text),
        you=sum(1 for t in toks if t in ("you", "your", "you're", "yourself")),
        filler=sum(1 for t in toks if t in L.FILLER_WORDS) + L.count_phrases(n, L.FILLER_PHRASES),
        emotion=sum(1 for t in toks if t in L.EMOTION_WORDS),
        intensifiers=sum(1 for t in toks if t in L.INTENSIFIERS),
        laughter=sum(1 for t in toks if t in L.LAUGHTER),
        exclaim="!" in text,
        controversy=L.count_phrases(n, L.CONTROVERSY_PHRASES),
        value=L.count_phrases(n, L.VALUE_PHRASES),
        imperative=bool(toks) and toks[0] in L.IMPERATIVE_VERBS,
        education=L.count_phrases(n, L.EDUCATION_PHRASES),
        story=L.count_phrases(n, L.STORY_PHRASES),
        payoff=L.count_phrases(n, L.PAYOFF_PHRASES),
        meta=L.count_phrases(n, L.META_PHRASES),
    )


def sentence_hook_score(f: SentenceFeatures) -> float:
    """Standalone 'how good is this sentence as an opener' score (also used to pick hook text)."""
    s = 35.0
    if f.question or f.question_start:
        s += 16
    s += min(24, 10 * f.hook_hits)
    s += min(10, 5 * f.controversy)
    if f.has_number:
        s += 8
    if f.you:
        s += 6
    if 4 <= f.n_words <= 16:
        s += 8
    elif f.n_words > 30:
        s -= 10
    elif f.n_words < 3:
        s -= 15
    if f.first_word in L.FILLER_WORDS or f.filler:
        s -= 15
    if f.first_word in L.DANGLING_STARTS:
        s -= 18
    elif f.first_word in L.ANAPHORIC_STARTS:
        s -= 8
    if f.emotion or f.exclaim:
        s += 5
    if f.meta:
        s -= 25
    return clamp(s)


@dataclass
class ClipScore:
    total: float
    scores: dict[str, float]
    reasons: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    category: str = "Highlight"


class Scorer:
    def __init__(
        self,
        sentences: list[Sentence],
        words: list[Word],
        topics: TopicBoundaries,
        audio: AudioProfile,
        *,
        source_height: int,
        weights: dict[str, float],
        target: tuple[float, float],
    ):
        self.sentences = sentences
        self.words = words
        self.topics = topics
        self.audio = audio
        self.audio_median = audio.median_db
        self.weights = weights
        self.target = target
        self.f = [sentence_features(s, words) for s in sentences]
        self.visual_base = 85.0 if source_height >= 1080 else 72.0 if source_height >= 720 else 55.0 if source_height >= 480 else 40.0

    # ---- criteria -------------------------------------------------------------------------
    def _hook(self, i0: int, i1: int, start: float, r: list[str], w: list[str]) -> float:
        first = self.f[i0]
        s = sentence_hook_score(first)
        # A strong second sentence within the first ~6 s helps a weak opener.
        if i1 - i0 > 1 and self.sentences[i0 + 1].start - start < 6:
            s = max(s, 0.6 * s + 0.4 * sentence_hook_score(self.f[i0 + 1]))
        if first.question or first.question_start:
            r.append("Opens with a question")
        if first.hook_hits:
            r.append("Opening uses a strong hook phrase")
        if first.has_number:
            r.append("Opening includes a concrete number")
        if first.first_word in L.DANGLING_STARTS:
            w.append(f"Starts mid-thought (“{first.first_word}…”)")
        elif first.first_word in L.ANAPHORIC_STARTS:
            w.append(f"Opening refers back to earlier context (“{first.first_word}…”)")
        if first.n_words > 30:
            w.append("Long first sentence; hook lands slowly")
        return s

    def _context(self, i0: int, i1: int, dur: float, r: list[str], w: list[str]) -> float:
        first, last = self.f[i0], self.f[i1 - 1]
        s = 62.0
        if last.terminal:
            s += 14
            r.append("Ends on a complete sentence")
        elif last.question:
            s -= 8
            w.append("Ends on an unanswered question")
        if last.last_word in L.WEAK_ENDINGS:
            s -= 15
            w.append("Ending is cut off mid-phrase")
        if first.first_word in L.DANGLING_STARTS:
            s -= 22
        elif first.first_word in L.ANAPHORIC_STARTS:
            s -= 10
        # payoff in the final third
        n = i1 - i0
        tail = range(i0 + max(0, (2 * n) // 3), i1)
        if any(self.f[k].payoff for k in tail):
            s += 12
            r.append("Has a clear payoff/conclusion")
        cross = self.topics.crossing(i0, i1)
        if cross > 0:
            s -= min(30, 60 * cross)
            w.append("Spans a topic change")
        else:
            s += 6
        meta = sum(self.f[k].meta for k in range(i0, i1))
        if meta:
            s -= 20
            w.append("Contains promotional/meta talk (subscribe, sponsor, intro/outro)")
        # long dead-air gaps inside the clip
        gaps = sum(1 for k in range(i0 + 1, i1) if self.sentences[k].start - self.sentences[k - 1].end > 2.5)
        if gaps:
            s -= 6 * gaps
            w.append("Contains long pauses")
        lo, hi = self.target
        mid = (lo + hi) / 2
        s += 6 * (1 - min(1.0, abs(dur - mid) / max(1.0, (hi - lo))))
        return clamp(s)

    def _density(self, i0: int, i1: int, attr: str) -> float:
        words = sum(self.f[k].n_words for k in range(i0, i1)) or 1
        return 100.0 * sum(getattr(self.f[k], attr) for k in range(i0, i1)) / words

    def _emotion(self, i0: int, i1: int, start: float, end: float, r: list[str], w: list[str]) -> float:
        words = sum(self.f[k].n_words for k in range(i0, i1)) or 1
        hits = sum(self.f[k].emotion + 0.5 * self.f[k].intensifiers + 2 * self.f[k].laughter + (1 if self.f[k].exclaim else 0) + self.f[k].controversy for k in range(i0, i1))
        dens = 100.0 * hits / words
        s = 22 + min(50, dens * 9)
        db, _ = self.audio.window(start, end)
        if db.size:
            loud = float(np.mean(db > self.audio_median + 6))
            s += min(28, loud * 120)
            if loud > 0.12:
                r.append("Energetic delivery (audio peaks)")
        if sum(self.f[k].laughter for k in range(i0, i1)):
            r.append("Contains laughter")
        if dens > 4:
            r.append("Emotionally charged language")
        if any(self.f[k].controversy for k in range(i0, i1)):
            r.append("Takes a strong/controversial stance")
        return clamp(s)

    def _value(self, i0: int, i1: int, r: list[str]) -> float:
        words = sum(self.f[k].n_words for k in range(i0, i1)) or 1
        hits = sum(self.f[k].value + (1.5 if self.f[k].imperative else 0) + 0.6 * self.f[k].education + (0.7 if self.f[k].has_number else 0) for k in range(i0, i1))
        dens = 100.0 * hits / words
        s = 15 + min(85, dens * 11)
        if dens > 3:
            r.append("Actionable advice or clear explanation")
        return clamp(s)

    def _story(self, i0: int, i1: int, r: list[str]) -> float:
        n = i1 - i0
        hits = [self.f[k].story for k in range(i0, i1)]
        total = sum(hits)
        # Saturating count (not density): adding a strong opener shouldn't dilute a story.
        s = 15 + min(60, 12 * total)
        setup = sum(hits[: max(1, (n + 1) // 2)])
        resolution = any(self.f[k].payoff for k in range(i0 + n // 2, i1))
        if setup and resolution:
            s += 25
            r.append("Story with setup and resolution")
        elif total >= 3:
            r.append("Personal story")
        return clamp(s)

    def _audio(self, i0: int, i1: int, start: float, end: float, dur: float, w: list[str]) -> float:
        s = 70.0
        db, pk = self.audio.window(start, end)
        if db.size:
            rel = float(np.median(db)) - self.audio_median
            if rel < -8:
                s -= 25
                w.append("Quieter than the rest of the video")
            clip_frac = float(np.mean(pk > 0.99))
            if clip_frac > 0.02:
                s -= 20
                w.append("Audio clipping/distortion detected")
            silent = float(np.mean(db < -45))
            s -= min(25, silent * 60)
        words = sum(self.f[k].n_words for k in range(i0, i1))
        wps = words / max(dur, 1)
        if 1.8 <= wps <= 4.2:
            s += 15
        elif wps < 1.2:
            s -= 15
            w.append("Sparse speech")
        fill = sum(self.f[k].filler for k in range(i0, i1)) / max(words, 1)
        if fill > 0.05:
            s -= 12
            w.append("Lots of filler words")
        return clamp(s)

    # ---- public ---------------------------------------------------------------------------
    def score(self, i0: int, i1: int, start: float, end: float) -> ClipScore:
        dur = end - start
        reasons: list[str] = []
        weak: list[str] = []
        sc = {
            "hook": self._hook(i0, i1, start, reasons, weak),
            "context": self._context(i0, i1, dur, reasons, weak),
            "emotion": self._emotion(i0, i1, start, end, reasons, weak),
            "value": self._value(i0, i1, reasons),
            "story": self._story(i0, i1, reasons),
            "visual": self.visual_base,
            "audio": self._audio(i0, i1, start, end, dur, weak),
        }
        raw = sum(self.weights[k] * sc[k] for k in CRITERIA)
        # Global substance penalties: filler-heavy or repetitive windows shouldn't rank well
        # just because they're well-formed.
        n_words = sum(self.f[k].n_words for k in range(i0, i1)) or 1
        filler_ratio = sum(self.f[k].filler for k in range(i0, i1)) / n_words
        if filler_ratio > 0.06:
            raw -= min(20.0, 120 * (filler_ratio - 0.06))
        text = " ".join(self.sentences[k].text for k in range(i0, i1))
        content = L.content_tokens(text)
        diversity = len(set(content)) / n_words
        if diversity < 0.25:
            raw -= min(15.0, 60 * (0.25 - diversity))
            weak.append("Repetitive / low information density")
        cats = {
            "Advice": sc["value"] if self._density(i0, i1, "value") > 1 else 0,
            "Story": sc["story"],
            "Opinion": 30 + 20 * sum(self.f[k].controversy for k in range(i0, i1)),
            "Education": 15 + min(70, self._density(i0, i1, "education") * 10),
            "Humour": 30 + 30 * sum(self.f[k].laughter for k in range(i0, i1)),
            "Emotional": sc["emotion"] - 10,
        }
        cat, best = max(cats.items(), key=lambda kv: kv[1])
        return ClipScore(
            total=round(clamp(CALIBRATION(raw)), 1),
            scores={k: round(v, 1) for k, v in sc.items()},
            reasons=list(dict.fromkeys(reasons)),
            weaknesses=list(dict.fromkeys(weak)),
            category=cat if best >= 45 else "Highlight",
        )


def quality_label(score: float, excellent: float = 85, strong: float = 70, review: float = 55) -> str:
    if score >= excellent:
        return "Excellent candidate"
    if score >= strong:
        return "Strong candidate"
    if score >= review:
        return "Needs review"
    return "Low priority"
