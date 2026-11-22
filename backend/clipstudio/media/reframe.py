"""Speaker-aware reframing: face detection on sampled frames → smoothed crop path.

Pipeline (all CPU, one frame in memory at a time, ~50 MB RAM):
1. Sample frames at ``SAMPLE_FPS`` from the clip range with OpenCV, downscaled to ``DET_WIDTH``.
2. Detect faces with YuNet (230 KB ONNX model shipped in assets/).
3. Detect hard scene cuts from HSV histogram correlation between samples.
4. Choose a subject per sample (largest face, with continuity preference); a switch to a new
   subject must persist ``CONFIRM_SAMPLES`` samples unless there's a scene cut.
5. Per scene: fill gaps, median-filter, apply a dead-zone (camera holds still for small moves),
   then exponential smoothing — giving calm, intentional camera movement.
6. Interpolate to output frame rate and emit an FFmpeg ``sendcmd`` script for the crop filter.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import cv2
import numpy as np

from ..cancel import NEVER, CancelToken

log = logging.getLogger(__name__)

SAMPLE_FPS = 4.0
DET_WIDTH = 480
CONFIRM_SAMPLES = 3
CUT_THRESHOLD = 0.55  # histogram correlation below this = scene cut
MODEL = Path(__file__).resolve().parents[1] / "assets" / "face_detection_yunet_2023mar.onnx"


@dataclass
class Face:
    cx: float  # centre, normalised 0..1
    cy: float
    w: float  # size, normalised
    h: float
    score: float

    @property
    def area(self) -> float:
        return self.w * self.h


@dataclass
class FaceTrack:
    times: list[float] = field(default_factory=list)  # seconds relative to clip start
    faces: list[list[Face]] = field(default_factory=list)
    cuts: list[int] = field(default_factory=list)  # sample indices where a new scene starts

    @property
    def face_ratio(self) -> float:
        return sum(1 for f in self.faces if f) / max(1, len(self.faces))

    @property
    def two_person_ratio(self) -> float:
        """Fraction of samples with two clearly separated, similarly sized faces."""
        n = 0
        for fs in self.faces:
            big = sorted(fs, key=lambda f: f.area, reverse=True)[:2]
            if len(big) == 2 and big[1].area > 0.35 * big[0].area and abs(big[0].cx - big[1].cx) > 0.25:
                n += 1
        return n / max(1, len(self.faces))


def detect_faces(src: Path, start: float, end: float, *, cancel: CancelToken = NEVER) -> FaceTrack:
    cap = cv2.VideoCapture(str(src))
    if not cap.isOpened():
        raise RuntimeError("OpenCV could not open the video")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        step = max(1, round(fps / SAMPLE_FPS))
        cap.set(cv2.CAP_PROP_POS_MSEC, start * 1000.0)
        detector = None
        track = FaceTrack()
        prev_hist = None
        idx = 0
        while True:
            if idx % 16 == 0:
                cancel.check()
            ok = cap.grab()
            if not ok:
                break
            t = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            if t > end:
                break
            if idx % step:
                idx += 1
                continue
            idx += 1
            ok, frame = cap.retrieve()
            if not ok or frame is None:
                continue
            h0, w0 = frame.shape[:2]
            scale = DET_WIDTH / w0
            small = cv2.resize(frame, (DET_WIDTH, max(2, int(h0 * scale))), interpolation=cv2.INTER_AREA)
            sh, sw = small.shape[:2]
            if detector is None:
                detector = cv2.FaceDetectorYN.create(str(MODEL), "", (sw, sh), 0.7, 0.3, 5000)
            _, dets = detector.detect(small)
            faces = []
            if dets is not None:
                for d in dets:
                    x, y, w, h, score = float(d[0]), float(d[1]), float(d[2]), float(d[3]), float(d[-1])
                    if w * h < (sw * sh) * 0.0015:  # ignore tiny background faces
                        continue
                    faces.append(Face((x + w / 2) / sw, (y + h / 2) / sh, w / sw, h / sh, score))
            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [32, 32], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            if prev_hist is not None and cv2.compareHist(prev_hist, hist, cv2.HISTCMP_CORREL) < CUT_THRESHOLD:
                track.cuts.append(len(track.times))
            prev_hist = hist
            track.times.append(max(0.0, t - start))
            track.faces.append(faces)
        return track
    finally:
        cap.release()


def _pick_subjects(track: FaceTrack, rank: int = 0) -> list[Face | None]:
    """Choose one face per sample.

    ``rank`` 0 = main subject (largest face); 2 / 1 = left / right person for the split layout.
    """
    out: list[Face | None] = []
    current: Face | None = None
    pending: Face | None = None
    pending_n = 0
    cuts = set(track.cuts)
    for i, faces in enumerate(track.faces):
        if i in cuts:
            current, pending, pending_n = None, None, 0
        ordered = sorted(faces, key=lambda f: f.area, reverse=True)
        if rank == 1 and len(ordered) >= 2:
            # secondary subject = the other big face, ordered left→right for stable split
            ordered = sorted(ordered[:2], key=lambda f: f.cx)
            cand = ordered[1]
        elif rank == 1:
            cand = None
        elif rank == 2 and len(ordered) >= 2:
            # split-layout primary: the left of the two biggest faces
            cand = sorted(ordered[:2], key=lambda f: f.cx)[0]
        else:
            cand = ordered[0] if ordered else None
        if cand is None:
            out.append(current)
            continue
        if current is None:
            current = cand
        else:
            # continuity: the face nearest the current subject keeps the camera
            near = min(faces, key=lambda f: abs(f.cx - current.cx) + abs(f.cy - current.cy)) if faces else cand
            if abs(near.cx - current.cx) < 0.12 and (rank == 1 or near is cand or near.area > 0.6 * cand.area):
                current, pending, pending_n = near, None, 0
            else:
                # different subject: require it to persist before moving the camera
                if pending is not None and abs(pending.cx - cand.cx) < 0.12:
                    pending_n += 1
                else:
                    pending, pending_n = cand, 1
                if pending_n >= CONFIRM_SAMPLES:
                    current, pending, pending_n = cand, None, 0
        out.append(current)
    return out


def _smooth_axis(values: np.ndarray, deadzone: float, alpha: float) -> np.ndarray:
    """Median filter → dead-zone hold → exponential smoothing (forward + backward to avoid lag)."""
    n = len(values)
    if n == 0:
        return values
    v = values.copy()
    if n >= 5:
        pad = np.pad(v, 2, mode="edge")
        v = np.array([np.median(pad[i : i + 5]) for i in range(n)])
    held = np.empty(n)
    cam = v[0]
    for i in range(n):
        if abs(v[i] - cam) > deadzone:
            cam = v[i] - np.sign(v[i] - cam) * deadzone * 0.5
        held[i] = cam
    fwd = np.empty(n)
    acc = held[0]
    for i in range(n):
        acc = acc + alpha * (held[i] - acc)
        fwd[i] = acc
    bwd = np.empty(n)
    acc = fwd[-1]
    for i in range(n - 1, -1, -1):
        acc = acc + alpha * (fwd[i] - acc)
        bwd[i] = acc
    return bwd


@dataclass
class CropPlan:
    crop_w: int
    crop_h: int
    xs: np.ndarray  # per output frame
    ys: np.ndarray
    fps: float


def crop_size(src_w: int, src_h: int, aspect: float) -> tuple[int, int]:
    """Largest crop of ``aspect`` (w/h) that fits in the source. Even dimensions."""
    if src_w / src_h > aspect:
        ch = src_h
        cw = int(round(src_h * aspect))
    else:
        cw = src_w
        ch = int(round(src_w / aspect))
    return cw - cw % 2, ch - ch % 2


def plan_crop(
    track: FaceTrack | None,
    *,
    src_w: int,
    src_h: int,
    aspect: float,
    duration: float,
    out_fps: float,
    rank: int = 0,
    headroom: float = 0.08,
) -> CropPlan:
    cw, ch = crop_size(src_w, src_h, aspect)
    n_out = max(1, int(np.ceil(duration * out_fps)))
    centre_x = np.full(n_out, (src_w - cw) / 2)
    centre_y = np.full(n_out, (src_h - ch) / 2)
    if track is None or not track.times or track.face_ratio < 0.15:
        return CropPlan(cw, ch, centre_x, centre_y, out_fps)

    subjects = _pick_subjects(track, rank)
    times = np.asarray(track.times)
    tx = np.array([s.cx * src_w if s else np.nan for s in subjects])
    # Put eyes slightly above centre (faces sit in the upper-middle of a vertical frame).
    ty = np.array([(s.cy - headroom) * src_h if s else np.nan for s in subjects])

    seg_starts = sorted({0, *track.cuts})
    seg_bounds = list(zip(seg_starts, [*seg_starts[1:], len(times)]))
    xs = np.empty(len(times))
    ys = np.empty(len(times))
    for a, b in seg_bounds:
        for arr, src_arr, full, size in ((xs, tx, src_w, cw), (ys, ty, src_h, ch)):
            seg = src_arr[a:b].copy()
            if np.all(np.isnan(seg)):
                seg[:] = full / 2
            else:
                idx = np.arange(len(seg))
                good = ~np.isnan(seg)
                seg = np.interp(idx, idx[good], seg[good])
            sm = _smooth_axis(seg, deadzone=0.06 * full, alpha=0.25)
            arr[a:b] = np.clip(sm - size / 2, 0, full - size)

    t_out = np.arange(n_out) / out_fps
    # Hard cuts: step interpolation across the cut instead of a pan.
    x_out = np.interp(t_out, times, xs)
    y_out = np.interp(t_out, times, ys)
    for c in track.cuts:
        if 0 < c < len(times):
            mask = (t_out >= times[c - 1]) & (t_out < times[c])
            x_out[mask] = xs[c - 1]
            y_out[mask] = ys[c - 1]
    return CropPlan(cw, ch, np.round(x_out).astype(int), np.round(y_out).astype(int), out_fps)


def write_sendcmd(plan: CropPlan, path: Path, target: str = "crop") -> None:
    """Emit commands only when the crop position changes, to keep the script small."""
    lines: list[str] = []
    px = py = None
    for i, (x, y) in enumerate(zip(plan.xs, plan.ys)):
        if x == px and y == py:
            continue
        t = i / plan.fps
        lines.append(f"{t:.3f} {target} x {int(x)}, {target} y {int(y)};")
        px, py = x, y
    path.write_text("\n".join(lines) + "\n")
