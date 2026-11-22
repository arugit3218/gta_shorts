"""Sentence normalisation and lexical topic segmentation.

Topic segmentation is TextTiling (Hearst, 1997) over sentence blocks using bag-of-words cosine
similarity. The block/depth-score approach follows ClipsAI's TextTiler (MIT) but uses lexical
vectors instead of a transformer embedding model, so it needs only numpy and negligible RAM.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

import numpy as np

from ..transcription.types import Sentence, Transcript
from .lexicon import content_tokens

MAX_SENTENCE_SECONDS = 18.0
MIN_SPLIT_WORDS = 4


def normalise_sentences(t: Transcript) -> list[Sentence]:
    """Split run-on sentences (common in unpunctuated speech) at the best pause or comma."""
    out: list[Sentence] = []
    stack = list(reversed(t.sentences))
    while stack:
        s = stack.pop()
        if s.end - s.start <= MAX_SENTENCE_SECONDS or s.i1 - s.i0 < 2 * MIN_SPLIT_WORDS:
            out.append(s)
            continue
        best_k, best_score = None, -1.0
        for k in range(s.i0 + MIN_SPLIT_WORDS, s.i1 - MIN_SPLIT_WORDS):
            prev, nxt = t.words[k - 1], t.words[k]
            gap = nxt.s - prev.e
            score = gap + (0.4 if prev.w.endswith((",", ";", ":", "—", "-")) else 0.0)
            # prefer splits near the middle
            mid = (s.i0 + s.i1) / 2
            score -= 0.002 * abs(k - mid)
            if score > best_score:
                best_k, best_score = k, score
        assert best_k is not None
        a = Sentence(s.start, t.words[best_k - 1].e, " ".join(w.w for w in t.words[s.i0:best_k]), s.i0, best_k)
        b = Sentence(t.words[best_k].s, s.end, " ".join(w.w for w in t.words[best_k:s.i1]), best_k, s.i1)
        stack.extend([b, a])
    return out


@dataclass
class TopicBoundaries:
    depth: np.ndarray  # depth score at boundary *before* sentence i (len = n_sentences), 0 if none
    boundaries: list[int]

    def crossing(self, i0: int, i1: int) -> float:
        """Sum of boundary depths inside sentence range [i0, i1).

        Boundaries are only accurate to about ±1 sentence, so boundaries adjacent to the window
        edges are ignored (tolerance band).
        """
        if i1 - i0 < 4:
            return 0.0
        return float(self.depth[i0 + 2 : i1 - 1].sum())


def _cos(a: Counter, b: Counter) -> float:
    if not a or not b:
        return 0.0
    common = set(a) & set(b)
    num = sum(a[k] * b[k] for k in common)
    den = (sum(v * v for v in a.values()) ** 0.5) * (sum(v * v for v in b.values()) ** 0.5)
    return num / den if den else 0.0


def topic_boundaries(sentences: list[Sentence], block: int = 6) -> TopicBoundaries:
    n = len(sentences)
    depth = np.zeros(n, dtype=np.float32)
    if n < 2 * block + 2:
        return TopicBoundaries(depth, [])
    bags = [Counter(content_tokens(s.text)) for s in sentences]
    gaps = np.zeros(n, dtype=np.float32)
    for i in range(1, n):
        left = sum((bags[j] for j in range(max(0, i - block), i)), Counter())
        right = sum((bags[j] for j in range(i, min(n, i + block))), Counter())
        gaps[i] = _cos(left, right)
    # smooth with a flat window of 3
    sm = gaps.copy()
    sm[1:-1] = (gaps[:-2] + gaps[1:-1] + gaps[2:]) / 3
    sm[0] = sm[1]
    raw_depth = np.zeros(n, dtype=np.float32)
    for i in range(1, n - 1):
        lp = sm[i]
        j = i
        while j > 0 and sm[j - 1] >= lp:
            j -= 1
            lp = sm[j]
        rp = sm[i]
        j = i
        while j < n - 1 and sm[j + 1] >= rp:
            j += 1
            rp = sm[j]
        raw_depth[i] = (lp - sm[i]) + (rp - sm[i])
    valid = raw_depth[1:-1]
    cutoff = float(valid.mean() + valid.std()) if valid.size else 1.0
    boundaries = [i for i in range(1, n - 1) if raw_depth[i] > cutoff and raw_depth[i] >= raw_depth[i - 1] and raw_depth[i] >= raw_depth[i + 1]]
    for i in boundaries:
        depth[i] = raw_depth[i]
    return TopicBoundaries(depth, boundaries)
