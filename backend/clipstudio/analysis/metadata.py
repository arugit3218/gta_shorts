"""Extractive hook/title/description/hashtag generation (no LLM required).

Every hook, title and description is built from the speaker's own words, so nothing is invented
and the meaning isn't changed. An optional local LLM can propose alternatives (see ``llm``).
"""

from __future__ import annotations

import math
import re
from collections import Counter

from . import lexicon as L
from .scoring import sentence_features, sentence_hook_score
from ..transcription.types import Sentence, Word

_LEADING_JUNK = re.compile(r"^(?:(?:and|but|so|um|uh|like|well|okay|ok|yeah|you know|i mean|anyway)[,\s]+)+", re.I)


def clean_quote(text: str, max_words: int = 14) -> str:
    t = _LEADING_JUNK.sub("", text.strip()).strip()
    t = re.sub(r"\b(um|uh)\b[,]?\s*", "", t, flags=re.I).strip()
    ws = t.split()
    if len(ws) > max_words:
        cut = ws[:max_words]
        # prefer cutting at a comma within the window
        for k in range(len(cut) - 1, max_words // 2, -1):
            if cut[k].endswith(","):
                cut = cut[: k + 1]
                break
        t = " ".join(cut).rstrip(",;:") + "…"
    return t[:1].upper() + t[1:] if t else t


def generate(clip_sentences: list[Sentence], words: list[Word], category: str, idf: dict[str, float]) -> dict:
    feats = [(s, sentence_features(s, words)) for s in clip_sentences]
    ranked = sorted(feats, key=lambda sf: sentence_hook_score(sf[1]), reverse=True)

    hooks: list[str] = []
    for s, f in ranked:
        if f.n_words < 3 or f.meta:
            continue
        q = clean_quote(s.text)
        if q and q.lower() not in {h.lower() for h in hooks}:
            hooks.append(q)
        if len(hooks) == 3:
            break
    if not hooks and clip_sentences:
        hooks = [clean_quote(clip_sentences[0].text)]

    title_src = hooks[0] if hooks else ""
    title = clean_quote(title_src.rstrip("…"), max_words=10).rstrip(".")
    if len(title) > 70:
        title = title[:67].rsplit(" ", 1)[0] + "…"

    desc_parts: list[str] = []
    for s, f in feats:
        if f.meta:
            continue
        desc_parts.append(clean_quote(s.text, max_words=40))
        if sum(len(p) for p in desc_parts) > 160:
            break
    description = " ".join(desc_parts)[:240]

    kw = keywords(" ".join(s.text for s in clip_sentences), idf, k=4)
    tags = [*kw, *L.CATEGORY_HASHTAGS.get(category, ["highlights"]), "shorts"]
    hashtags = list(dict.fromkeys("#" + re.sub(r"[^a-z0-9]", "", t.lower()) for t in tags if t))[:7]
    return {"hooks": hooks, "title": title, "description": description, "hashtags": hashtags, "keywords": kw}


def build_idf(sentences: list[Sentence]) -> dict[str, float]:
    """IDF over sentences of the whole transcript, so keywords are specific to the clip."""
    df: Counter[str] = Counter()
    for s in sentences:
        df.update(set(L.content_tokens(s.text)))
    n = max(1, len(sentences))
    return {t: math.log((n + 1) / (c + 1)) + 1 for t, c in df.items()}


def keywords(text: str, idf: dict[str, float], k: int = 4) -> list[str]:
    # Keep original surface forms for display; rank by tf-idf on stems.
    surface: dict[str, str] = {}
    tf: Counter[str] = Counter()
    for tok in L.tokens(text):
        if tok in L.STOPWORDS or len(tok) <= 3 or tok.isdigit():
            continue
        stem = L.content_tokens(tok)
        if not stem:
            continue
        tf[stem[0]] += 1
        surface.setdefault(stem[0], tok)
    ranked = sorted(tf, key=lambda t: tf[t] * idf.get(t, 1.0), reverse=True)
    return [surface[t] for t in ranked[:k] if tf[t] >= 1]
