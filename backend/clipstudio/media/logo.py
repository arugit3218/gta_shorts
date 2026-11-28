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

VIDEO_EXTS = {".mp4", ".mov", ".m4v", ".webm"}
LOGO_EXTS = {".png", ".gif", ".apng"} | VIDEO_EXTS  # PNG may be an animated APNG
SAMPLE_FPS, MAX_SAMPLES = 2, 20  # video logos are judged on their first 10 seconds
GREEN_MIN_SHARE = 0.12  # a green screen fills at least this much of the frame
KEY_SIMILARITY, KEY_BLEND = 0.22, 0.08

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


def is_video(path: Path | str) -> bool:
    return Path(path).suffix.lower() in VIDEO_EXTS


def decoder_args(path: Path | str) -> list[str]:
    """FFmpeg's built-in VP8/VP9 decoders drop transparency; the libvpx ones keep it."""
    if Path(path).suffix.lower() != ".webm":
        return []
    cmd = [ff.FFPROBE, "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(path)]
    codec = subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout.strip()
    lib = {"vp8": "libvpx", "vp9": "libvpx-vp9"}.get(codec)
    return ["-c:v", lib] if lib else []


def key_filter(key: str | None) -> str:
    """Filter step (to append after scaling) that makes the green-screen colour ``key`` transparent."""
    return f",format=rgba,colorkey={key}:{KEY_SIMILARITY}:{KEY_BLEND}" if key else ""


def detect_green_screen(frames: np.ndarray) -> str | None:
    """The flat green-screen colour ("0xRRGGBB") filling part of these RGBA frames, or None."""
    rgb = frames[..., :3].reshape(-1, 3).astype(np.int32)
    r, g, b = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    green = rgb[(g > 110) & (g * 10 > r * 14) & (g * 10 > b * 14)]
    if len(green) < GREEN_MIN_SHARE * len(rgb):
        return None
    colour = np.median(green, axis=0)
    flat = int((np.linalg.norm(green - colour, axis=1) < 30).sum())
    # one even colour, not just greenery in a picture
    if flat < GREEN_MIN_SHARE * len(rgb) or flat < 0.6 * len(green):
        return None
    return "0x" + "".join(f"{int(round(c)):02X}" for c in colour)


def crop_filter(crop: list[int] | tuple[int, ...] | None) -> str:
    """Filter step (to put before scaling) keeping only the ``[x, y, w, h]`` part of the source."""
    return f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""


def visible_box(path: Path, w: int, h: int, key: str | None = None) -> list[int] | None:
    """[x, y, w, h] of the part of a w x h video logo that ever shows something, or None for (nearly) all of it."""
    gw, gh = max(2, w // 2), max(2, h // 2)
    shown = sample_rgba(path, gw, gh, key)[..., 3].max(axis=0) > 24
    if not shown.any():
        return None
    rows, cols = np.where(shown.any(axis=1))[0], np.where(shown.any(axis=0))[0]
    # outwards to even source pixels
    x0, x1 = int(cols[0] * w / gw) // 2 * 2, min(w // 2 * 2, -int(-(cols[-1] + 1) * w // gw // 2) * 2)
    y0, y1 = int(rows[0] * h / gh) // 2 * 2, min(h // 2 * 2, -int(-(rows[-1] + 1) * h // gh // 2) * 2)
    if (x1 - x0) * (y1 - y0) >= 0.96 * w * h:
        return None
    return [x0, y0, x1 - x0, y1 - y0]


def sample_rgba(path: Path, w: int, h: int, key: str | None = None, crop: list[int] | None = None) -> np.ndarray:
    """Frames of a video logo, sampled over its start, as (n, h, w, 4)."""
    cmd = [ff.FFMPEG, "-v", "error", *decoder_args(path), "-i", str(path), "-vf", f"{crop_filter(crop)}fps={SAMPLE_FPS},scale={w}:{h}:flags=area,format=rgba{key_filter(key)}", "-frames:v", str(MAX_SAMPLES), "-f", "rawvideo", "-pix_fmt", "rgba", "pipe:1"]
    try:
        raw = subprocess.run(cmd, capture_output=True, timeout=120, check=True).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise MediaError("Couldn't read the logo video") from e
    n = len(raw) // (w * h * 4)
    if not n:
        raise MediaError("Couldn't read the logo video")
    return np.frombuffer(raw[: n * w * h * 4], dtype=np.uint8).reshape(n, h, w, 4)


def video_preview(path: Path, out_png: Path) -> tuple[int, int, bool, str | None, list[int] | None]:
    """Save a still of a video logo (browsers can't show every format).

    Returns (width, height, transparent, green-screen colour to remove, visible area). Only videos without
    their own transparency are checked for a green screen. When only part of the frame shows anything, a
    second still of just that part is saved next to ``out_png`` as ``*_crop.png``.
    """
    frames = sample_rgba(path, 108, 192)
    key = None
    if not (frames[..., 3] < 250).any():
        key = detect_green_screen(frames)
        if key:
            frames = sample_rgba(path, 108, 192, key)
    alpha = frames[..., 3]
    transparent = bool((alpha < 250).any())
    shown = (alpha > 24).sum(axis=(1, 2))
    # the frame showing the most of the logo; for an opaque video, one from the middle (skips fade-ins)
    best = int(shown.argmax()) if transparent else len(shown) // 2
    cmd = [ff.FFMPEG, "-v", "error", "-y", *decoder_args(path), "-i", str(path), "-vf", f"fps={SAMPLE_FPS},select=eq(n\\,{best}),format=rgba{key_filter(key)}", "-frames:v", "1", "-update", "1", str(out_png)]
    try:
        subprocess.run(cmd, capture_output=True, timeout=120, check=True)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        raise MediaError("Couldn't read the logo video") from e
    still = inspect_logo(out_png)
    crop = visible_box(path, still.width, still.height, key) if transparent else None
    if crop:
        part = out_png.with_name(out_png.stem + "_crop.png")
        cmd = [ff.FFMPEG, "-v", "error", "-y", "-i", str(out_png), "-vf", crop_filter(crop) + "format=rgba", "-frames:v", "1", "-update", "1", str(part)]
        subprocess.run(cmd, capture_output=True, timeout=60, check=True)
    return still.width, still.height, transparent, key, crop


def visible_alpha(path: Path, w: int, h: int, key: str | None = None, crop: list[int] | None = None) -> np.ndarray:
    """Alpha (h, w) of what the logo shows: any sampled frame for a video, else the first frame."""
    if is_video(path):
        return sample_rgba(path, w, h, key, crop)[..., 3].max(axis=0)
    return first_frame_rgba(path, w, h)[..., 3]


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
        a = visible_alpha(path, grid_w, grid_h, logo.get("key_color"))
        return a > 24
    lw, lh, x, y = free_placement(logo, grid_w * 10, grid_h * 10)
    gw, gh = max(1, round(lw / 10)), max(1, round(lh / 10))
    a = visible_alpha(path, gw, gh, logo.get("key_color"), logo.get("crop")) > 24
    m = np.zeros((grid_h, grid_w), dtype=bool)
    gx, gy = round(x / 10), round(y / 10)
    sub = m[gy : gy + gh, gx : gx + gw]
    sub |= a[: sub.shape[0], : sub.shape[1]]
    return m


def logo_input_args(logo: dict, fps: float) -> list[str]:
    """FFmpeg input options so the logo lasts (and loops) for the whole clip."""
    path = logo["file"]
    ext = Path(path).suffix.lower()
    if ext in VIDEO_EXTS:  # only the picture is used; the logo video's sound is ignored
        return ["-stream_loop", "-1", *decoder_args(path), "-i", path]
    if logo.get("animated"):
        # GIF/APNG demuxers loop natively with ignore_loop=0 (-stream_loop hangs on APNG).
        fmt = ["-f", "apng"] if ext in (".apng", ".png") else []
        return ["-ignore_loop", "0", *fmt, "-i", path]
    return ["-loop", "1", "-framerate", f"{fps:g}", "-i", path]


def logo_filter(logo: dict, idx: int, out_w: int, out_h: int, fps: float, src_label: str, out_label: str) -> str:
    """Filter chain overlaying input ``idx`` onto ``src_label``."""
    alpha = logo.get("opacity", 1.0)
    fade = f",colorchannelmixer=aa={alpha:.3f}" if alpha < 0.999 else ""
    key = key_filter(logo.get("key_color"))
    if logo.get("mode") == "template":
        prep = f"[{idx}:v]fps={fps:g},scale={out_w}:{out_h}:flags=lanczos{key},format=rgba{fade}[lg]"
        pos = "0:0"
    else:
        lw, lh, x, y = free_placement(logo, out_w, out_h)
        prep = f"[{idx}:v]{crop_filter(logo.get("crop"))}fps={fps:g},scale={lw}:{lh}:flags=lanczos{key},format=rgba{fade}[lg]"
        pos = f"{x}:{y}"
    return f"{prep};[{src_label}][lg]overlay={pos}:eof_action=repeat:shortest=0:format=auto[{out_label}]"
