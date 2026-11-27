"""Keep on-screen text clear of the campaign logo.

Captions, the opening title text and the end text are placed where the style (or the campaign, or the
user) asks, then nudged up or down to the nearest spot where they don't sit on the logo. A position the
user set by hand on a clip is respected as it is; the compliance check then warns if the logo covers it.

All positions are fractions of the frame height (0 = top, 1 = bottom), centre of the text block.
"""

from __future__ import annotations

import math
from functools import lru_cache
from pathlib import Path

import numpy as np

from .safezones import GRID_H, GRID_W

Band = tuple[float, float]

# Text must stay inside this vertical range (platform UI covers the very top and bottom).
TOP, BOTTOM = 0.08, 0.90
GAP = 0.015  # breathing room between text and logo
HOOK_SIZE = 0.042  # title text font size, fraction of height (media/overlays.py)


def caption_height(style) -> float:
    """Height of a full caption block (all lines) as a fraction of the frame."""
    pad = 0.02 if getattr(style, "background", False) else 0.0
    return style.max_lines * style.size * 1.2 + 2 * style.outline + pad


def title_height(text: str) -> float:
    """Height of the boxed title text: up to 3 lines of about 21 characters (overlays.py wraps the same way)."""
    lines = max(1, min(3, math.ceil(len((text or "").strip()) / 21)))
    return lines * HOOK_SIZE * 1.2 + 0.03


@lru_cache(maxsize=64)
def _logo_mask(file: str, mtime: float, mode: str, x: float, y: float, width: float) -> np.ndarray | None:
    from ..media.logo import content_mask

    try:
        return content_mask({"file": file, "mode": mode, "x": x, "y": y, "width": width}, GRID_W, GRID_H)
    except Exception:
        return None


def logo_mask(logo: dict | None) -> np.ndarray | None:
    """Visible logo pixels on the safe-zone grid (cached per file and placement)."""
    if not logo or not logo.get("file") or not Path(logo["file"]).exists():
        return None
    p = Path(logo["file"])
    return _logo_mask(str(p), p.stat().st_mtime, logo.get("mode", "free"), float(logo.get("x", 0.5)), float(logo.get("y", 0.22)), float(logo.get("width", 0.4)))


def blocked_rows(mask: np.ndarray | None, x0: float, x1: float) -> list[Band]:
    """Vertical runs where the logo has pixels between x0 and x1 (text is centred, so only that span matters)."""
    if mask is None or not mask.any():
        return []
    cols = mask[:, int(round(x0 * GRID_W)) : int(round(x1 * GRID_W))]
    rows = cols.any(axis=1)
    runs: list[Band] = []
    start = None
    for i, on in enumerate(rows):
        if on and start is None:
            start = i
        elif not on and start is not None:
            runs.append((start / GRID_H, i / GRID_H))
            start = None
    if start is not None:
        runs.append((start / GRID_H, 1.0))
    return runs


def overlap(center: float, height: float, blocks: list[Band]) -> float:
    """Fraction of the text block's height that lies on a blocked band."""
    a, b = center - height / 2, center + height / 2
    covered = sum(max(0.0, min(b, y1) - max(a, y0)) for y0, y1 in blocks)
    return min(1.0, covered / height) if height > 0 else 0.0


def place(center: float, height: float, blocks: list[Band]) -> tuple[float, bool]:
    """Nearest centre to ``center`` where the block clears every band (with a small gap). Returns
    (centre, moved). When nothing fits, the original centre is kept (and the check reports it)."""
    padded = [(y0 - GAP, y1 + GAP) for y0, y1 in blocks]
    lo, hi = TOP + height / 2, BOTTOM - height / 2
    if overlap(center, height, padded) == 0:
        return center, False
    best = None
    for step in range(1, 200):
        for c in (center - step * 0.005, center + step * 0.005):
            if lo <= c <= hi and overlap(c, height, padded) == 0:
                if best is None or abs(c - center) < abs(best - center):
                    best = c
        if best is not None:
            break
    return (round(best, 3), True) if best is not None else (center, False)


def layout(*, logo: dict | None, caption_style, caption_set_by_clip: bool, captions_on: bool, hook_text: str | None, end_text: str | None = None, end_y: float = 0.45) -> dict:
    """Effective vertical positions for captions, title text and end text around the logo."""
    mask = logo_mask(logo)
    cap_blocks = blocked_rows(mask, 0.2, 0.8)
    txt_blocks = blocked_rows(mask, 0.1, 0.9)
    out: dict = {"logo_band": None, "caption_moved": False, "hook_moved": False, "end_moved": False}
    if cap_blocks or txt_blocks:
        rows = cap_blocks + txt_blocks
        out["logo_band"] = [round(min(r[0] for r in rows), 3), round(max(r[1] for r in rows), 3)]

    cap_h = caption_height(caption_style)
    cap_y = float(caption_style.position)
    if captions_on and cap_blocks and not caption_set_by_clip:
        cap_y, out["caption_moved"] = place(cap_y, cap_h, cap_blocks)
    out["caption_position"] = cap_y
    out["caption_logo_overlap"] = round(overlap(cap_y, cap_h, cap_blocks), 3) if captions_on else 0.0
    cap_band = [(cap_y - cap_h / 2, cap_y + cap_h / 2)] if captions_on else []

    hook_y = 0.2
    if hook_text:
        hook_y, out["hook_moved"] = place(hook_y, title_height(hook_text), txt_blocks + cap_band)
    out["hook_y"] = hook_y

    if end_text:
        ey, out["end_moved"] = place(end_y, title_height(end_text), txt_blocks + cap_band)
        out["end_y"] = ey
    return out
