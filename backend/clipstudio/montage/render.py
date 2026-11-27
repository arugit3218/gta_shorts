"""Render a montage plan with FFmpeg: frame-exact cuts on the beat, 9:16 crops, song, overlays."""

from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import Callable

from ..cancel import NEVER, CancelToken
from ..errors import MediaError
from ..media import ffmpeg as ff
from ..media.logo import logo_filter, logo_input_args
from ..media.overlays import DisclosureText, build_overlay_ass
from ..media.render import AFMT, Inputs, _link_fonts, _run_in, ass_layer

FPS = 30


def _cover(sw: int, sh: int, w: int, h: int, mx: float) -> tuple[int, int, int, int]:
    """9:16 cover crop of a sw x sh frame, centred horizontally on mx (0..1)."""
    target = w / h
    if sw / sh > target:
        ch = sh - sh % 2
        cw = int(ch * target)
        cw -= cw % 2
    else:
        cw = sw - sw % 2
        ch = int(cw / target)
        ch -= ch % 2
    x = int(round(mx * sw - cw / 2))
    x = max(0, min(sw - cw, x))
    y = (sh - ch) // 2
    return cw, ch, x, y


def render_montage(
    plan: dict,
    out_path: Path,
    *,
    height: int = 1080,
    song: Path | None,
    audio: str = "song",  # song | silent
    pov: str = "",
    flash: bool = True,
    disclosure: DisclosureText | None = None,
    logo: dict | None = None,
    encoder_args: list[str],
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken = NEVER,
) -> Path:
    slots = plan["slots"]
    if not slots:
        raise MediaError("The montage plan has no shots")
    w, h = height, int(round(height * 16 / 9))
    w -= w % 2
    h -= h % 2
    L = float(plan["length"])
    out_path.parent.mkdir(parents=True, exist_ok=True)
    workdir = Path(tempfile.mkdtemp(prefix="montage_", dir=out_path.parent))
    dims: dict[str, tuple[int, int]] = {}
    try:
        _link_fonts(workdir)
        inputs = Inputs()
        chains: list[str] = []
        labels: list[str] = []
        frame_at = [round(s["at"] * FPS) for s in slots] + [round(L * FPS)]
        for k, s in enumerate(slots):
            n = frame_at[k + 1] - frame_at[k]
            if n <= 0:
                continue
            f = s["file"]
            if f not in dims:
                if not Path(f).exists():
                    raise MediaError(f"A source video for this montage is missing ({Path(f).name}). Re-plan the montage.")
                info = ff.probe(Path(f))
                dims[f] = (info.width, info.height)
            sw, sh = dims[f]
            idx = inputs.add(["-ss", f"{s['t']:.3f}", "-t", f"{n / FPS + 0.5:.3f}", "-i", f])
            cw, ch, x, y = _cover(sw, sh, w, h, float(s.get("mx", 0.5)))
            fx = ",fade=t=in:st=0:d=0.14:color=white" if (flash and s.get("impact")) else ""
            chains.append(f"[{idx}:v]fps={FPS},crop={cw}:{ch}:{x}:{y},scale={w}:{h}:flags=lanczos,setsar=1,format=yuv420p,trim=end_frame={n},setpts=PTS-STARTPTS{fx}[s{k}]")
            labels.append(f"[s{k}]")
        chains.append(f"{''.join(labels)}concat=n={len(labels)}:v=1:a=0[vc]")
        cur = "vc"
        overlay = build_overlay_ass(w, h, hook=None, disclosure=disclosure, clip_duration=L, pov=pov)
        if overlay:
            chains.append(ass_layer("overlay", overlay, workdir, cur, "vo", L))
            cur = "vo"
        if logo and logo.get("file"):
            li = inputs.add(logo_input_args(logo, FPS))
            chains.append(logo_filter(logo, li, w, h, FPS, cur, "vl"))
            cur = "vl"
        chains.append(f"[{cur}]format=yuv420p[v]")
        maps = ["-map", "[v]"]
        if audio == "song" and song is not None:
            ai = inputs.add(["-ss", f"{plan['song_start']:.3f}", "-t", f"{L + 0.2:.3f}", "-i", str(song)])
            fo = max(0.0, L - 0.5)
            chains.append(f"[{ai}:a]{AFMT},afade=t=out:st={fo:.2f}:d=0.5,alimiter=limit=0.95:level=disabled,aresample=48000[a]")
            maps += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        (workdir / "graph.txt").write_text(";".join(chains))
        part = out_path.with_name(out_path.stem + ".part.mp4")
        args = [*inputs.args, "-/filter_complex", "graph.txt", *maps, *encoder_args, "-r", str(FPS), "-movflags", "+faststart", "-t", f"{L:.3f}", str(part)]
        _run_in(workdir, args, L, on_progress or (lambda p: None), cancel)
        os.replace(part, out_path)
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        out_path.with_name(out_path.stem + ".part.mp4").unlink(missing_ok=True)
