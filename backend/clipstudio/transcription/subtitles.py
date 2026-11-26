"""Build a Transcript from the uploader's own subtitles (no speech model needed).

Subtitle cues only have line-level timing, so word times are interpolated within each cue by
character length. Good enough for ranking and captions; less precise than real word timing.
"""

from __future__ import annotations

import re

from .types import Sentence, Transcript, Word


def transcript_from_cues(cues: list[tuple[float, float, str]], duration: float) -> Transcript:
    words: list[Word] = []
    for s, e, text in cues:
        toks = [t for t in re.split(r"\s+", text.replace(">>", " ").strip()) if t and not re.fullmatch(r"\[.*\]|\(.*\)|♪+", t)]
        if not toks:
            continue
        total = sum(len(t) + 1 for t in toks)
        span = max(0.05, e - s)
        t = s
        for tok in toks:
            d = span * (len(tok) + 1) / total
            words.append(Word(tok, round(t, 3), round(t + d * 0.92, 3), 0.8))
            t += d
    sentences: list[Sentence] = []
    i0 = 0
    for i, w in enumerate(words):
        last = i == len(words) - 1
        gap = (words[i + 1].s - w.e) if not last else 0
        if last or w.w.endswith((".", "!", "?")) or gap > 1.2:
            ws = words[i0 : i + 1]
            sentences.append(Sentence(ws[0].s, ws[-1].e, " ".join(x.w for x in ws), i0, i + 1))
            i0 = i + 1
    return Transcript(model="creator-subtitles", language="en", duration=duration, words=words, sentences=sentences)
