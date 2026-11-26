"""Text overlays burned in with the captions: hook title (A6) and on-screen disclosure (A5).

Both are placed inside the Reels/Shorts safe area so platform UI doesn't cover them.
"""

from __future__ import annotations

from dataclasses import dataclass

from .captions import _ass_time, _escape, _wrap, ass_color

HOOK_FONT = "Anton"


HOOK_STYLES = {
    # key: (label, font, text colour, box/outline colour, boxed)
    "box": ("White box", HOOK_FONT, "#111111", "#FFFFFF", True),
    "dark": ("Dark box", HOOK_FONT, "#FFFFFF", "#111111", True),
    "yellow": ("Yellow box", HOOK_FONT, "#111111", "#FFD300", True),
    "banner": ("Red banner", HOOK_FONT, "#FFFFFF", "#E5242B", True),
    "bold": ("Bold outline", HOOK_FONT, "#FFE600", "#000000", False),
    "outline": ("Clean outline", "Montserrat ExtraBold", "#FFFFFF", "#000000", False),
}


@dataclass
class HookText:
    text: str
    duration: float = 3.5
    y: float = 0.2  # vertical centre, fraction of height
    style: str = "box"


@dataclass
class ProgressBar:
    position: str = "top"  # top | bottom
    color: str = "#FFE600"
    start: float = 0.0  # fraction of the bar filled at the start of this video …
    end: float = 1.0  # … and at its end (a cold-open teaser and the main part share one bar)


@dataclass
class DisclosureText:
    text: str
    corner: str = "top_left"


def tidy_hook(text: str, max_words: int = 10) -> str:
    words = (text or "").strip().strip('"“”').split()
    if len(words) > max_words:
        words = words[:max_words]
        words[-1] = words[-1].rstrip(",;:") + "…"
    return " ".join(words)


def build_overlay_ass(
    width: int,
    height: int,
    *,
    hook: HookText | None,
    disclosure: DisclosureText | None,
    clip_duration: float,
    pov: str | None = None,
    progress: ProgressBar | None = None,
) -> str | None:
    if not (hook and hook.text.strip()) and not (disclosure and disclosure.text.strip()) and not (pov and pov.strip()) and progress is None:
        return None
    pov_px = round(height * 0.036)
    hook_px = round(height * 0.042)
    disc_px = round(height * 0.024)
    pad = round(hook_px * 0.32)
    hook_styles = "\n".join(
        f"Style: Hook_{k},{font},{hook_px},{ass_color(fg)},{ass_color(fg)},{ass_color(bg)},{ass_color('#000000', 0x60)},0,0,0,0,100,100,0,0,"
        + (f"3,{pad},0" if boxed else f"1,{max(3, round(hook_px * 0.09))},{max(2, round(hook_px * 0.05))}")
        + ",5,0,0,0,1"
        for k, (_, font, fg, bg, boxed) in HOOK_STYLES.items()
    )
    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{hook_styles}
Style: Bar,Arial,10,{ass_color("#FFFFFF")},{ass_color("#FFFFFF")},{ass_color("#000000")},{ass_color("#000000", 0xFF)},0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: Pov,Montserrat ExtraBold,{pov_px},{ass_color("#FFFFFF")},{ass_color("#FFFFFF")},{ass_color("#000000")},{ass_color("#000000", 0x80)},0,0,0,0,100,100,0,0,1,{max(2, round(pov_px * 0.09))},{max(1, round(pov_px * 0.05))},5,0,0,0,1
Style: Disc,Montserrat ExtraBold,{disc_px},{ass_color("#FFFFFF")},{ass_color("#FFFFFF")},{ass_color("#000000", 0x50)},{ass_color("#000000", 0x50)},0,0,0,0,100,100,0,0,3,{round(disc_px * 0.3)},0,7,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []
    if pov and pov.strip():
        # POV / caption text for the whole montage, upper third (inside the safe area)
        toks = [_escape(w) for w in pov.strip().split()][:24]
        max_chars = max(10, int((width * 0.82) / (pov_px * 0.56)))
        lines = _wrap(toks, 3, max_chars)
        text = "\\N".join(" ".join(toks[i] for i in line) for line in lines)
        events.append(f"Dialogue: 1,{_ass_time(0.0)},{_ass_time(clip_duration + 1)},Pov,,0,0,0,,{{\\pos({width // 2},{round(height * 0.2)})\\fad(200,0)}}{text}")
    if hook and hook.text.strip():
        toks = [_escape(w) for w in tidy_hook(hook.text).upper().split()]
        max_chars = max(8, int((width * 0.80) / (hook_px * 0.5)))
        lines = _wrap(toks, 3, max_chars)
        text = "\\N".join(" ".join(toks[i] for i in line) for line in lines)
        end = min(clip_duration, hook.duration)
        tag = f"\\pos({width // 2},{round(hook.y * height)})\\fscx70\\fscy70\\t(0,140,\\fscx100\\fscy100)\\fad(0,220)"
        st = hook.style if hook.style in HOOK_STYLES else "box"
        events.append(f"Dialogue: 2,{_ass_time(0.0)},{_ass_time(end)},Hook_{st},,0,0,0,,{{{tag}}}{text}")
    if disclosure and disclosure.text.strip():
        margin_x = round(width * 0.06)
        top = round(height * 0.115)
        bottom = round(height * 0.74)
        x, y, an = {
            "top_left": (margin_x, top, 7),
            "top_right": (width - margin_x, top, 9),
            "bottom_left": (margin_x, bottom, 1),
            "bottom_right": (width - round(width * 0.17), bottom, 3),
        }.get(disclosure.corner, (margin_x, top, 7))
        tag = f"\\an{an}\\pos({x},{y})"
        events.append(f"Dialogue: 3,{_ass_time(0.0)},{_ass_time(clip_duration + 1)},Disc,,0,0,0,,{{{tag}}}{_escape(disclosure.text.strip())}")
    if progress is not None:
        events += progress_events(width, height, progress, clip_duration)
    return header + "\n".join(events) + "\n"


def progress_events(width: int, height: int, bar: ProgressBar, duration: float) -> list[str]:
    """A thin bar that fills left → right over the video (animated rectangular clip)."""
    bh = max(4, round(height * 0.0065))
    y = 0 if bar.position == "top" else height - bh
    x0 = round(width * max(0.0, min(1.0, bar.start)))
    x1 = round(width * max(0.0, min(1.0, bar.end)))
    shape = f"m 0 0 l {width} 0 {width} {bh} 0 {bh}"
    ms = max(1, round(duration * 1000))
    end = _ass_time(duration + 1)
    track = f"Dialogue: 4,{_ass_time(0.0)},{end},Bar,,0,0,0,,{{\\an7\\pos(0,{y})\\1c{ass_color('#000000')}\\1a&H99&\\p1}}{shape}{{\\p0}}"
    fill = (
        f"Dialogue: 5,{_ass_time(0.0)},{end},Bar,,0,0,0,,"
        f"{{\\an7\\pos(0,{y})\\1c{ass_color(bar.color)}\\clip(0,{y},{x0},{y + bh})\\t(0,{ms},\\clip(0,{y},{x1},{y + bh}))\\p1}}{shape}{{\\p0}}"
    )
    return [track, fill]
