"""Instagram Reels / YouTube Shorts safe zones for 9:16 video (A10).

Rectangles are fractions of the frame (x0, y0, x1, y1). Two kinds:
- ``ui``:   areas the app's interface usually covers (header, caption/username block, action buttons).
- ``nogo``: the core of those areas where important content (logos, hook text) must not go.

These are approximations of the current app layouts (they vary by device and app version). A
campaign's own safe-zone template, if supplied, is shown in the editor as the authority.
"""

from __future__ import annotations

import numpy as np

Rect = tuple[float, float, float, float]

ZONES: dict[str, dict[str, list[Rect]]] = {
    "reels": {
        "ui": [(0.0, 0.0, 1.0, 0.10), (0.0, 0.78, 1.0, 1.0), (0.86, 0.42, 1.0, 0.90)],
        "nogo": [(0.0, 0.0, 1.0, 0.06), (0.0, 0.84, 1.0, 1.0), (0.89, 0.50, 1.0, 0.88)],
    },
    "shorts": {
        "ui": [(0.0, 0.0, 1.0, 0.09), (0.0, 0.80, 1.0, 1.0), (0.85, 0.45, 1.0, 0.92)],
        "nogo": [(0.0, 0.0, 1.0, 0.05), (0.0, 0.86, 1.0, 1.0), (0.88, 0.52, 1.0, 0.90)],
    },
}

LABELS = {"reels": "Instagram Reels", "shorts": "YouTube Shorts"}

GRID_W, GRID_H = 108, 192  # 1/10 of 1080x1920


def zone_mask(platforms: list[str], kind: str) -> np.ndarray:
    """Boolean mask (GRID_H x GRID_W) of the union of zones for the given platforms."""
    m = np.zeros((GRID_H, GRID_W), dtype=bool)
    for p in platforms:
        for x0, y0, x1, y1 in ZONES.get(p, {}).get(kind, []):
            m[int(round(y0 * GRID_H)) : int(round(y1 * GRID_H)), int(round(x0 * GRID_W)) : int(round(x1 * GRID_W))] = True
    return m


def rect_mask(rect: Rect) -> np.ndarray:
    m = np.zeros((GRID_H, GRID_W), dtype=bool)
    x0, y0, x1, y1 = rect
    m[max(0, int(y0 * GRID_H)) : min(GRID_H, int(np.ceil(y1 * GRID_H))), max(0, int(x0 * GRID_W)) : min(GRID_W, int(np.ceil(x1 * GRID_W)))] = True
    return m


def overlap_fraction(content: np.ndarray, platforms: list[str], kind: str) -> float:
    """Fraction of ``content`` (bool mask on the grid) that falls inside the zones."""
    total = int(content.sum())
    if total == 0:
        return 0.0
    return float((content & zone_mask(platforms, kind)).sum()) / total


def safe_box(platforms: list[str]) -> Rect:
    """The largest centred box free of all UI zones (for placing text)."""
    top = max((r[3] for p in platforms for r in ZONES[p]["ui"] if r[1] == 0.0 and r[2] == 1.0), default=0.1)
    bottom = min((r[1] for p in platforms for r in ZONES[p]["ui"] if r[3] == 1.0 and r[2] == 1.0), default=0.8)
    right = min((r[0] for p in platforms for r in ZONES[p]["ui"] if r[0] > 0.5), default=0.86)
    return (1.0 - right, top, right, bottom)


def public_zones() -> dict:
    return {p: {"label": LABELS[p], **z} for p, z in ZONES.items()}
