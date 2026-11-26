"""Detect captions already burned into the source video, so they can be blurred.

Cropping a 16:9 video to 9:16 cuts burned-in subtitles in half, and they clash with our own
captions. We look for subtitle-like text in the lower part of the frame (works for light-on-dark
and dark-on-light text): strong edges → horizontal smearing → wide, short, dense blobs. A band is
reported only if text shows up consistently, with time intervals of when it is visible.
Pure OpenCV on small frames; no model download.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..cancel import NEVER, CancelToken

SAMPLE_FPS = 2.0
DET_WIDTH = 480
REGION = (0.50, 0.97)  # search the lower half (where subtitles live)
MIN_PRESENCE = 0.15


@dataclass
class TextBand:
    y0: float  # fractions of source height
    y1: float
    presence: float
    intervals: list[tuple[float, float]] = field(default_factory=list)  # clip-relative seconds


def frame_text_rows(small_bgr: np.ndarray) -> tuple[float, float] | None:
    """Vertical extent (fractions of frame height) of subtitle-like text in the lower region, or None.

    Text rows are rows with many short edge strokes (lots of on/off transitions) concentrated in the
    middle of the frame; box borders and smooth gradients have long runs and few transitions.
    """
    h, w = small_bgr.shape[:2]
    r0, r1 = int(h * REGION[0]), int(h * REGION[1])
    c0, c1 = int(w * 0.08), int(w * 0.92)
    gray = cv2.cvtColor(small_bgr[r0:r1, c0:c1], cv2.COLOR_BGR2GRAY)
    grad = cv2.morphologyEx(gray, cv2.MORPH_GRADIENT, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3)))
    _, bw = cv2.threshold(grad, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    if float(grad.max()) < 60:  # nothing with real contrast
        return None
    b = bw > 0
    if b.mean() > 0.35:  # busy texture everywhere (foliage, crowds): not reliable
        return None
    trans = np.count_nonzero(b[:, 1:] & ~b[:, :-1], axis=1)
    cw = c1 - c0
    is_text = trans >= max(6, int(cw * 0.035))
    rows = is_text  # runs below tolerate 1-2 row gaps (between ascenders/descenders)
    runs: list[tuple[int, int]] = []
    i = 0
    n = len(rows)
    while i < n:
        if rows[i]:
            j = i
            gap = 0
            k = i
            while k < n and gap <= 2:
                if rows[k]:
                    j = k
                    gap = 0
                else:
                    gap += 1
                k += 1
            runs.append((i, j + 1))
            i = k
        else:
            i += 1
    best: tuple[int, int] | None = None
    for a, z in runs:
        hh = z - a
        if hh < h * 0.025 or hh > h * 0.16:
            continue
        cols = np.nonzero(b[a:z].any(axis=0))[0]
        if len(cols) == 0:
            continue
        span = (cols[-1] - cols[0]) / cw
        centre = (cols[0] + cols[-1]) / 2 / cw
        if span < 0.15 or not 0.25 < centre < 0.75:
            continue
        if best is None or (z - a) > (best[1] - best[0]):
            best = (a, z)
    if best is None:
        return None
    return (best[0] + r0) / h, (best[1] + r0) / h


class TextCollector:
    """Accumulates per-sample detections (fed from any frame sampler) and summarises them."""

    def __init__(self) -> None:
        self.samples: list[tuple[float, tuple[float, float] | None]] = []

    def add(self, t: float, small_bgr: np.ndarray) -> None:
        self.samples.append((t, frame_text_rows(small_bgr)))

    def band(self, duration: float) -> TextBand | None:
        if not self.samples:
            return None
        hits = [(t, r) for t, r in self.samples if r]
        presence = len(hits) / len(self.samples)
        if presence < MIN_PRESENCE or len(hits) < 2:
            return None
        y0 = float(np.percentile([r[0] for _, r in hits], 10))
        y1 = float(np.percentile([r[1] for _, r in hits], 90))
        pad = (y1 - y0) * 0.25 + 0.01
        y0, y1 = max(0.0, y0 - pad), min(1.0, y1 + pad)
        if y1 - y0 > 0.3:  # implausibly tall: probably not subtitles
            return None
        step = 1.0 / SAMPLE_FPS
        intervals: list[tuple[float, float]] = []
        for t, r in self.samples:
            if not r:
                continue
            s, e = max(0.0, t - step * 0.75), min(duration, t + step * 0.75)
            if intervals and s - intervals[-1][1] <= step * 1.1:
                intervals[-1] = (intervals[-1][0], e)
            else:
                intervals.append((s, e))
        if len(intervals) > 40:  # keep the filter expression small: merge into coarser spans
            merged = [intervals[0]]
            for s, e in intervals[1:]:
                if s - merged[-1][1] < 2.0:
                    merged[-1] = (merged[-1][0], e)
                else:
                    merged.append((s, e))
            intervals = merged[:60]
        return TextBand(round(y0, 4), round(y1, 4), round(presence, 3), [(round(s, 2), round(e, 2)) for s, e in intervals])


def detect_burned_text(src: Path, start: float, end: float, *, cancel: CancelToken = NEVER) -> TextBand | None:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        return None
    col = TextCollector()
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / SAMPLE_FPS))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        idx = 0
        while True:
            if idx % 32 == 0:
                cancel.check()
            if not cap.grab():
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t > end:
                break
            take = idx % step == 0
            idx += 1
            if not take:
                continue
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            h0, w0 = frame.shape[:2]
            small = cv2.resize(frame, (DET_WIDTH, max(2, int(h0 * DET_WIDTH / w0))), interpolation=cv2.INTER_AREA)
            col.add(max(0.0, t - start), small)
    finally:
        cap.release()
    return col.band(end - start)


def blur_filter(band: TextBand, src_w: int, src_h: int, in_label: str, out_label: str) -> str:
    """Blur the band only while text is visible (enable expression on the overlay)."""
    by = int(band.y0 * src_h)
    bh = max(4, int((band.y1 - band.y0) * src_h))
    by -= by % 2
    bh -= bh % 2
    expr = "+".join(f"between(t,{s:.2f},{e:.2f})" for s, e in band.intervals) or "0"
    return (
        f"[{in_label}]split=2[bto][btc];"
        f"[btc]crop={src_w}:{bh}:0:{by},gblur=sigma=14,eq=brightness=-0.04[btb];"
        f"[bto][btb]overlay=0:{by}:enable='{expr}'[{out_label}]"
    )
