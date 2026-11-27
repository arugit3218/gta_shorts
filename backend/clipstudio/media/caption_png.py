"""Captions and text overlays for FFmpeg builds without libass (e.g. Homebrew FFmpeg 8.x).

Adapted from nero-harsh/clipstudio (``media/caption_png.py``): text is drawn with Pillow into transparent
images and FFmpeg lays them over the video. Instead of re-implementing each feature, this reads the very
ASS files ClipStudio already writes (captions, title text, end text, #ad label, POV text, progress bar) and
draws them, so both paths always show the same words, colours, sizes and positions.

What's simplified compared with libass: the small "pop" scale-in and fades are dropped (text appears at
its final size), and a glow is drawn as a soft outline.

How it reaches FFmpeg: every change of what's on screen becomes one image, listed with its duration in a
concat file; ``movie=…:f=concat`` reads them as a stream that ``overlay`` lays over the video.
"""

from __future__ import annotations

import logging
import os
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from . import ffmpeg as ff

log = logging.getLogger(__name__)

FONTS_BUNDLED = Path(__file__).resolve().parent.parent / "assets" / "fonts"
FONT_DIRS = [
    FONTS_BUNDLED,
    Path("/System/Library/Fonts/Supplemental"),
    Path("/System/Library/Fonts"),
    Path("/Library/Fonts"),
    Path.home() / "Library" / "Fonts",
    Path("/usr/share/fonts/truetype"),
    Path("/usr/share/fonts"),
]
FALLBACK_FONTS = ["Arial Bold", "Arial", "Helvetica", "DejaVuSans-Bold", "DejaVuSans"]


# ----------------------------------------------------------------------------- which path
@lru_cache(maxsize=4)
def _ffmpeg_has_libass(ffmpeg: str) -> bool:
    try:
        out = subprocess.run([ffmpeg, "-hide_banner", "-filters"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.SubprocessError):
        return True  # can't tell: keep the normal path
    return bool(re.search(r"^\s*\S*\s+ass\s+V->V", out, re.M))


def use_images() -> bool:
    """True when captions must be drawn as images. ``CLIPSTUDIO_CAPTIONS=images`` forces it (for testing);
    ``=libass`` forces the normal path."""
    force = os.environ.get("CLIPSTUDIO_CAPTIONS", "").strip().lower()
    if force in ("images", "png"):
        return True
    if force in ("libass", "ass"):
        return False
    return not _ffmpeg_has_libass(ff.FFMPEG)


def backend_name() -> str:
    return "images" if use_images() else "libass"


# ----------------------------------------------------------------------------- fonts
def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


@lru_cache(maxsize=1)
def _font_index() -> dict[str, str]:
    """normalised file stem → path, for every font file in the font folders (one level of subfolders)."""
    idx: dict[str, str] = {}
    for d in FONT_DIRS:
        if not d.is_dir():
            continue
        for p in [*d.glob("*"), *d.glob("*/*")]:
            if p.suffix.lower() in (".ttf", ".otf", ".ttc"):
                idx.setdefault(_norm(p.stem), str(p))
    return idx


@lru_cache(maxsize=64)
def find_font(name: str, bold: bool) -> str | None:
    idx = _font_index()
    wants = [f"{name} Bold", f"{name}-Bold", name, f"{name} Regular", f"{name}-Regular"] if bold else [name, f"{name} Regular", f"{name}-Regular"]
    for w in wants:
        if _norm(w) in idx:
            return idx[_norm(w)]
    # same family under another file name: "Montserrat" → Montserrat-ExtraBold.ttf, "Helvetica Neue" → HelveticaNeue.ttc
    key = _norm(name)
    family = sorted((stem for stem in idx if stem.startswith(key)), key=lambda st: (("bold" in st) != bold, len(st)))
    if family:
        return idx[family[0]]
    for w in (FALLBACK_FONTS if bold else ["Arial", "Helvetica", "DejaVuSans", "Arial Bold"]):
        if _norm(w) in idx:
            return idx[_norm(w)]
    return None


@lru_cache(maxsize=128)
def _font(name: str, bold: bool, px: int):
    """A font whose full line height (ascender + descender) is ``px``, which is how libass reads an ASS
    font size; Pillow's size is the smaller em size, so it's scaled down to match."""
    path = find_font(name, bold)
    if path:
        try:
            f = ImageFont.truetype(path, max(6, px))
            a, d = f.getmetrics()
            if a + d > 0:
                f = ImageFont.truetype(path, max(6, round(px * px / (a + d))))
            return f
        except OSError:
            pass
    log.warning("Caption font %r not found; using the default font", name)
    return ImageFont.load_default(max(6, px))


# ----------------------------------------------------------------------------- ASS reading
@dataclass
class AssStyle:
    font: str
    size: float
    primary: tuple
    outline_color: tuple
    back: tuple
    bold: bool
    border_style: int
    outline: float
    shadow: float
    align: int


@dataclass
class Seg:
    text: str
    color: tuple
    scale: float = 1.0


@dataclass
class Event:
    start: float
    end: float
    layer: int
    style: AssStyle
    lines: list[list[Seg]]
    pos: tuple[float, float] | None
    align: int
    glow: bool = False
    bar: dict | None = None  # progress bar (drawing + animated clip)
    extra: dict = field(default_factory=dict)


def _color(v: str) -> tuple:
    """ASS &HAABBGGRR (or &HBBGGRR&) → RGBA."""
    h = v.strip().lstrip("&").lstrip("Hh").rstrip("&")
    h = h.rjust(8, "0")
    a, b, g, r = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16), int(h[6:8], 16)
    return (r, g, b, 255 - a)


def _time(t: str) -> float:
    h, m, s = t.split(":")
    return int(h) * 3600 + int(m) * 60 + float(s)


def parse_ass(text: str) -> tuple[int, int, list[Event]]:
    res_x, res_y = 1080, 1920
    styles: dict[str, AssStyle] = {}
    events: list[Event] = []
    fmt: list[str] = []
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("PlayResX:"):
            res_x = int(line.split(":", 1)[1])
        elif line.startswith("PlayResY:"):
            res_y = int(line.split(":", 1)[1])
        elif line.startswith("Format:") and not fmt and "Fontname" in line:
            fmt = [x.strip() for x in line.split(":", 1)[1].split(",")]
        elif line.startswith("Style:"):
            vals = [x.strip() for x in line.split(":", 1)[1].split(",")]
            d = dict(zip(fmt, vals))
            styles[d["Name"]] = AssStyle(
                font=d["Fontname"], size=float(d["Fontsize"]), primary=_color(d["PrimaryColour"]), outline_color=_color(d["OutlineColour"]),
                back=_color(d["BackColour"]), bold=d["Bold"] not in ("0", ""), border_style=int(d["BorderStyle"]), outline=float(d["Outline"]),
                shadow=float(d["Shadow"]), align=int(d["Alignment"]),
            )
        elif line.startswith("Dialogue:"):
            parts = line.split(":", 1)[1].split(",", 9)
            if len(parts) < 10 or parts[3].strip() not in styles:
                continue
            ev = _event(float(_time(parts[1].strip())), float(_time(parts[2].strip())), int(parts[0]), styles[parts[3].strip()], parts[9])
            if ev:
                events.append(ev)
    return res_x, res_y, events


_TAG = re.compile(r"\{([^}]*)\}")
# emoji and pictographs: the caption fonts have no glyphs for them (Pillow would draw empty boxes)
_NO_GLYPH = re.compile("[\U00010000-\U0010FFFF\u2600-\u27BF\uFE0F\u200D]")


def _event(start: float, end: float, layer: int, st: AssStyle, text: str) -> Event | None:
    if end <= start:
        return None
    pos = None
    align = st.align
    glow = False
    color = st.primary
    scale = 1.0
    lines: list[list[Seg]] = [[]]
    bar = None
    if r"\p1" in text:  # the progress bar: a rectangle drawing, revealed by an animated clip
        tags = text
        m_pos = re.search(r"\\pos\(([\d.-]+),([\d.-]+)\)", tags)
        m_col = re.search(r"\\1c(&H[0-9A-Fa-f]{6,8})&?", tags)
        m_alpha = re.search(r"\\1a&H([0-9A-Fa-f]{2})&", tags)
        m_shape = re.search(r"m 0 0 l ([\d.]+) 0 [\d.]+ ([\d.]+)", tags)
        clips = re.findall(r"\\clip\(([\d.]+),([\d.]+),([\d.]+),([\d.]+)\)", tags)
        if not (m_pos and m_shape):
            return None
        rgba = _color(m_col.group(1)) if m_col else st.primary
        if m_alpha:
            rgba = (*rgba[:3], 255 - int(m_alpha.group(1), 16))
        w_, h_ = float(m_shape.group(1)), float(m_shape.group(2))
        bar = {"x": float(m_pos.group(1)), "y": float(m_pos.group(2)), "w": w_, "h": h_, "color": rgba}
        if clips:
            bar["x0"] = float(clips[0][2])  # filled up to here at the start …
            bar["x1"] = float(clips[-1][2])  # … and up to here at the end
        return Event(start, end, layer, st, [], None, 7, bar=bar)
    pieces = _TAG.split(text)
    for i, piece in enumerate(pieces):
        if i % 2 == 1:  # override tags
            tag = re.sub(r"\\t\([^)]*\)", lambda m: m.group(0)[3:-1], piece)  # a transition: take its final values
            if m := re.search(r"\\pos\(([\d.-]+),([\d.-]+)\)", tag):
                pos = (float(m.group(1)), float(m.group(2)))
            if m := re.search(r"\\an(\d)", tag):
                align = int(m.group(1))
            for m in re.finditer(r"\\1?c(&H[0-9A-Fa-f]{6,8})&?", tag):
                color = _color(m.group(1))
            if ms := re.findall(r"\\fscx(\d+(?:\.\d+)?)", tag):
                scale = float(ms[-1]) / 100
            if re.search(r"\\blur\d", tag):
                glow = True
            continue
        for k, chunk in enumerate(piece.split("\\N")):
            if k > 0:
                lines.append([])
            chunk = _NO_GLYPH.sub("", chunk.replace("\\h", " "))
            if chunk:
                lines[-1].append(Seg(chunk, color, scale))
    lines = [ln for ln in lines if any(s.text.strip() for s in ln)]
    if not lines:
        return None
    return Event(start, end, layer, st, lines, pos, align, glow)


# ----------------------------------------------------------------------------- drawing
def _draw_event(img: Image.Image, ev: Event, ox: float = 0, oy: float = 0) -> None:
    """Draw one event; (ox, oy) = where ``img`` sits in the frame (images are cropped to the text area)."""
    st = ev.style
    draw = ImageDraw.Draw(img)
    stroke = max(0, round(st.outline)) if st.border_style != 3 else 0
    line_boxes = []
    for ln in ev.lines:
        # advance widths (they include the spaces carried at the start of a word's text)
        widths = [draw.textlength(s.text, font=_font(st.font, st.bold, round(st.size * s.scale))) for s in ln]
        line_boxes.append((sum(widths) + 2 * stroke, widths))
    line_h = st.size * 1.05
    total_h = line_h * len(ev.lines)
    max_w = max(b[0] for b in line_boxes)
    x, y = ev.pos if ev.pos else (ev.extra.get("w", 1080) / 2, ev.extra.get("h", 1920) / 2)
    x, y = x - ox, y - oy
    # anchor (numpad layout: 7 8 9 top, 4 5 6 middle, 1 2 3 bottom)
    col = (ev.align - 1) % 3
    row = 2 - (ev.align - 1) // 3
    left = x - max_w * (0, 0.5, 1)[col]
    top = y - total_h * (0, 0.5, 1)[row]
    if st.border_style == 3:  # opaque box behind the text (libass fills it with the outline colour)
        pad = st.outline
        draw.rounded_rectangle([left - pad, top - pad * 0.6, left + max_w + pad, top + total_h + pad * 0.6], radius=max(2, pad * 0.35), fill=st.outline_color)
    for li, (ln, (lw, widths)) in enumerate(zip(ev.lines, line_boxes)):
        lx = left + (max_w - lw) * (0, 0.5, 1)[col] + stroke
        ly = top + li * line_h + line_h / 2
        for s, w in zip(ln, widths):
            f = _font(st.font, st.bold, round(st.size * s.scale))
            if st.shadow > 0 and st.border_style != 3:
                draw.text((lx + st.shadow, ly + st.shadow), s.text, font=f, fill=(0, 0, 0, 140), anchor="lm", stroke_width=stroke, stroke_fill=(0, 0, 0, 140))
            if ev.glow:  # soft glow, blurred only around this word
                gb = draw.textbbox((lx, ly), s.text, font=f, anchor="lm", stroke_width=stroke + 4)
                pad = 12
                gx, gy = int(gb[0]) - pad, int(gb[1]) - pad
                g = Image.new("RGBA", (int(gb[2] - gb[0]) + 2 * pad, int(gb[3] - gb[1]) + 2 * pad), (0, 0, 0, 0))
                ImageDraw.Draw(g).text((lx - gx, ly - gy), s.text, font=f, fill=st.outline_color, anchor="lm", stroke_width=stroke + 4, stroke_fill=st.outline_color)
                layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
                layer.paste(g.filter(ImageFilter.GaussianBlur(5)), (gx, gy))
                img.alpha_composite(layer)
                draw = ImageDraw.Draw(img)
            draw.text((lx, ly), s.text, font=f, fill=s.color, anchor="lm", stroke_width=stroke, stroke_fill=st.outline_color)
            lx += w


# ----------------------------------------------------------------------------- to FFmpeg
def overlay_chain(ass_text: str, name: str, workdir: Path, src: str, out: str, duration: float) -> str:
    """Filter chain ``[src] … [out]`` drawing this ASS file as images (the libass-free path)."""
    width, height, events = parse_ass(ass_text)
    text_events = [e for e in events if e.bar is None]
    bars = [e for e in events if e.bar is not None]
    chain: list[str] = []
    cur = src
    if text_events:
        for e in text_events:
            e.extra |= {"w": width, "h": height}
        # 1. the area any text uses: measure each event once (one full frame at a time, then dropped)
        boxes = []
        for e in text_events:
            probe = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            _draw_event(probe, e)
            if b := probe.getbbox():
                boxes.append(b)
            del probe
        if boxes:
            x0, y0 = max(0, min(b[0] for b in boxes) // 2 * 2), max(0, min(b[1] for b in boxes) // 2 * 2)
            x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
            cw, ch = max(2, min(width - x0, (x1 - x0 + 1) // 2 * 2)), max(2, min(height - y0, (y1 - y0 + 1) // 2 * 2))
            # 2. every change of what's on screen = one small image, written as soon as it's drawn
            times = sorted({0.0, *(e.start for e in text_events), *(e.end for e in text_events)})
            times = [t for t in times if t < duration + 1] + [duration + 1]
            files: dict[tuple, str] = {}
            lines = ["ffconcat version 1.0"]
            last = ""
            for t0, t1 in zip(times, times[1:]):
                if t1 - t0 < 1e-3:
                    continue
                live = tuple(sorted((i for i, e in enumerate(text_events) if e.start <= t0 < e.end), key=lambda i: text_events[i].layer))
                if live not in files:
                    im = Image.new("RGBA", (cw, ch), (0, 0, 0, 0))
                    for i in live:
                        _draw_event(im, text_events[i], x0, y0)
                    fn = f"{name}_{len(files):04d}.png"
                    im.save(workdir / fn, compress_level=1)
                    files[live] = fn
                last = files[live]
                lines += [f"file {last}", f"duration {t1 - t0:.3f}"]
            lines.append(f"file {last}")  # the concat demuxer needs the last image twice
            (workdir / f"{name}.txt").write_text("\n".join(lines) + "\n")
            chain.append(f"movie={name}.txt:f=concat,format=rgba,setpts=PTS-STARTPTS[{name}img];[{cur}][{name}img]overlay=x={x0}:y={y0}:eof_action=pass:repeatlast=1[{name}t]")
            cur = f"{name}t"
    for k, e in enumerate(bars):
        b = e.bar or {}
        w_, h_ = max(2, round(b["w"])), max(2, round(b["h"]))
        r, g, bl, a = b["color"]
        color = f"0x{r:02X}{g:02X}{bl:02X}@{a / 255:.3f}"
        lbl = f"{name}b{k}"
        if "x1" in b:  # the filled part grows from x0 to x1 over the clip: slide a bar in from the left
            x0, x1 = b["x0"], b["x1"]
            span = max(0.01, e.end - e.start - 1) if e.end - e.start > 1 else duration
            xexpr = f"-{w_}+{x0:.1f}+({x1 - x0:.1f})*min(1,t/{span:.3f})"
            chain.append(f"color=c={color}:s={w_}x{h_}:d={duration + 1:.3f},format=rgba[{lbl}c];[{cur}][{lbl}c]overlay=x='{xexpr}':y={round(b['y'])}:eof_action=pass[{lbl}]")
        else:  # the dim track behind it
            chain.append(f"color=c={color}:s={w_}x{h_}:d={duration + 1:.3f},format=rgba[{lbl}c];[{cur}][{lbl}c]overlay=x={round(b['x'])}:y={round(b['y'])}:eof_action=pass[{lbl}]")
        cur = lbl
    if cur == src:
        return f"[{src}]null[{out}]"
    return ";".join(chain) + f";[{cur}]null[{out}]"

