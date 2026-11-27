"""Clip rendering with FFmpeg.

Every auxiliary file (crop command scripts, ASS subtitles, fonts link) is written to a per-render
temp dir and FFmpeg runs with that dir as its working directory, so the filtergraph only
references simple relative names. This avoids path-escaping bugs (spaces/colons in external-drive
paths) entirely.

Video chain:  source → [blur burned-in captions] → reframe (track/center/blur/split)
              → [jump cuts] → [punch-in zooms] → captions (+ keyword emphasis) → [emoji]
              → hook title + disclosure + progress bar → logo/template overlay
Audio chain:  voice [jump cuts] (clean-up, [denoise], [profanity mute]) [+ beep] [+ music ducked under
              speech] [+ campaign sound] [+ sound effects] → loudness normalisation (−14 LUFS) → limiter

Jump cuts are applied *after* reframing (select/aselect), so crop plans and burned-text blur stay
in source time while captions, overlays, zooms and sound effects work in output time.
"""

from __future__ import annotations

import json
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
from .burned_text import TextBand, TextCollector, blur_filter, detect_burned_text
from .captions import CapWord, CaptionStyle, build_ass
from .logo import logo_filter, logo_input_args
from .overlays import DisclosureText, EndText, HookText, ProgressBar, build_overlay_ass, end_start
from .polish import (
    ZOOM,
    Timeline,
    audio_envelope,
    censor,
    enable_expr,
    pick_emoji,
    pick_emphasis,
    plan_segments,
    plan_zooms,
    render_emoji_png,
)
from .reframe import (
    BIG_FACE_H,
    FaceTrack,
    MotionCollector,
    cover_crop,
    crop_size,
    detect_faces,
    facecam_rect,
    median_face_height,
    plan_crop,
    plan_motion_crop,
    scan_frames,
    write_sendcmd,
)
from .sfx import Cue, add_cut_whooshes

log = logging.getLogger(__name__)

ASPECTS = {"9:16": 9 / 16, "1:1": 1.0, "16:9": 16 / 9}
FONTS_DIR = Path(__file__).resolve().parents[1] / "assets" / "fonts"
AFMT = "aresample=48000,aformat=sample_fmts=fltp:channel_layouts=stereo"


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
    layout: str = "auto"  # auto | center | blur | split | track | stream
    # Stream layout: {"cam": [x0,y0,x1,y1] (fractions of the source) or None, "cam_height": 0.38,
    # "game_x": 0.5, "cam_top": True}. Without a cam box, Auto/Stream try to find the webcam.
    stream: dict | None = None
    auto_fallback: str = "fill"  # what Auto does when there's no big face: fill (crop) | blur
    auto_stream: bool = True  # Auto may detect a webcam overlay and use the stream layout
    follow_action: bool = True  # fill crops (and stream gameplay) drift towards where the motion is
    words: list[CapWord] = field(default_factory=list)  # times relative to clip start
    style: CaptionStyle | None = None
    captions: bool = True
    normalise_audio: bool = True
    max_fps: float = 30.0
    encoder: str = "videotoolbox"  # videotoolbox | x264
    x264_preset: str = "veryfast"
    # audio
    voice_enhance: bool = True
    music: Path | None = None
    music_volume: float = 0.25
    music_offset: float = 0.0
    music_duck: bool = True
    sound: Path | None = None  # campaign sound mixed in (A4)
    sound_offset: float = 0.0
    sound_volume: float = 1.0
    original_volume: float = 1.0  # level of the source audio (lowered/muted under a campaign sound)
    sfx: list[Cue] = field(default_factory=list)
    sfx_files: dict[str, Path] = field(default_factory=dict)
    sfx_gain: dict[str, float] = field(default_factory=dict)
    sfx_on_cuts: bool = False
    # overlays
    hook: HookText | None = None
    end_text: EndText | None = None  # text over the last seconds (call to action / end screen)
    disclosure: DisclosureText | None = None
    logo: dict | None = None  # campaign LogoSpec as a dict (with a stored file path)
    blur_burned: str = "off"  # auto | off
    burned_band: TextBand | None = None  # precomputed (preview frames)
    # retention polish (Phase 4A)
    jumpcut: str = "off"  # off | light | normal | tight (dead air removed only where it's quiet)
    fillers: bool = True  # also cut um / uh when jump cuts are on
    zoom: str = "off"  # off | subtle | punchy
    emphasis: bool = False  # keyword emphasis in captions
    emoji: bool = False
    progress: str = "off"  # off | top | bottom
    progress_span: tuple[float, float] = (0.0, 1.0)
    profanity: str = "off"  # off | beep | mute
    denoise: str = "off"  # off | light | strong
    flash_in: bool = False  # white flash at the start (main part after a cold open)
    music_fade_in: bool = True
    music_fade_out: bool = True
    segments: list[tuple[float, float]] | None = None  # precomputed jump cuts (clip-relative source time)

    @property
    def duration(self) -> float:
        return self.end - self.start

    def timeline(self) -> Timeline:
        if self.segments:
            return Timeline([tuple(x) for x in self.segments], self.duration)
        return Timeline.full(self.duration)

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

    def fps(self) -> float:
        return min(self.src_fps or 30.0, self.max_fps)


@dataclass
class RenderResult:
    path: Path
    layout_used: str
    face_ratio: float
    burned_text: dict | None = None
    sfx: list[dict] = field(default_factory=list)
    stream: dict | None = None  # stream settings actually used (incl. a detected webcam box)
    polish: dict = field(default_factory=dict)  # what retention polish actually did
    out_duration: float = 0.0


@dataclass
class Post:
    """Everything that happens in *output* time (after jump cuts)."""

    timeline: Timeline
    words: list[CapWord]
    emphasis: set[int] = field(default_factory=set)
    zooms: list[tuple[float, float]] = field(default_factory=list)
    emoji: list[tuple[int, float, float]] = field(default_factory=list)  # (input index, start, end)
    emoji_size: int = 0
    mute: list[tuple[float, float]] = field(default_factory=list)  # profanity spans
    progress: ProgressBar | None = None

    @property
    def duration(self) -> float:
        return self.timeline.out_duration


DENOISE = {"light": "afftdn=nr=10:nf=-45:tn=1", "strong": "afftdn=nr=20:nf=-38:tn=1"}


def plan_timeline(spec: RenderSpec, env=None) -> Timeline:
    """Jump cuts for a spec (needs the clip's audio; returns the full clip when off/impossible)."""
    if spec.segments:
        return spec.timeline()
    if spec.jumpcut == "off" or not spec.has_audio or not spec.words:
        return Timeline.full(spec.duration)
    try:
        if env is None:
            env = audio_envelope(spec.src, spec.start, spec.duration)
        return plan_segments(spec.words, spec.duration, env, level=spec.jumpcut, fillers=spec.fillers, fps=spec.fps())
    except Exception as e:
        log.warning("Jump-cut planning failed, keeping the whole clip: %s", e)
        return Timeline.full(spec.duration)


def plan_post(spec: RenderSpec, timeline: Timeline, *, layout: str, env=None) -> Post:
    """Output-time words, emphasis, zooms and profanity spans (emoji inputs are added by the caller)."""
    w, h = spec.out_size()
    words = timeline.map_words(spec.words)
    mute: list[tuple[float, float]] = []
    if spec.profanity in ("beep", "mute"):
        words, mute = censor(words)
    emphasis: set[int] = set()
    if spec.emphasis and spec.style is not None and spec.captions:
        emphasis = pick_emphasis(words, spec.style, width=w, height=h)
    zooms: list[tuple[float, float]] = []
    if spec.zoom in ZOOM and layout in ("track", "center", "blur"):  # never zoom a stacked layout
        targets = emphasis if spec.emphasis else (pick_emphasis(words, spec.style, width=w, height=h) if spec.style is not None else set())
        zooms = plan_zooms(words, targets, timeline.out_duration, level=spec.zoom, cut_points=timeline.cut_points(), env=env, timeline=timeline)
    progress = None
    if spec.progress in ("top", "bottom"):
        color = spec.style.highlight_color if spec.style is not None and spec.style.highlight_color.upper() != "#FFFFFF" else "#FFE600"
        progress = ProgressBar(spec.progress, color, *spec.progress_span)
    return Post(timeline, words, emphasis, zooms, mute=mute, progress=progress)


class Inputs:
    """Ordered FFmpeg inputs; returns each input's index for use in the filtergraph."""

    def __init__(self) -> None:
        self.args: list[str] = []
        self.n = 0

    def add(self, args: list[str]) -> int:
        self.args += args
        self.n += 1
        return self.n - 1


def _encoder_args(spec: RenderSpec, w: int, h: int) -> list[str]:
    if spec.encoder == "x264":
        return ["-c:v", "libx264", "-preset", spec.x264_preset, "-crf", "21", "-pix_fmt", "yuv420p"]
    kbps = 5000 if h * w <= 1280 * 720 else 9000
    return ["-c:v", "h264_videotoolbox", "-b:v", f"{kbps}k", "-maxrate", f"{int(kbps * 1.5)}k", "-allow_sw", "1", "-pix_fmt", "yuv420p"]


STREAM_DEFAULTS = {"cam": None, "cam_height": 0.38, "game_x": 0.5, "cam_top": True}


def stream_settings(spec: RenderSpec, track: FaceTrack | None) -> dict | None:
    """Stream layout settings with a webcam box (configured, or detected from faces), or None."""
    cfg = STREAM_DEFAULTS | (spec.stream or {})
    if cfg.get("cam"):
        return cfg
    cam = facecam_rect(track)
    return cfg | {"cam": cam, "detected": True} if cam else None


def choose_layout(spec: RenderSpec, track: FaceTrack | None) -> tuple[str, dict | None]:
    """(layout, stream settings). Auto never letterboxes vertical output unless asked to:
    a big face → follow it; a webcam overlay → stream layout; otherwise fill the frame."""
    fallback = "blur" if spec.auto_fallback == "blur" else "center"
    if spec.layout == "stream":
        st = stream_settings(spec, track)
        return ("stream", st) if st else (fallback, None)
    if spec.layout != "auto":
        return spec.layout, None
    if ASPECTS[spec.aspect] >= spec.src_w / spec.src_h - 0.01:
        return "center", None  # target is as wide as the source: nothing to track
    if track is not None and track.face_ratio >= 0.3 and median_face_height(track) >= BIG_FACE_H:
        return "track", None
    if spec.auto_stream and ASPECTS[spec.aspect] < 1:
        st = stream_settings(spec, track)
        if st:
            return "stream", st
    if track is not None and track.face_ratio >= 0.3:
        return "track", None  # small but steady face, not in a corner (e.g. a wide shot)
    return fallback, None


def _ass_filter(name: str, workdir: Path) -> str:
    return f"ass={name}:fontsdir=fonts" if (workdir / "fonts").exists() else f"ass={name}"


def ass_layer(name: str, ass_text: str, workdir: Path, src: str, out: str, duration: float) -> str:
    """Burn an ASS file onto ``[src]`` → ``[out]``: with libass when FFmpeg has it, otherwise drawn as
    images (media/caption_png.py), so captions also work with Homebrew's FFmpeg 8.x."""
    from .caption_png import overlay_chain, use_images

    if use_images():
        return overlay_chain(ass_text, name, workdir, src, out, duration)
    (workdir / f"{name}.ass").write_text(ass_text, encoding="utf-8")
    return f"[{src}]{_ass_filter(f'{name}.ass', workdir)}[{out}]"


def _link_fonts(workdir: Path) -> None:
    if FONTS_DIR.is_dir():
        try:
            os.symlink(FONTS_DIR, workdir / "fonts", target_is_directory=True)
        except OSError:
            shutil.copytree(FONTS_DIR, workdir / "fonts", dirs_exist_ok=True)


def build_video_graph(
    spec: RenderSpec,
    layout: str,
    track: FaceTrack | None,
    workdir: Path,
    *,
    band: TextBand | None = None,
    logo_idx: int | None = None,
    stream: dict | None = None,
    motion: MotionCollector | None = None,
    post: Post | None = None,
) -> str:
    w, h = spec.out_size()
    post = post or Post(Timeline.full(spec.duration), list(spec.words))
    fps = spec.fps()
    aspect = ASPECTS[spec.aspect]
    chains: list[str] = []
    base = "0:v"
    if band is not None and band.intervals:
        chains.append(blur_filter(band, spec.src_w, spec.src_h, "0:v", "src"))
        base = "src"
    if layout in ("track", "center"):
        if layout == "center" and motion is not None:
            plan = plan_motion_crop(motion, src_w=spec.src_w, src_h=spec.src_h, aspect=aspect, duration=spec.duration, out_fps=fps)
        else:
            plan = plan_crop(track if layout == "track" else None, src_w=spec.src_w, src_h=spec.src_h, aspect=aspect, duration=spec.duration, out_fps=fps)
        write_sendcmd(plan, workdir / "crop.cmd")
        chains.append(
            f"[{base}]fps={fps:g},sendcmd=f=crop.cmd,crop@crop=w={plan.crop_w}:h={plan.crop_h}:x={int(plan.xs[0])}:y={int(plan.ys[0])},"
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
            f"[{base}]fps={fps:g},split=2[s1][s2];"
            f"[s1]sendcmd=f=top.cmd,crop@top=w={top.crop_w}:h={top.crop_h}:x={int(top.xs[0])}:y={int(top.ys[0])},scale={w}:{half_h},setsar=1[t];"
            f"[s2]sendcmd=f=bot.cmd,crop@bot=w={bot.crop_w}:h={bot.crop_h}:x={int(bot.xs[0])}:y={int(bot.ys[0])},scale={w}:{h - half_h},setsar=1[b];"
            f"[t][b]vstack=inputs=2[vb]"
        )
    elif layout == "stream" and stream and stream.get("cam"):
        # webcam on top (or bottom), gameplay filling the rest: both "cover" crops, no bars
        cam_h = int(round(h * float(stream.get("cam_height", 0.38))))
        cam_h -= cam_h % 2
        game_h = h - cam_h
        x0, y0, x1, y1 = stream["cam"]
        cw, ch, cx, cy = cover_crop((x0 * spec.src_w, y0 * spec.src_h, x1 * spec.src_w, y1 * spec.src_h), w / cam_h, spec.src_w, spec.src_h)
        gw, gh = crop_size(spec.src_w, spec.src_h, w / game_h)
        gx = int(round(float(stream.get("game_x", 0.5)) * spec.src_w - gw / 2))
        gx = max(0, min(spec.src_w - gw, gx))
        gy = (spec.src_h - gh) // 2
        order = "[cm][gm]" if stream.get("cam_top", True) else "[gm][cm]"
        game_crop = f"crop={gw}:{gh}:{gx}:{gy}"
        if motion is not None and stream.get("game_follow", True):
            gplan = plan_motion_crop(motion, src_w=spec.src_w, src_h=spec.src_h, aspect=w / game_h, duration=spec.duration, out_fps=fps, crop_wh=(gw, gh))
            write_sendcmd(gplan, workdir / "game.cmd", target="crop@game")
            game_crop = f"sendcmd=f=game.cmd,crop@game=w={gw}:h={gh}:x={int(gplan.xs[0])}:y={gy}"
        chains.append(
            f"[{base}]fps={fps:g},split=2[c0][g0];"
            f"[c0]crop={cw}:{ch}:{cx}:{cy},scale={w}:{cam_h}:flags=lanczos,setsar=1[cm];"
            f"[g0]{game_crop},scale={w}:{game_h}:flags=lanczos,setsar=1[gm];"
            f"{order}vstack=inputs=2[vb]"
        )
    else:  # blur
        chains.append(
            f"[{base}]fps={fps:g},split=2[a][b];"
            f"[a]scale={w // 4}:{h // 4}:force_original_aspect_ratio=increase,crop={w // 4}:{h // 4},boxblur=8:2,scale={w}:{h},eq=brightness=-0.08[bg];"
            f"[b]scale={w}:{h}:force_original_aspect_ratio=decrease,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vb]"
        )
    cur = "vb"
    if post.timeline.cut:
        chains.append(f"[{cur}]select='{post.timeline.select_expr(1 / fps)}',setpts=N/{fps:g}/TB[vj]")
        cur = "vj"
    if spec.flash_in:
        chains.append(f"[{cur}]fade=t=in:st=0:d=0.2:color=white[vf]")
        cur = "vf"
    if post.zooms:
        z = ZOOM.get(spec.zoom, 1.1)
        zw, zh = int(w / z) // 2 * 2, int(h / z) // 2 * 2
        fy = 0.4 if layout == "track" else 0.5  # keep a tracked face in the zoomed frame
        chains.append(
            f"[{cur}]split=2[z0][z1];[z1]crop={zw}:{zh}:{(w - zw) // 2}:{int((h - zh) * fy)},scale={w}:{h}:flags=bicubic,setsar=1[zz];"
            f"[z0][zz]overlay=0:0:enable='{enable_expr(post.zooms)}'[vz]"
        )
        cur = "vz"
    end = spec.end_text if spec.end_text is not None and spec.end_text.text.strip() else None
    card_at = end_start(end, post.duration) if end is not None and end.look == "card" else None
    words = post.words
    if card_at is not None:
        # end card: the video is dimmed and softened behind the text, and captions stop there
        on = f"enable='gte(t,{card_at:.3f})'"
        chains.append(f"[{cur}]boxblur=12:2:{on},drawbox=x=0:y=0:w=iw:h=ih:color=black@0.5:t=fill:{on}[vk]")
        cur = "vk"
        words = [x for x in words if x.s < card_at]
    if spec.captions and words and spec.style is not None:
        chains.append(ass_layer("subs", build_ass(words, spec.style, width=w, height=h, emphasis=post.emphasis), workdir, cur, "vc", post.duration))
        cur = "vc"
    if post.emoji and spec.style is not None:
        ey = int(spec.style.position * h - spec.style.size * h * (1.1 + 0.5 * (spec.style.max_lines - 1)) - post.emoji_size)
        ey = max(int(h * 0.12), ey)
        for k, (idx, a, b) in enumerate(post.emoji):
            chains.append(f"[{cur}][{idx}:v]overlay=x=(W-w)/2:y={ey}:enable='between(t,{a:.3f},{b:.3f})'[ve{k}]")
            cur = f"ve{k}"
    hook = spec.hook
    if hook is not None and layout == "stream" and stream:
        # put the hook title on the seam between webcam and gameplay
        seam = float(stream.get("cam_height", 0.38))
        hook = HookText(hook.text, hook.duration, seam if stream.get("cam_top", True) else 1 - seam, hook.style)
    overlay = build_overlay_ass(w, h, hook=hook, disclosure=spec.disclosure, clip_duration=post.duration, progress=post.progress, end_text=end)
    if overlay:
        chains.append(ass_layer("overlay", overlay, workdir, cur, "vo", post.duration))
        cur = "vo"
    if logo_idx is not None and spec.logo:
        chains.append(logo_filter(spec.logo, logo_idx, w, h, fps, cur, "vl"))
        cur = "vl"
    chains.append(f"[{cur}]format=yuv420p[v]")
    return ";".join(chains)


LOUDNORM = "loudnorm=I=-14:TP=-1.5:LRA=11"


def loudnorm_filter(measured: dict | None) -> str:
    """Second pass of two-pass loudness (adapted from nero-harsh/clipstudio): with the first pass's numbers
    the correction is one steady gain to -14 LUFS; without them, FFmpeg's single-pass dynamic mode."""
    try:
        if measured and all(k in measured for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")):
            m = {k: float(measured[k]) for k in ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")}
            if all(v == v and abs(v) != float("inf") for v in m.values()) and m["input_i"] > -70:
                return (
                    f"{LOUDNORM}:measured_I={m['input_i']:.2f}:measured_TP={m['input_tp']:.2f}:measured_LRA={m['input_lra']:.2f}"
                    f":measured_thresh={m['input_thresh']:.2f}:offset={m['target_offset']:.2f}:linear=true"
                )
    except (TypeError, ValueError):
        pass
    return LOUDNORM


def measure_mix_loudness(cwd: Path, input_args: list[str], graph: str, duration: float, cancel: CancelToken) -> dict | None:
    """First pass: run only the audio mix and read loudnorm's measurement. None if it can't be measured."""
    (cwd / "agraph.txt").write_text(graph)
    cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-y", *input_args, "-/filter_complex", "agraph.txt", "-map", "[a]", "-t", f"{duration:.3f}", "-f", "null", "-"]
    cancel.check()
    try:
        with tempfile.TemporaryFile(mode="w+") as errf:
            proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.DEVNULL, stderr=errf, text=True)
            cancel.attach(proc)
            try:
                proc.wait(timeout=max(60.0, duration * 4))
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
                return None
            finally:
                cancel.detach(proc)
            if cancel.cancelled:
                raise Cancelled()
            errf.seek(0)
            err = errf.read()
        if proc.returncode != 0:
            return None
        block = err[err.rfind("{") : err.rfind("}") + 1]
        return json.loads(block) if block else None
    except (OSError, ValueError):
        return None


def build_audio_graph(
    spec: RenderSpec,
    *,
    music_idx: int | None = None,
    sound_idx: int | None = None,
    sfx_idx: list[tuple[int, Cue]] | None = None,
    post: Post | None = None,
    measured: dict | None = None,
    measure_only: bool = False,
) -> str | None:
    """The audio mix. ``measure_only``: end in a loudness measurement (first pass of two-pass loudness);
    ``measured``: the first pass's numbers, so the loudness correction is exact and linear."""
    if not spec.has_audio:
        return None
    post = post or Post(Timeline.full(spec.duration), list(spec.words))
    dur = post.duration
    parts: list[str] = []
    mix: list[str] = []
    voice = "[0:a]"
    if post.timeline.cut:
        # 160-sample frames (3.3 ms) so cuts land within a few ms of the video's frame-exact cuts
        voice += f"aresample=48000,asetnsamples=n=160:p=0,aselect='{post.timeline.select_expr(160 / 48000)}',asetpts=N/SR/TB,"
    voice += AFMT
    if spec.denoise in DENOISE:
        voice += "," + DENOISE[spec.denoise]
    if spec.voice_enhance:
        voice += ",highpass=f=80,acompressor=threshold=0.1:ratio=3:attack=5:release=120:makeup=2"
    if post.mute:
        voice += f",volume=0:enable='{enable_expr(post.mute)}'"
    voice_vol = spec.original_volume if sound_idx is not None else 1.0
    if voice_vol < 0.999:
        voice += f",volume={voice_vol:.3f}"
    duck = music_idx is not None and spec.music_duck and voice_vol > 0.05
    if duck:
        parts.append(voice + ",asplit=2[vmain][vkey]")
    else:
        parts.append(voice + "[vmain]")
    mix.append("[vmain]")
    if music_idx is not None:
        fo = max(0.0, dur - 1.5)
        fades = ("afade=t=in:d=1," if spec.music_fade_in else "") + (f"afade=t=out:st={fo:.2f}:d=1.5," if spec.music_fade_out else "")
        parts.append(f"[{music_idx}:a]{AFMT},volume={spec.music_volume:.3f},{fades}atrim=0:{dur + 0.5:.3f}[mraw]")
        if duck:
            # Music drops ~10 dB while someone is speaking and comes back up in pauses.
            parts.append("[mraw][vkey]sidechaincompress=threshold=0.02:ratio=8:attack=20:release=400:makeup=1[mus]")
            mix.append("[mus]")
        else:
            mix.append("[mraw]")
    if sound_idx is not None:
        fo = max(0.0, dur - 0.6)
        parts.append(f"[{sound_idx}:a]{AFMT},volume={spec.sound_volume:.3f},afade=t=out:st={fo:.2f}:d=0.6[snd]")
        mix.append("[snd]")
    for k, (idx, cue) in enumerate(sfx_idx or []):
        ms = int(round(cue.t * 1000))
        gain = spec.sfx_gain.get(cue.type, 0.4)
        parts.append(f"[{idx}:a]{AFMT},volume={gain:.3f},adelay={ms}:all=1[fx{k}]")
        mix.append(f"[fx{k}]")
    if post.mute and spec.profanity == "beep":
        parts.append(f"sine=frequency=1000:sample_rate=48000:duration={dur + 0.5:.3f},{AFMT},asetnsamples=n=480:p=0,volume=0.18,volume=0:enable='not({enable_expr(post.mute)})'[beep]")
        mix.append("[beep]")
    if len(mix) > 1:
        master = "".join(mix) + f"amix=inputs={len(mix)}:duration=first:normalize=0:dropout_transition=0"
    else:
        master = "[vmain]anull"
    if measure_only:
        parts.append(master + f",{LOUDNORM}:print_format=json[a]")
        return ";".join(parts)
    if spec.normalise_audio:
        master += "," + loudnorm_filter(measured)
    master += ",alimiter=limit=0.84:level=disabled,aresample=48000[a]"  # catch peaks at about -1.5 dB (no auto make-up gain)
    parts.append(master)
    return ";".join(parts)


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
        band: TextBand | None = None
        collector = TextCollector() if spec.blur_burned == "auto" else None
        narrower = ASPECTS[spec.aspect] < spec.src_w / spec.src_h - 0.01
        motion = MotionCollector(crop_frac=ASPECTS[spec.aspect] / (spec.src_w / spec.src_h)) if (spec.follow_action and narrower and spec.layout in ("auto", "center", "stream")) else None
        hooks = [cb for cb in (collector.add if collector else None, motion.add if motion else None) if cb]

        def fan(t: float, small) -> None:
            for cb in hooks:
                cb(t, small)

        needs_faces = face_track and (spec.layout in ("auto", "track", "split") or (spec.layout == "stream" and not (spec.stream or {}).get("cam")))
        scanned = False
        if needs_faces and narrower:
            try:
                track = detect_faces(spec.src, spec.start, spec.end, cancel=cancel, on_frame=fan if hooks else None)
                scanned = True
            except Cancelled:
                raise
            except Exception as e:  # never fail a render because face detection failed
                log.warning("Face detection failed, falling back: %s", e)
        if hooks and not scanned:
            try:  # one cheap pass for text + motion when faces weren't needed
                scan_frames(spec.src, spec.start, spec.end, hooks, cancel=cancel)
            except Cancelled:
                raise
            except Exception as e:
                log.warning("Frame scan failed: %s", e)
        if collector is not None:
            try:
                band = collector.band(spec.duration) if collector.samples else None
            except Exception as e:
                log.warning("Burned-in text detection failed: %s", e)
        if on_progress:
            on_progress(0.15)
        layout, stream = choose_layout(spec, track)
        if layout == "split" and (track is None or track.two_person_ratio < 0.2):
            layout = "track" if track and track.face_ratio >= 0.3 else ("blur" if spec.auto_fallback == "blur" else "center")
        if layout == "track" and (track is None or track.face_ratio < 0.15):
            layout = "center"

        env = None
        if spec.has_audio and (spec.jumpcut != "off" or spec.zoom in ZOOM) and not spec.segments:
            try:
                env = audio_envelope(spec.src, spec.start, spec.duration)
            except Exception as e:
                log.warning("Audio envelope failed: %s", e)
        timeline = plan_timeline(spec, env)
        post = plan_post(spec, timeline, layout=layout, env=env)

        cues = list(spec.sfx)
        if spec.sfx_on_cuts and track is not None and layout in ("track", "split"):
            cuts = [track.times[c] for c in track.cuts if 0 < c < len(track.times)]
            cues = add_cut_whooshes(cues, cuts, spec.duration)
        if timeline.cut:  # sound effects were planned in source time
            cues = [Cue(c.type, t) for c in cues if (t := timeline.map_time(c.t)) is not None]
        cues = [c for c in cues if c.type in spec.sfx_files]
        out_dur = post.duration

        _link_fonts(workdir)
        fps = spec.fps()
        inputs = Inputs()
        inputs.add(["-ss", f"{spec.start:.3f}", "-t", f"{spec.duration:.3f}", "-i", str(spec.src)])
        logo_idx = inputs.add(logo_input_args(spec.logo, fps)) if spec.logo and spec.logo.get("file") else None
        music_idx = inputs.add(["-stream_loop", "-1", "-ss", f"{spec.music_offset:.2f}", "-i", str(spec.music)]) if spec.music and spec.has_audio else None
        sound_idx = inputs.add(["-stream_loop", "-1", "-ss", f"{spec.sound_offset:.2f}", "-i", str(spec.sound)]) if spec.sound and spec.has_audio else None
        sfx_idx = [(inputs.add(["-i", str(spec.sfx_files[c.type])]), c) for c in cues] if spec.has_audio else []
        if spec.emoji and post.emphasis and spec.style is not None and spec.captions:
            size = int(spec.out_size()[1] * 0.075)
            pngs: dict[str, int | None] = {}
            for a, b, ch in pick_emoji(post.words, post.emphasis):
                if ch not in pngs:
                    png = render_emoji_png(ch, workdir / f"emoji{len(pngs)}.png", size)
                    pngs[ch] = inputs.add(["-loop", "1", "-framerate", f"{fps:g}", "-t", f"{out_dur + 1:.3f}", "-i", png.name]) if png else None
                if pngs[ch] is not None:
                    post.emoji.append((pngs[ch], a, min(b, out_dur)))
            post.emoji_size = size

        graph = build_video_graph(spec, layout, track, workdir, band=band, logo_idx=logo_idx, stream=stream, motion=motion, post=post)
        measured = None
        if spec.normalise_audio and spec.has_audio:  # two-pass loudness: measure the finished mix first
            first = build_audio_graph(spec, music_idx=music_idx, sound_idx=sound_idx, sfx_idx=sfx_idx, post=post, measure_only=True)
            if first:
                measured = measure_mix_loudness(workdir, inputs.args, first, out_dur, cancel)
        agraph = build_audio_graph(spec, music_idx=music_idx, sound_idx=sound_idx, sfx_idx=sfx_idx, post=post, measured=measured)
        if agraph:
            graph += ";" + agraph
        (workdir / "graph.txt").write_text(graph)
        w, h = spec.out_size()
        part = out_path.with_name(out_path.stem + ".part.mp4")
        args = [*inputs.args, "-/filter_complex", "graph.txt", "-map", "[v]"]
        if agraph:
            args += ["-map", "[a]", "-c:a", "aac", "-b:a", "192k"]
        args += [*_encoder_args(spec, w, h), "-r", f"{fps:g}", "-movflags", "+faststart", "-t", f"{out_dur:.3f}", str(part)]

        def prog(p: float) -> None:
            if on_progress:
                on_progress(0.15 + 0.85 * p)

        _run_in(workdir, args, out_dur, prog, cancel)
        os.replace(part, out_path)  # atomic: a finished file is either complete or absent
        bt = {"y0": band.y0, "y1": band.y1, "presence": band.presence, "intervals": band.intervals} if band else None
        polish = {
            "cuts": len(timeline.segments) - 1 if timeline.cut else 0,
            "removed": round(timeline.removed, 2),
            "zooms": len(post.zooms),
            "emphasis": len(post.emphasis),
            "emoji": len(post.emoji),
            "censored": len(post.mute),
            "segments": [[round(a, 3), round(b, 3)] for a, b in timeline.segments] if timeline.cut else None,
        }
        return RenderResult(out_path, layout, track.face_ratio if track else 0.0, bt, [c.as_dict() for c in cues], stream, polish, round(out_dur, 3))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        part = out_path.with_name(out_path.stem + ".part.mp4")
        if part.exists():
            part.unlink(missing_ok=True)


def _run_in(cwd: Path, args: list[str], duration: float, on_progress, cancel: CancelToken) -> None:
    # ff.run_ffmpeg doesn't take cwd; run a scoped subprocess with identical semantics instead.
    cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-y", "-loglevel", "error", "-progress", "pipe:1", "-nostats", *args]
    cancel.check()
    # stderr goes to a temp file, not a pipe: an unread stderr pipe fills up (64 KB) when FFmpeg
    # logs repeatedly and then blocks FFmpeg forever.
    with tempfile.TemporaryFile(mode="w+") as errf:
        proc = subprocess.Popen(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=errf, text=True)
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
        finally:
            cancel.detach(proc)
            if proc.poll() is None:
                proc.kill()
                proc.wait()
        errf.seek(0)
        err = errf.read()[-4000:]
    if cancel.cancelled:
        raise Cancelled()
    if proc.returncode != 0:
        tail = " | ".join(err.strip().splitlines()[-6:])
        raise MediaError(f"Render failed: {tail or proc.returncode}")


def render_preview_frame(spec: RenderSpec, t: float, out_jpg: Path) -> Path:
    """Single frame at clip-relative time ``t`` with the exact caption/overlay/logo styling (for the editor)."""
    workdir = Path(tempfile.mkdtemp(prefix="preview_", dir=out_jpg.parent))
    try:
        stream = STREAM_DEFAULTS | (spec.stream or {}) if (spec.stream or {}).get("cam") else None
        layout = spec.layout if spec.layout in ("center", "blur") else ("stream" if spec.layout in ("stream", "auto") and stream else ("blur" if spec.auto_fallback == "blur" and spec.layout == "auto" else "center"))
        sub = RenderSpec(**{**spec.__dict__, "start": spec.start + t, "end": spec.start + t + 1.0})
        sub.words = [CapWord(w.w, w.s - t, w.e - t) for w in spec.words if w.e > t]
        sub.segments = None
        if sub.hook:
            sub.hook = HookText(sub.hook.text, max(0.0, sub.hook.duration - t), sub.hook.y, sub.hook.style)
        if sub.end_text:  # keep it starting where it would in the full clip
            e = sub.end_text
            sub.end_text = EndText(e.text, e.duration, e.y, e.style, e.look, end_start(e, spec.duration) - t)
        if spec.progress in ("top", "bottom") and spec.duration > 0:
            a, b = spec.progress_span
            sub.progress_span = (a + (b - a) * t / spec.duration, a + (b - a) * min(1.0, (t + 1) / spec.duration))
        band = None
        if spec.burned_band is not None:
            b = spec.burned_band
            band = TextBand(b.y0, b.y1, b.presence, [(s - t, e - t) for s, e in b.intervals if e > t])
        _link_fonts(workdir)
        inputs = Inputs()
        inputs.add(["-ss", f"{spec.start + t:.3f}", "-i", str(spec.src)])
        logo_idx = inputs.add(logo_input_args(spec.logo, sub.fps())) if spec.logo and spec.logo.get("file") else None
        post = plan_post(sub, Timeline.full(sub.duration), layout=layout)
        post.zooms = []
        graph = build_video_graph(sub, layout, None, workdir, band=band, logo_idx=logo_idx, stream=stream, post=post)
        (workdir / "graph.txt").write_text(graph)
        tmp = out_jpg.with_suffix(".part.jpg")
        _run_in(workdir, [*inputs.args, "-/filter_complex", "graph.txt", "-map", "[v]", "-frames:v", "1", "-q:v", "3", "-update", "1", str(tmp)], 1.0, lambda p: None, NEVER)
        os.replace(tmp, out_jpg)
        return out_jpg
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


def make_thumbnail(video: Path, out_jpg: Path, t: float = 1.0) -> Path:
    tmp = out_jpg.with_suffix(".part.jpg")
    ff.run_ffmpeg(["-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1", "-vf", "scale=360:-2", "-q:v", "5", "-update", "1", str(tmp)], timeout=60)
    os.replace(tmp, out_jpg)
    return out_jpg


def concat_parts(parts: list[Path], out_path: Path) -> Path:
    """Join renders made with identical settings (cold-open teaser + main part) without re-encoding."""
    workdir = Path(tempfile.mkdtemp(prefix="concat_", dir=out_path.parent))
    try:
        lst = workdir / "list.txt"
        lst.write_text("".join(f"file '{p.resolve().as_posix()}'\n" for p in parts))
        tmp = out_path.with_name(out_path.stem + ".part.mp4")
        ff.run_ffmpeg(["-f", "concat", "-safe", "0", "-i", str(lst), "-c", "copy", "-movflags", "+faststart", str(tmp)], timeout=300)
        os.replace(tmp, out_path)
        return out_path
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


__all__ = ["RenderSpec", "RenderResult", "render_clip", "render_preview_frame", "make_thumbnail", "crop_size", "concat_parts", "plan_timeline"]
