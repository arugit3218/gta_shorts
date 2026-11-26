"""Retention polish (Phase 4A): planning code that decides *what* to change in a clip.

Everything here is pure planning on word timestamps and a loudness envelope; the render step turns
the plans into FFmpeg filters.

- Dead-air / filler removal ("jump cuts"): pauses between words are cut only when the audio there
  is actually quiet, so gameplay sound, music or reactions between sentences are never removed.
  Filler words (um, uh, …) are cut out too. Cut points are snapped to the video frame grid.
- Keyword emphasis: at most one word per caption page gets the emphasis colour (numbers, money,
  strong words, words that map to an emoji, long content words).
- Emoji: a few emphasised words get a matching emoji above the captions.
- Punch-in zooms: alternate segments after jump cuts are zoomed in (hides the cut), plus short
  punch-ins on emphasised words or, without speech, on the loudest moments.
- Profanity: listed words are beeped or muted and masked in the captions (f***).
- Cold open: the strongest 2–3 s of the clip, shown first as a teaser.
"""

from __future__ import annotations

import logging
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from . import ffmpeg as ff
from .captions import CapWord, CaptionStyle, paginate

log = logging.getLogger(__name__)

ENV_RATE = 50  # envelope frames per second (20 ms)

# ----------------------------------------------------------------------------- vocabulary
FILLERS = {"um", "umm", "uh", "uhh", "uhm", "erm", "er", "ah", "hmm", "mm", "mhm"}

_PROFANE = re.compile(
    r"^(\w*fuck\w*|\w*shit\w*|bitch\w*|cunts?|dick|dicks|dickhead\w*|pussy|pussies|asshole\w*|"
    r"bastards?|nigg\w*|fag|fags|faggot\w*|retard\w*|whores?|sluts?|cock|cocks|wank\w*|twats?)$"
)

STOPWORDS = set(
    """a an the and or but if then so to of in on at by for with from up down out over under is are was were be been
    being am i you he she it we they me him her us them my your his its our their this that these those there here
    what which who whom whose when where why how all any both each few more most other some such no nor not only own
    same than too very can will just don should now do does did doing have has had having would could about into
    through during before after again further once also yeah okay ok like really gonna wanna got get gets going go
    know think thing things something stuff right well kind sort mean actually literally basically""".split()
)

STRONG = set(
    """insane crazy never always best worst biggest secret free million millions billion billions money dead died
    kill killed killing win won lost lose impossible unbelievable incredible huge massive instantly every nobody
    everyone everything nothing banned illegal warning mistake shocking crazy ridiculous legendary perfect
    exactly finally first last only""".split()
)

EMOJI: dict[str, set[str]] = {
    "💰": {"money", "cash", "dollars", "dollar", "rich", "paid", "pay", "million", "millions", "billion", "billions", "profit", "salary", "price", "expensive", "cheap"},
    "🔥": {"fire", "insane", "crazy", "hot", "lit", "amazing", "incredible", "unbelievable", "legendary", "best"},
    "😂": {"funny", "laugh", "laughing", "lol", "hilarious", "joke", "jokes", "lmao"},
    "😱": {"shocked", "shocking", "scary", "scared", "terrifying", "omg", "wow", "insanely"},
    "💀": {"dead", "died", "die", "kill", "killed", "killing", "death", "rip", "murdered"},
    "❤️": {"love", "loved", "loving", "heart"},
    "🧠": {"think", "smart", "idea", "ideas", "learn", "learned", "brain", "genius", "lesson"},
    "🚀": {"fast", "faster", "grow", "growth", "launch", "rocket", "speed", "boost"},
    "🏆": {"win", "won", "winner", "winning", "champion", "victory", "trophy"},
    "⚠️": {"warning", "danger", "dangerous", "mistake", "mistakes", "wrong", "careful"},
    "🚓": {"police", "cops", "cop", "arrested", "wanted", "chase"},
    "🚗": {"car", "cars", "drive", "driving", "drift", "race", "racing"},
    "👀": {"look", "watch", "secret", "secrets", "hidden", "see"},
    "💯": {"exactly", "perfect", "facts", "true", "hundred"},
}
_EMOJI_OF = {w: e for e, ws in EMOJI.items() for w in ws}


def norm(word: str) -> str:
    return re.sub(r"[^a-z0-9$%']", "", word.lower()).strip("'")


def is_filler(word: str) -> bool:
    return norm(word) in FILLERS


def is_profane(word: str) -> bool:
    return bool(_PROFANE.match(norm(word).replace("'", "")))


def mask(word: str) -> str:
    m = re.match(r"^(\W*)(\w)(\w*)(\W*)$", word)
    if not m:
        return "*" * len(word)
    return f"{m.group(1)}{m.group(2)}{'*' * len(m.group(3))}{m.group(4)}"


# ----------------------------------------------------------------------------- audio envelope
def audio_envelope(src: Path, start: float, duration: float) -> np.ndarray:
    """Loudness in dBFS, one value per 20 ms, for [start, start + duration] of ``src``."""
    sr = 16000
    cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-loglevel", "error", "-ss", f"{start:.3f}", "-t", f"{duration:.3f}", "-i", str(src), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"]
    out = subprocess.run(cmd, capture_output=True, timeout=max(60, duration * 2), check=False).stdout
    x = np.frombuffer(out, dtype=np.int16).astype(np.float32) / 32768.0
    hop = sr // ENV_RATE
    n = x.size // hop
    if n == 0:
        return np.zeros(0, np.float32)
    frames = x[: n * hop].reshape(n, hop)
    rms = np.sqrt(np.mean(frames**2, axis=1) + 1e-12)
    return (20 * np.log10(rms)).astype(np.float32)


def _env_slice(env: np.ndarray, a: float, b: float) -> np.ndarray:
    i, j = int(a * ENV_RATE), int(np.ceil(b * ENV_RATE))
    return env[max(0, i) : max(0, min(env.size, j))]


def speech_level(env: np.ndarray, words: list[CapWord]) -> float:
    vals = [v for w in words for v in _env_slice(env, w.s, w.e)]
    return float(np.median(vals)) if vals else float(np.median(env)) if env.size else -30.0


# ----------------------------------------------------------------------------- jump cuts
JUMPCUT = {
    # min pause to cut, silence kept after the last word, silence kept before the next word
    "light": (0.90, 0.18, 0.12),
    "normal": (0.60, 0.14, 0.10),
    "tight": (0.38, 0.10, 0.07),
}


@dataclass
class Timeline:
    """Kept parts of a clip (clip-relative source seconds), in order."""

    segments: list[tuple[float, float]]
    source_duration: float

    @classmethod
    def full(cls, duration: float) -> Timeline:
        return cls([(0.0, duration)], duration)

    @property
    def cut(self) -> bool:
        return len(self.segments) > 1 or (bool(self.segments) and (self.segments[0][0] > 0.01 or self.segments[-1][1] < self.source_duration - 0.01))

    @property
    def out_duration(self) -> float:
        return sum(e - s for s, e in self.segments)

    @property
    def removed(self) -> float:
        return max(0.0, self.source_duration - self.out_duration)

    def offsets(self) -> list[float]:
        out, acc = [], 0.0
        for s, e in self.segments:
            out.append(acc)
            acc += e - s
        return out

    def cut_points(self) -> list[float]:
        """Output times where a jump cut happens."""
        return self.offsets()[1:]

    def map_time(self, t: float) -> float | None:
        for (s, e), off in zip(self.segments, self.offsets()):
            if s - 1e-6 <= t < e:
                return off + (t - s)
        return None

    def map_words(self, words: list[CapWord]) -> list[CapWord]:
        out: list[CapWord] = []
        offs = self.offsets()
        for w in words:
            mid = (w.s + w.e) / 2
            for (s, e), off in zip(self.segments, offs):
                if s <= mid < e:
                    out.append(CapWord(w.w, round(off + max(w.s, s) - s, 3), round(off + min(w.e, e) - s, 3)))
                    break
        return out

    def select_expr(self, frame: float) -> str:
        """FFmpeg select/aselect expression keeping the segments (``frame`` = one frame's length)."""
        h = frame / 2
        return "+".join(f"between(t,{max(0.0, s - h):.4f},{e - h - 1e-4:.4f})" for s, e in self.segments)


def plan_segments(words: list[CapWord], duration: float, env: np.ndarray | None, *, level: str = "normal", fillers: bool = True, fps: float = 30.0) -> Timeline:
    """Which parts of the clip to keep. Pauses are only removed where the audio is quiet."""
    if level not in JUMPCUT or not words or env is None or env.size == 0:
        return Timeline.full(duration)
    min_gap, keep_after, keep_before = JUMPCUT[level]
    level_db = speech_level(env, words)
    quiet_db = min(level_db - 16.0, -26.0)

    def quiet(a: float, b: float) -> bool:
        seg = _env_slice(env, a, b)
        return seg.size > 0 and (float(np.percentile(seg, 90)) < quiet_db or float(np.percentile(seg, 90)) < -50)

    cuts: list[tuple[float, float]] = []
    first, last = words[0], words[-1]
    if first.s > keep_before + 0.3 and quiet(0, first.s - keep_before):
        cuts.append((0.0, first.s - keep_before))
    for i, w in enumerate(words):
        if fillers and is_filler(w.w):
            lo = words[i - 1].e if i > 0 else 0.0
            hi = words[i + 1].s if i + 1 < len(words) else duration
            cuts.append((max(lo, w.s - 0.03), min(hi, w.e + 0.03)))
    # pauses are measured between all words (a filler's own audio is never part of a "quiet" check)
    for a, b in zip(words, words[1:]):
        gap = b.s - a.e
        if gap >= min_gap:
            ca, cb = a.e + keep_after, b.s - keep_before
            if cb - ca > 0.2 and quiet(ca, cb):
                cuts.append((ca, cb))
    tail = last.e + 0.35
    if duration - tail > 0.5 and quiet(tail, duration):
        cuts.append((tail, duration))
    if not cuts:
        return Timeline.full(duration)
    # merge, snap to the frame grid, invert into kept segments
    cuts.sort()
    merged: list[list[float]] = []
    for a, b in cuts:
        if merged and a <= merged[-1][1] + 0.05:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    snap = lambda t: round(round(t * fps) / fps, 4)  # noqa: E731
    keep: list[tuple[float, float]] = []
    cur = 0.0
    for a, b in merged:
        a, b = snap(a), snap(b)
        if a - cur >= 0.2:
            keep.append((cur, a))
        elif keep and a > cur:
            pass  # a sliver between two cuts: drop it
        cur = max(cur, b)
    if snap(duration) - cur >= 0.2:
        keep.append((cur, snap(duration)))
    tl = Timeline(keep, duration)
    if not keep or tl.out_duration < max(3.0, duration * 0.5):
        return Timeline.full(duration)  # something is off (or nearly all silence): don't butcher the clip
    return tl


# ----------------------------------------------------------------------------- emphasis & emoji
def _word_score(w: str) -> float:
    n = norm(w)
    if not n or n in STOPWORDS or is_filler(n):
        return 0.0
    s = 0.0
    if re.search(r"\d", n) or "$" in w or "%" in w:
        s += 3.0
    if n in STRONG:
        s += 2.5
    if n in _EMOJI_OF:
        s += 1.5
    if w.strip(".,!?").isupper() and len(n) > 1:
        s += 1.0
    if len(n) >= 7:
        s += 1.0
    elif len(n) >= 5:
        s += 0.5
    if w.endswith("!"):
        s += 0.5
    return s


def pick_emphasis(words: list[CapWord], style: CaptionStyle, *, width: int, height: int, threshold: float = 2.0) -> set[int]:
    """Indices of emphasised words: at most one per caption page, only clearly strong words."""
    if not words:
        return set()
    index = {id(w): i for i, w in enumerate(words)}
    out: set[int] = set()
    for page in paginate(words, style, width=width, height=height):
        best = max(page.words, key=lambda w: _word_score(w.w))
        if _word_score(best.w) >= threshold:
            out.add(index[id(best)])
    return out


def pick_emoji(words: list[CapWord], emphasis: set[int], *, max_count: int = 4, spacing: float = 3.0) -> list[tuple[float, float, str]]:
    """(start, end, emoji) for a few emphasised words that have a matching emoji."""
    out: list[tuple[float, float, str]] = []
    last = -1e9
    for i in sorted(emphasis):
        e = _EMOJI_OF.get(norm(words[i].w))
        if not e or words[i].s - last < spacing:
            continue
        out.append((words[i].s, words[i].s + 1.3, e))
        last = words[i].s
        if len(out) >= max_count:
            break
    return out


def render_emoji_png(emoji: str, path: Path, size: int) -> Path | None:
    """Colour emoji as a transparent PNG (macOS Apple Color Emoji via Pillow); None if unavailable."""
    try:
        from PIL import Image, ImageDraw, ImageFont

        font = None
        for cand in ("/System/Library/Fonts/Apple Color Emoji.ttc", "/usr/share/fonts/truetype/noto/NotoColorEmoji.ttf"):
            if Path(cand).exists():
                font = ImageFont.truetype(cand, 160 if "Apple" in cand else 109)
                break
        if font is None:
            return None
        im = Image.new("RGBA", (220, 220), (0, 0, 0, 0))
        ImageDraw.Draw(im).text((110, 110), emoji, font=font, embedded_color=True, anchor="mm")
        box = im.getbbox()
        if not box:
            return None
        im = im.crop(box)
        scale = size / max(im.size)
        im = im.resize((max(2, round(im.width * scale)), max(2, round(im.height * scale))), Image.LANCZOS)
        im.save(path)
        return path
    except Exception as e:  # never fail a render over an emoji
        log.warning("Emoji render failed: %s", e)
        return None


# ----------------------------------------------------------------------------- profanity
def censor(words: list[CapWord]) -> tuple[list[CapWord], list[tuple[float, float]]]:
    """(words with profanity masked, time spans to beep/mute)."""
    out, spans = [], []
    for w in words:
        if is_profane(w.w):
            out.append(CapWord(mask(w.w), w.s, w.e))
            spans.append((max(0.0, w.s - 0.03), w.e + 0.03))
        else:
            out.append(w)
    return out, spans


def enable_expr(spans: list[tuple[float, float]]) -> str:
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in spans) or "0"


# ----------------------------------------------------------------------------- punch-in zooms
ZOOM = {"subtle": 1.10, "punchy": 1.20}


def plan_zooms(
    words: list[CapWord],
    emphasis: set[int],
    duration: float,
    *,
    level: str,
    cut_points: list[float] | None = None,
    env: np.ndarray | None = None,
    timeline: Timeline | None = None,
) -> list[tuple[float, float]]:
    """Output-time spans that are shown zoomed in."""
    if level not in ZOOM or duration < 2:
        return []
    spans: list[tuple[float, float]] = []
    # 1) alternate segments after jump cuts (classic way to hide a jump cut)
    bounds = [0.0, *(cut_points or []), duration]
    for k, (a, b) in enumerate(zip(bounds, bounds[1:])):
        if k % 2 == 1 and b - a >= 0.6:
            spans.append((a, b))
    # 2) punch-in on emphasised words
    gap = 2.5 if level == "punchy" else 4.0
    limit = 99 if level == "punchy" else 3
    last, n = -1e9, 0
    for i in sorted(emphasis):
        t = words[i].s
        if t - last < gap or t < 0.4:
            continue
        spans.append((max(0.0, t - 0.04), min(duration, t + (1.4 if level == "punchy" else 1.8))))
        last, n = t, n + 1
        if n >= limit:
            break
    # 3) no speech: punch in on the loudest moments (gameplay)
    if not words and env is not None and env.size > ENV_RATE * 2:
        med = float(np.median(env))
        order = np.argsort(env)[::-1]
        picked: list[float] = []
        for i in order[:400]:
            if env[i] < med + 10 or len(picked) >= 3:
                break
            t_src = i / ENV_RATE
            t = timeline.map_time(t_src) if timeline else t_src
            if t is None or any(abs(t - p) < 4 for p in picked):
                continue
            picked.append(t)
            spans.append((max(0.0, t - 0.15), min(duration, t + 1.3)))
    if not spans:
        return []
    spans.sort()
    merged: list[list[float]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 0.3:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    out, total = [], 0.0
    for a, b in merged:
        if total + (b - a) > duration * 0.45:
            continue
        out.append((round(a, 3), round(b, 3)))
        total += b - a
    return out


# ----------------------------------------------------------------------------- cold open
def peak_window(words: list[CapWord], duration: float, env: np.ndarray | None, *, peak_t: float | None = None, length: float = 2.6) -> tuple[float, float] | None:
    """The strongest ~2.6 s of the clip (clip-relative), snapped to whole words; None if too short."""
    if duration < 8:
        return None
    lo = duration * 0.3
    hi = duration - 0.4
    if peak_t is not None and lo <= peak_t <= hi:
        a = peak_t - length * 0.45
    elif env is not None and env.size > ENV_RATE * 4:
        win = max(1, int(length * ENV_RATE))
        lin = 10 ** (env / 20)
        sm = np.convolve(lin, np.ones(win) / win, mode="valid")
        i0, i1 = int(lo * ENV_RATE), max(int(lo * ENV_RATE) + 1, int((hi - length) * ENV_RATE))
        sub = sm[i0:i1]
        if sub.size == 0:
            return None
        a = (i0 + int(np.argmax(sub))) / ENV_RATE
    else:
        return None
    a = max(0.0, min(a, duration - length - 0.1))
    b = a + length
    # don't start or end mid-word
    for w in words:
        if w.s < a < w.e:
            a = w.s
        if w.s < b < w.e:
            b = w.e
    b = min(b, a + 4.0, duration)
    return (round(a, 3), round(b, 3)) if b - a >= 1.2 else None


__all__ = [
    "Timeline",
    "plan_segments",
    "audio_envelope",
    "pick_emphasis",
    "pick_emoji",
    "render_emoji_png",
    "censor",
    "plan_zooms",
    "peak_window",
    "enable_expr",
    "ZOOM",
    "JUMPCUT",
]
