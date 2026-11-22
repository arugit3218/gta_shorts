import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from clipstudio.transcription.types import Sentence, Transcript, Word  # noqa: E402


def make_transcript(sentences: list[str], wps: float = 2.8, gap: float = 0.3) -> Transcript:
    """Build a synthetic transcript with evenly spaced words."""
    words: list[Word] = []
    sents: list[Sentence] = []
    t = 0.5
    for text in sentences:
        i0 = len(words)
        for w in text.split():
            words.append(Word(w, round(t, 3), round(t + 1 / wps * 0.85, 3)))
            t += 1 / wps
        sents.append(Sentence(words[i0].s, words[-1].e, text, i0, len(words)))
        t += gap
    return Transcript(model="test", language="en", duration=t + 1, words=words, sentences=sents)


@pytest.fixture
def transcript_factory():
    return make_transcript
