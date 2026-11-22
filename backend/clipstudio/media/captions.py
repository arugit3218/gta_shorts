"""Word-level animated captions rendered as ASS subtitles (burned in by FFmpeg/libass).

Design notes:
- Captions are grouped into short "pages" (≤ max_words, never across a sentence end or a long pause).
- The active word is highlighted by *colour only* — changing its size would reflow a centred line
  and make the text jitter. "Pop" animates the whole page once when it appears.
- Sizes are relative to output height so presets look the same at 720p and 1080p.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

Animation = Literal["none", "highlight", "pop", "fade", "karaoke"]


class CaptionStyle(BaseModel):
    name: str = "Custom"
    font: str = "Arial Black"
    size: float = Field(0.055, ge=0.02, le=0.12, description="font size as a fraction of output height")
    color: str = "#FFFFFF"
    highlight_color: str = "#FFE600"
    outline_color: str = "#000000"
    outline: float = Field(0.006, ge=0, le=0.03, description="outline width as a fraction of height")
    shadow: float = Field(0.0, ge=0, le=0.03)
    bold: bool = True
    uppercase: bool = True
    position: float = Field(0.70, ge=0.05, le=0.95, description="vertical centre of captions, 0=top 1=bottom")
    max_words: int = Field(3, ge=1, le=12)
    max_lines: int = Field(2, ge=1, le=3)
    animation: Animation = "pop"
    background: bool = False  # opaque box behind text
    background_color: str = "#000000"


PRESETS: dict[str, CaptionStyle] = {
    "classic": CaptionStyle(name="Classic white", font="Arial", size=0.042, bold=True, uppercase=False, max_words=7, animation="none", outline=0.004, shadow=0.002, position=0.80),
    "bold": CaptionStyle(name="Bold animated", font="Arial Black", size=0.058, uppercase=True, max_words=3, animation="pop", highlight_color="#FFE600"),
    "highlight": CaptionStyle(name="Highlighted word", font="Arial Black", size=0.05, uppercase=False, max_words=4, animation="highlight", highlight_color="#22E06B", outline=0.007),
    "karaoke": CaptionStyle(name="Karaoke fill", font="Impact", size=0.06, uppercase=True, max_words=4, animation="karaoke", highlight_color="#00D1FF", outline=0.006),
    "cinematic": CaptionStyle(name="Cinematic", font="Georgia", size=0.038, bold=False, uppercase=False, max_words=8, animation="fade", outline=0.0, shadow=0.004, position=0.86, color="#F5F1E8"),
    "minimal": CaptionStyle(name="Minimal", font="Helvetica Neue", size=0.036, bold=False, uppercase=False, max_words=6, animation="none", outline=0.0, shadow=0.0, background=True, background_color="#000000", position=0.82),
}


@dataclass
class CapWord:
    w: str
    s: float
    e: float


@dataclass
class Page:
    words: list[CapWord] = field(default_factory=list)
    start: float = 0.0
    end: float = 0.0


def ass_color(hex_color: str, alpha: int = 0) -> str:
    h = hex_color.lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", h):
        h = "FFFFFF"
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H{alpha:02X}{b}{g}{r}".upper()


def _ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _escape(text: str) -> str:
    return text.replace("\\", "/").replace("{", "(").replace("}", ")").replace("\n", " ")


def paginate(words: list[CapWord], style: CaptionStyle, *, width: int, height: int) -> list[Page]:
    font_px = style.size * height
    max_chars_line = max(8, int((width * 0.86) / (font_px * 0.62)))
    max_chars = max_chars_line * style.max_lines
    pages: list[Page] = []
    cur: list[CapWord] = []
    for i, w in enumerate(words):
        if cur:
            gap = w.s - cur[-1].e
            chars = sum(len(x.w) + 1 for x in cur) + len(w.w)
            sentence_end = cur[-1].w.endswith((".", "!", "?"))
            if len(cur) >= style.max_words or chars > max_chars or gap > 0.6 or sentence_end:
                pages.append(Page(cur, cur[0].s, cur[-1].e))
                cur = []
        cur.append(w)
    if cur:
        pages.append(Page(cur, cur[0].s, cur[-1].e))
    # Hold each page until the next one starts (max +0.5 s) so text doesn't flicker.
    for a, b in zip(pages, pages[1:]):
        a.end = min(b.start, a.end + 0.5) if b.start > a.end else b.start
    if pages:
        pages[-1].end += 0.4
    return pages


def _wrap(tokens: list[str], max_lines: int, max_chars_line: int) -> list[list[int]]:
    """Split token indices into ≤ max_lines balanced lines."""
    n = len(tokens)
    width = lambda a, b: sum(len(t) for t in tokens[a:b]) + (b - a - 1)  # noqa: E731
    if max_lines <= 1 or n < 2 or width(0, n) <= max_chars_line:
        return [list(range(n))]
    # Brute-force the split(s) minimising the longest line (n is tiny: ≤ 12 words).
    best: tuple[int, list[int]] | None = None
    if max_lines == 2 or n < 3:
        for k in range(1, n):
            cost = max(width(0, k), width(k, n))
            if best is None or cost < best[0]:
                best = (cost, [k])
    else:
        for k1 in range(1, n - 1):
            for k2 in range(k1 + 1, n):
                cost = max(width(0, k1), width(k1, k2), width(k2, n))
                if best is None or cost < best[0]:
                    best = (cost, [k1, k2])
        # Prefer 2 lines if they fit as well as 3.
        for k in range(1, n):
            cost = max(width(0, k), width(k, n))
            if cost <= max_chars_line and (best is None or cost <= best[0] + 2):
                best = (cost, [k])
                break
    cuts = [0, *best[1], n] if best else [0, n]
    return [list(range(a, b)) for a, b in zip(cuts, cuts[1:])]


def build_ass(words: list[CapWord], style: CaptionStyle, *, width: int, height: int) -> str:
    font_px = round(style.size * height)
    outline = round(style.outline * height, 1)
    shadow = round(style.shadow * height, 1)
    margin_h = round(width * 0.07)
    # Alignment 5 = middle-centre; we position explicitly with \pos.
    border_style = 3 if style.background else 1
    back = ass_color(style.background_color, 0x40) if style.background else ass_color("#000000", 0x80)
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Cap,{style.font},{font_px},{ass_color(style.color)},{ass_color(style.highlight_color)},{ass_color(style.outline_color)},{back},{-1 if style.bold else 0},0,0,0,100,100,0,0,{border_style},{outline if not style.background else round(font_px * 0.18)},{shadow},5,{margin_h},{margin_h},0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    pos = f"\\pos({width // 2},{round(style.position * height)})"
    hi = ass_color(style.highlight_color)
    base = ass_color(style.color)
    max_chars_line = max(8, int((width * 0.86) / (font_px * 0.62)))
    events: list[str] = []
    for page in paginate(words, style, width=width, height=height):
        toks = [_escape(w.w.upper() if style.uppercase else w.w) for w in page.words]
        lines = _wrap(toks, style.max_lines, max_chars_line)

        def render(active: int | None, karaoke_upto: int | None = None) -> str:
            parts = []
            for li, line in enumerate(lines):
                seg = []
                for i in line:
                    t = toks[i]
                    if active is not None and i == active:
                        seg.append(f"{{\\c{hi}}}{t}{{\\c{base}}}")
                    elif karaoke_upto is not None and i <= karaoke_upto:
                        seg.append(f"{{\\c{hi}}}{t}{{\\c{base}}}")
                    else:
                        seg.append(t)
                parts.append(" ".join(seg))
            return "\\N".join(parts)

        if style.animation in ("highlight", "pop", "karaoke"):
            for k, w in enumerate(page.words):
                s = page.start if k == 0 else w.s
                e = page.words[k + 1].s if k + 1 < len(page.words) else page.end
                if e <= s:
                    continue
                tag = pos
                if style.animation == "pop" and k == 0:
                    tag += "\\fscx80\\fscy80\\t(0,110,\\fscx100\\fscy100)"
                text = render(None, k) if style.animation == "karaoke" else render(k)
                events.append(f"Dialogue: 0,{_ass_time(s)},{_ass_time(e)},Cap,,0,0,0,,{{{tag}}}{text}")
        else:
            tag = pos + ("\\fad(150,150)" if style.animation == "fade" else "")
            events.append(f"Dialogue: 0,{_ass_time(page.start)},{_ass_time(page.end)},Cap,,0,0,0,,{{{tag}}}{render(None)}")
    return header + "\n".join(events) + "\n"


def resolve_style(preset: str, overrides: dict | None = None) -> CaptionStyle:
    base = PRESETS.get(preset, PRESETS["bold"])
    if not overrides:
        return base
    return CaptionStyle.model_validate(base.model_dump() | {k: v for k, v in overrides.items() if v is not None})
