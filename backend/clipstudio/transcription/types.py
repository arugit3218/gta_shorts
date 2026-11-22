"""Transcript data model shared by all transcription backends and the analysis engine."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class Word:
    w: str
    s: float  # start seconds
    e: float  # end seconds
    c: float = 1.0  # confidence


@dataclass
class Sentence:
    start: float
    end: float
    text: str
    i0: int  # first word index (inclusive)
    i1: int  # last word index (exclusive)


@dataclass
class Transcript:
    model: str
    language: str
    duration: float
    words: list[Word] = field(default_factory=list)
    sentences: list[Sentence] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(s.text for s in self.sentences)

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "language": self.language,
            "duration": self.duration,
            "words": [asdict(w) for w in self.words],
            "sentences": [asdict(s) for s in self.sentences],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Transcript":
        return cls(
            model=d["model"],
            language=d.get("language", "en"),
            duration=d.get("duration", 0.0),
            words=[Word(**w) for w in d["words"]],
            sentences=[Sentence(**s) for s in d["sentences"]],
        )


def _ts(t: float, sep: str = ",") -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(t: Transcript, max_words: int = 12) -> str:
    """SRT with sentences split into readable chunks of at most ``max_words``."""
    out: list[str] = []
    n = 1
    for s in t.sentences:
        ws = t.words[s.i0 : s.i1]
        for k in range(0, len(ws), max_words):
            chunk = ws[k : k + max_words]
            if not chunk:
                continue
            out.append(f"{n}\n{_ts(chunk[0].s)} --> {_ts(chunk[-1].e)}\n{' '.join(w.w for w in chunk)}\n")
            n += 1
    return "\n".join(out)
