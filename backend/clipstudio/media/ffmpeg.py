"""Safe FFmpeg/ffprobe invocation.

All commands are built as argument lists (never shell strings), so user input can't inject shell syntax.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..cancel import NEVER, CancelToken
from ..errors import Cancelled, MediaError

log = logging.getLogger(__name__)

FFMPEG = shutil.which("ffmpeg") or "ffmpeg"
FFPROBE = shutil.which("ffprobe") or "ffprobe"


def tools_available() -> dict[str, bool]:
    return {"ffmpeg": shutil.which("ffmpeg") is not None, "ffprobe": shutil.which("ffprobe") is not None}


@dataclass
class ProbeInfo:
    duration: float
    width: int
    height: int
    fps: float
    has_audio: bool
    video_codec: str
    size_bytes: int


def probe(path: Path) -> ProbeInfo:
    cmd = [FFPROBE, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=60, check=True).stdout
    except subprocess.CalledProcessError as e:
        raise MediaError(f"Not a readable video file ({e.stderr.strip()[:200]})") from e
    except subprocess.TimeoutExpired as e:
        raise MediaError("Timed out reading video metadata") from e
    data = json.loads(out or "{}")
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video" and not s.get("disposition", {}).get("attached_pic")), None)
    if video is None:
        raise MediaError("File has no video stream")
    audio = any(s.get("codec_type") == "audio" for s in streams)
    duration = float(data.get("format", {}).get("duration") or video.get("duration") or 0)
    if duration <= 0:
        raise MediaError("Could not determine video duration")
    num, _, den = (video.get("avg_frame_rate") or video.get("r_frame_rate") or "30/1").partition("/")
    fps = float(num) / float(den or 1) if float(den or 1) else 30.0
    w, h = int(video.get("width", 0)), int(video.get("height", 0))
    # Respect rotation metadata (phone videos)
    rot = 0
    for sd in video.get("side_data_list", []) or []:
        if "rotation" in sd:
            rot = abs(int(sd["rotation"]))
    if rot in (90, 270):
        w, h = h, w
    return ProbeInfo(
        duration=duration,
        width=w,
        height=h,
        fps=round(fps or 30.0, 3),
        has_audio=audio,
        video_codec=video.get("codec_name", "?"),
        size_bytes=int(data.get("format", {}).get("size") or path.stat().st_size),
    )


def run_ffmpeg(
    args: list[str],
    *,
    duration: float | None = None,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken = NEVER,
    timeout: float | None = None,
) -> None:
    """Run ffmpeg with machine-readable progress. ``args`` excludes the ffmpeg binary itself."""
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    log.debug("ffmpeg %s", " ".join(cmd))
    cancel.check()
    stderr_tail: list[str] = []
    # stderr to a temp file (not a pipe) so a chatty FFmpeg can never block on a full pipe buffer.
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=errf, text=True)
        cancel.attach(proc)
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if on_progress and duration and line.startswith("out_time_us="):
                    try:
                        us = int(line.split("=", 1)[1])
                        on_progress(max(0.0, min(1.0, us / 1e6 / duration)))
                    except ValueError:
                        pass
            proc.wait(timeout=timeout)
        finally:
            cancel.detach(proc)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        errf.seek(0)
        stderr_tail = errf.read().strip().splitlines()[-8:]
    if cancel.cancelled:
        raise Cancelled()
    if proc.returncode != 0:
        raise MediaError("FFmpeg failed: " + (" | ".join(stderr_tail) or f"exit code {proc.returncode}"))


def extract_audio(src: Path, dst: Path, *, duration: float, cancel: CancelToken = NEVER, on_progress=None) -> Path:
    """Decode to 16 kHz mono PCM WAV (what speech models expect). ~115 MB per hour."""
    tmp = dst.with_suffix(".part.wav")
    run_ffmpeg(
        ["-i", str(src), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", "-f", "wav", str(tmp)],
        duration=duration,
        on_progress=on_progress,
        cancel=cancel,
    )
    tmp.replace(dst)
    return dst


def extract_frame(src: Path, t: float, dst: Path, *, width: int = 480, vf_extra: list[str] | None = None) -> Path:
    vf = ",".join([*(vf_extra or []), f"scale={width}:-2"])
    run_ffmpeg(["-ss", f"{max(0.0, t):.3f}", "-i", str(src), "-frames:v", "1", "-vf", vf, "-q:v", "4", str(dst)], timeout=60)
    return dst
