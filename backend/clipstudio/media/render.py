"""Clip rendering with FFmpeg.

Every auxiliary file (crop command scripts, ASS subtitles) is written to a per-render temp dir and
FFmpeg runs with that dir as its working directory, so the filtergraph only references simple
relative names. This avoids path-escaping bugs (spaces/colons in external-drive paths) entirely.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from ..cancel import NEVER, CancelToken
from ..errors import Cancelled, MediaError
from . import ffmpeg as ff
from .captions import CapWord, CaptionStyle, build_ass
from .reframe import FaceTrack, crop_size, detect_faces, plan_crop, write_sendcmd

log = logging.getLogger(__name__)

ASPECTS = {"9:16": 9 / 16, "1:1": 1.0, "16:9": 16 / 9}


@dataclass
class RenderSpec:
    src: Path
    start: float
    end: float
    src_w: int
    src_h: int
    src_fps: float
    has_audio: bool
    aspect: str = "9:16"
    height: int = 720
    layout: str = "auto"  # auto | center | blur | split | track
    words: list[CapWord] = field(default_factory=list)  # times relative to clip start
    style: CaptionStyle | None = None
    captions: bool = True
    normalise_audio: bool = True
    max_fps: float = 30.0
    encoder: str = "videotoolbox"  # videotoolbox | x264
    x264_preset: str = "veryfast"
    music: Path | None = None
    music_volume: float = 0.12

    @property
    def duration(self) -> float:
        return self.end - self.start

    def out_size(self) -> tuple[int, int]:
        """``height`` is the quality class (720/1080) = the *short* side, like phone video:
        9:16@720 → 720x1280, 1:1@720 → 720x720, 16:9@720 → 1280x720."""
        a = ASPECTS[self.aspect]
        short = self.height
        if a <= 1:
            w, h = short, int(round(short / a))
        else:
            w, h = int(round(short * a)), short
        return w - w % 2, h - h % 2


@dataclass
class RenderResult:
    path: Path
    layout_used: str
    face_ratio: float


def _encoder_args(spec: RenderSpec, w: int, h: int) -> list[str]:
    if spec.encoder == "x264":
        return ["-c:v", "libx264", "-preset", spec.x264_preset, "-crf", "21", "-pix_fmt", "yuv420p"]
    kbps = 5000 if h * w <= 1280 * 720 else 9000
    return ["-c:v", "h264_videotoolbox", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-allow_sw", "1", "-pix_fmt", "yuv420p"]


def choose_layout(spec: RenderSpec, track: FaceTrack | None) -> str:
    if spec.layout != "auto":
        return spec.layout
    if ASPECTS[spec.aspect] >= spec.src_w / spec.src_h - 0.01:
        return "center"  # target is as wide as the source: nothing to track
    if track is None:
        return "blur"
    if track.face_ratio >= 0.3:
        return "track"
    return "blur"  # screen recordings / gameplay without faces: keep the whole frame


def build_video_graph(spec: RenderSpec, layout: str, track: FaceTrack | None, workdir: Path) -> str:
    w, h = spec.out_size()
    fps = min(spec.src_fps or 30.0, spec.max_fps)
    aspect = ASPECTS[spec.aspect]
    chains: list[str] = []
    if layout in ("track", "center"):
        plan = plan_crop(track if layout == "track" else None, src_w=spec.src_w, src_h=spec.src_h, aspect=aspect, duration=spec.duration, out_fps=fps)
        write_sendcmd(plan, workdir / "crop.cmd")
        chains.append(
            f"[0:v]fps={fps:g},sendcmd=f=crop.cmd,crop@crop=w={plan.crop_w}:h={plan.crop_h}:x={int(plan.xs[0])}:y={int(plan.ys[0])},"
            f"scale={w}:{h}:flags=lanczos,setsar=1[vb]"
        )
    elif layout == "split":
        half_h = h // 2 - (h // 2) % 2
        half_aspect = w / half_h
        top = plan_crop(track, src_w=spec.src_w, src_h=spec.src_h, aspect=half_aspect, duration=spec.duration, out_fps=fps, rank=2, headroom=0.02)
        bot = plan_crop(track, src_w=spec.src_w, src_h=spec.src_h, aspect=half_aspect, duration=spec.duration, out_fps=fps, rank=1, headroom=0.02)
        write_sendcmd(top, workdir / "top.cmd", target="crop@top")
        write_sendcmd(bot, workdir / "bot.cmd", target="crop@bot")
        chains.append(
            f"[0:v]fps={fps:g},split=2[s1][s2];"
            f"[s1]sendcmd=f=top.cmd,crop@top=w={top.crop_w}:h={top.crop_h}:x={int(top.xs[0])}:y={int(top.ys[0])},scale={w}:{half_h},setsar=1[t];"
            f"[s2]sendcmd=f=bot.cmd,crop@bot=w={bot.crop_w}:h={bot.crop_h}:x={int(bot.xs[0])}:y={int(bot.ys[0])},scale={w}:{h - half_h},setsar=1[b];"
            f"[t][b]vstack=inputs=2[vb]"
        )
    else:  # blur
        chains.append(
            f"[0:v]fps={fps:g},split=2[a][b];"
            f"[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},boxblur=8:2,scale={w}:{h},eq=brightness=-0.08[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vb]"
        )
    if spec.captions and spec.words and spec.style is not None:
        (workdir / "subs.ass").write_text(build_ass(spec.words, spec.style, width=w, height=h), encoding="utf-8")
        chains.append("[vb]ass=subs.ass[v]")
    else:
        chains.append("[vb]null[v]")
    return ";".join(chains)


def build_audio_graph(spec: RenderSpec) -> str | None:
    if not spec.has_audio:
        return None
    a = "[0:a]aresample=48000"
    if spec.normalise_audio:
        a += ",loudnorm=I=-14:TP=-1.5:LRA=11"
    if spec.music:
        fade_out = max(0.0, spec.duration - 1.5)
        return (
            f"{a}[voice];[1:a]aresample=48000,volume={spec.music_volume:.3f},afade=t=in:d=1,afade=t=out:st={fade_out:.2f}:d=1.5[m];"
            f"[voice][m]amix=inputs=2:duration=first:normalize=0,alimiter=limit=0.95[a]"
        )
    return a + ",alimiter=limit=0.95[a]"


def render_clip(
    spec: RenderSpec,
    out_path: Path,
    *,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken = NEVER,
    face_track: bool = True,
) -> RenderResult:
    if spec.duration <= 0.5:
        raise MediaError("Clip is too short to render")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="render_", dir=out_path.parent))
    try:
        track: FaceTrack | None = None
        needs_faces = spec.layout in ("auto", "track", "split") and face_track
        if needs_faces and ASPECTS[spec.aspect] < spec.src_w / spec.src_h - 0.01:
            try:
                track = detect_faces(spec.src, spec.start, spec.end, cancel=cancel)
            except Cancelled:
                raise
            except Exception as e:  # never fail a render because face detection failed
                log.warning("Face detection failed, falling back: %s", e)
        if on_progress:
            on_progress(0.15)
        layout = choose_layout(spec, track)
        if layout == "split" and (track is None or track.two_person_ratio < 0.2):
            layout = "track" if track and track.face_ratio >= 0.3 else "blur"
        if layout == "track" and (track is None or track.face_ratio < 0.15):
            layout = "center"
        graph = build_video_graph(spec, layout, track, workdir)
        agraph = build_audio_graph(spec)
        if agraph:
            graph += ";" + agraph
        (workdir / "graph.txt").write_text(graph)
        w, h = spec.out_size()
        part = out_path.with_name(out_path.stem + ".part.mp4")
        args = ["-ss", f"{spec.start:.3f}", "-t", f"{spec.duration:.3f}", "-i", str(spec.src)]
        if spec.music:
            args += ["-stream_loop", "-1", "-i", str(spec.music)]
        args += ["-/filter_complex", "graph.txt", "-map", "[v]"]
        if agraph:
            args += ["-map", "[a]", "-c:a", "aac", "-b:a", "160k"]
        args += [*_encoder_args(spec, w, h), "-movflags", "+faststart", "-t", f"{spec.duration:.3f}", str(part)]

        def prog(p: float) -> None:
            if on_progress:
                on_progress(0.15 + 0.85 * p)

        _run_in(workdir, args, spec.duration, prog, cancel)
        os.replace(part, out_path)  # atomic: a finished file is either complete or absent
        return RenderResult(out_path, layout, track.face_ratio if track else 0.0)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        part = out_path.with_name(out_path.stem + ".part.mp4")
        if part.exists():
            part.unlink(missing_ok=True)


def _run_in(cwd: Path, args: list[str], duration: float, on_progress, cancel: CancelToken) -> None:
    # ff.run_ffmpeg doesn't take cwd; run a scoped subprocess with identical semantics instead.
    cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    cancel.check()
    proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    cancel.attach(proc)
    try:
        assert proc.stdout is not None
        for line in proc.stdout:
            if line.startswith("out_time_us="):
                try:
                    on_progress(max(0.0, min(1.0, int(line.split("=", 1)[1]) / 1e6 / duration)))
                except ValueError:
                    pass
        proc.wait()
        err = proc.stderr.read() if proc.stderr else ""
    finally:
        cancel.detach(proc)
        if proc.poll() is None:
            proc.kill()
    if cancel.cancelled:
        raise Cancelled()
    if proc.returncode != 0:
        tail = " | ".join(err.strip().splitlines()[-6:])
        raise MediaError(f"Render failed: {tail or proc.returncode}")


def render_preview_frame(spec: RenderSpec, t: float, out_jpg: Path) -> Path:
    """Single frame at clip-relative time ``t`` with the exact caption styling (for the editor)."""
    workdir = Path(tempfile.mkdtemp(prefix="preview_", dir=out_jpg.parent))
    try:
        layout = spec.layout if spec.layout in ("center", "blur") else "center"
        sub = RenderSpec(**{**spec.__dict__, "start": spec.start + t, "end": spec.start + t + 1.0})
        sub.words = [CapWord(w.w, w.s - t, w.e - t) for w in spec.words if w.e > t]
        graph = build_video_graph(sub, layout, None, workdir)
        (workdir / "graph.txt").write_text(graph)
        tmp = out_jpg.with_suffix(".part.jpg")
        _run_in(workdir, ["-ss", f"{spec.start + t:.3f}", "-i", str(spec.src), "-/filter_complex", "graph.txt", "-map", "[v]", "-frames:v", "1", "-q:v", "3", "-update", "1", str(tmp)], 1.0, lambda p: None, NEVER)
        os.replace(tmp, out_jpg)
        return out_jpg
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def make_thumbnail(video: Path, out_jpg: Path, t: float = 1.0) -> Path:
    tmp = out_jpg.with_suffix(".part.jpg")
    ff.run_ffmpeg(["-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "5", "-update", "1", str(tmp)], timeout=60)
    os.replace(tmp, out_jpg)
    return out_jpg


__all__ = ["RenderSpec", "RenderResult", "render_clip", "render_preview_frame", "make_thumbnail", "crop_size"]
