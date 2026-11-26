"""Logo / brand overlays (A3).

Two modes:
- ``template``: a full-frame transparent file (PNG/GIF/APNG/WebP, typically 1080x1920) laid over the
  whole video exactly as supplied: scaled to the output frame, never moved, cropped or resized
  relative to it. Animated templates loop for the full clip.
- ``free``: a logo image scaled to a fraction of the output width and centred at (x, y), kept
  inside the frame. Zone rules ("top half") are enforced by the compliance checker and the UI.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ..errors import MediaError
from . import ffmpeg as ff

LOGO_EXTS = {".png", ".gif", ".apng"}  # PNG may be an animated APNG

ZONE_RECTS: dict[str, tuple[float, float, float, float]] = {
    "any": (0.0, 0.0, 1.0, 1.0),
    "top_half": (0.0, 0.0, 1.0, 0.5),
    "bottom_half": (0.0, 0.5, 1.0, 1.0),
    "top": (0.0, 0.0, 1.0, 0.34),
    "middle": (0.0, 0.33, 1.0, 0.67),
    "bottom": (0.0, 0.66, 1.0, 1.0),
    "top_left": (0.0, 0.0, 0.5, 0.5),
    "top_right": (0.5, 0.0, 1.0, 0.5),
    "bottom_left": (0.0, 0.5, 0.5, 1.0),
    "bottom_right": (0.5, 0.5, 1.0, 1.0),
}


@dataclass
class LogoInfo:
    width: int
    height: int
    animated: bool
    codec: str


def inspect_logo(path: Path) -> LogoInfo:
    cmd = [ff.FFPROBE, "-v", "error", "-print_format", "json", "-show_streams", "-count_packets", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise MediaError("Not a readable image file") from e
    streams = json.loads(out or "{}").get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not v:
        raise MediaError("Not a readable image file")
    frames = int(v.get("nb_read_packets") or v.get("nb_frames") or 1)
    return LogoInfo(int(v.get("width", 0)), int(v.get("height", 0)), frames > 1, v.get("codec_name", "?"))


def first_frame_rgba(path: Path, w: int, h: int) -> np.ndarray:
    """First frame scaled to w x h as an RGBA array (h, w, 4)."""
    cmd = [ff.FFMPEG, "-v", "error", "-i", str(path), "-frames:v", "1", "-vf", f"scale={w}:{h}:flags=area,format=rgba", "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=60, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise MediaError("Couldn't read the logo image") from e
    if len(raw) < w * h * 4:
        raise MediaError("Couldn't read the logo image")
    return np.frombuffer(raw[: w * h * 4], dtype=np.uint8).reshape(h, w, 4)


def free_placement(logo: dict, out_w: int, out_h: int) -> tuple[int, int, int, int]:
    """(width, height, x, y) in output pixels for a freely placed logo, kept inside the frame."""
    iw, ih = logo.get("image_w") or 1, logo.get("image_h") or 1
    lw = max(2, int(round(logo.get("width", 0.4) * out_w)))
    lh = max(2, int(round(lw * ih / iw)))
    if lh > out_h:
        lh = out_h
        lw = max(2, int(round(lh * iw / ih)))
    lw -= lw % 2
    lh -= lh % 2
    x = int(round(logo.get("x", 0.5) * out_w - lw / 2))
    y = int(round(logo.get("y", 0.22) * out_h - lh / 2))
    return lw, lh, max(0, min(out_w - lw, x)), max(0, min(out_h - lh, y))


def content_mask(logo: dict, grid_w: int, grid_h: int) -> np.ndarray:
    """Where the logo's visible (non-transparent) pixels land, as a bool mask on a grid of the frame."""
    path = Path(logo["file"])
    if logo.get("mode") == "template":
        a = first_frame_rgba(path, grid_w, grid_h)[..., 3]
        return a > 24
    lw, lh, x, y = free_placement(logo, grid_w * 10, grid_h * 10)
    gw, gh = max(1, round(lw / 10)), max(1, round(lh / 10))
    a = first_frame_rgba(path, gw, gh)[..., 3] > 24
    m = np.zeros((grid_h, grid_w), dtype=bool)
    gx, gy = round(x / 10), round(y / 10)
    sub = m[gy : gy + gh, gx : gx + gw]
    sub |= a[: sub.shape[0], : sub.shape[1]]
    return m


def logo_input_args(logo: dict, fps: float) -> list[str]:
    """FFmpeg input options so the logo lasts (and loops) for the whole clip."""
    path = logo["file"]
    ext = Path(path).suffix.lower()
    if logo.get("animated"):
        # GIF/APNG demuxers loop natively with ignore_loop=0 (-stream_loop hangs on APNG).
        fmt = ["-f", "apng"] if ext in (".apng", ".png") else []
        return ["-ignore_loop", "0", *fmt, "-i", path]
    return ["-loop", "1", "-framerate", f"{fps:g}", "-i", path]


def logo_filter(logo: dict, idx: int, out_w: int, out_h: int, fps: float, src_label: str, out_label: str) -> str:
    """Filter chain overlaying input ``idx`` onto ``src_label``."""
    alpha = logo.get("opacity", 1.0)
    fade = f",colorchannelmixer=aa={alpha:.3f}" if alpha < 0.999 else ""
    if logo.get("mode") == "template":
        prep = f"[{idx}:v]fps={fps:g},scale={out_w}:{out_h}:flags=lanczos,format=rgba{fade}[lg]"
        pos = "0:0"
    else:
        lw, lh, x, y = free_placement(logo, out_w, out_h)
        prep = f"[{idx}:v]fps={fps:g},scale={lw}:{lh}:flags=lanczos,format=rgba{fade}[lg]"
        pos = f"{x}:{y}"
    return f"{prep};[{src_label}][lg]overlay={pos}:eof_action=repeat:shortest=0:format=auto[{out_label}]"
