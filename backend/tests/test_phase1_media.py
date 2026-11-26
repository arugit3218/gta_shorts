"""Media tests for Phase 1: burned-in text detection, alignment, logos, full campaign render (synthetic media)."""

import subprocess
from pathlib import Path

import cv2
import numpy as np
import pytest

from clipstudio.media import ffmpeg as ff
from clipstudio.media.align import find_offset
from clipstudio.media.burned_text import TextCollector, detect_burned_text, frame_text_rows
from clipstudio.media.captions import CapWord, resolve_style
from clipstudio.media.logo import content_mask, inspect_logo
from clipstudio.media.overlays import DisclosureText, HookText, build_overlay_ass
from clipstudio.media.render import RenderSpec, render_clip
from clipstudio.media.sfx import Cue, builtin_dir


def _frame_with_text(text: bool) -> np.ndarray:
    f = np.full((270, 480, 3), 90, np.uint8)
    cv2.rectangle(f, (40, 30), (200, 150), (30, 160, 200), -1)  # some scenery
    if text:
        cv2.rectangle(f, (90, 222), (390, 252), (0, 0, 0), -1)
        cv2.putText(f, "THIS IS A SUBTITLE", (100, 244), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
    return f


def test_frame_text_rows():
    r = frame_text_rows(_frame_with_text(True))
    assert r is not None and 0.78 < r[0] < r[1] < 0.96
    assert frame_text_rows(_frame_with_text(False)) is None


def test_text_collector_intervals():
    col = TextCollector()
    for k in range(20):
        col.add(k * 0.5, _frame_with_text(4 <= k < 12))
    band = col.band(10.0)
    assert band is not None and len(band.intervals) == 1
    s, e = band.intervals[0]
    assert 1.4 <= s <= 2.1 and 5.4 <= e <= 6.2


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("p1")
    rng = np.random.default_rng(1)
    noise = (rng.standard_normal(16000 * 30) * 0.3).astype(np.float32)
    (d / "noise.f32").write_bytes(noise.tobytes())
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "f32le", "-ar", "16000", "-ac", "1", "-i", str(d / "noise.f32"), "-c:a", "aac", str(d / "ref.m4a")], check=True)
    # a "section" that really starts at 7.3 s
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-ss", "7.3", "-t", "12", "-i", str(d / "ref.m4a"), "-f", "lavfi", "-i", "color=c=gray:s=320x240:r=25:d=12", "-shortest", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(d / "section.mp4")], check=True)
    # source video with a burned-in subtitle band
    subprocess.run(
        [ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc2=size=960x540:rate=25:duration=6", "-f", "lavfi", "-i", "sine=frequency=220:duration=6",
         "-vf", "drawbox=x=230:y=440:w=500:h=50:color=black@0.9:t=fill", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(d / "src.mp4")],
        check=True,
    )
    logo = np.zeros((200, 600, 4), np.uint8)
    logo[20:180, 20:580] = (0, 200, 0, 255)
    cv2.imwrite(str(d / "logo.png"), logo)
    for i, c in enumerate([(0, 255, 255, 255), (255, 0, 0, 255)]):
        f = np.zeros((1920, 1080, 4), np.uint8)
        f[300:500, 100:500] = c
        cv2.imwrite(str(d / f"tf{i}.png"), f)
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-framerate", "2", "-i", str(d / "tf%d.png"), "-vf", "split[a][b];[a]palettegen=reserve_transparent=1[p];[b][p]paletteuse=alpha_threshold=128", "-loop", "0", str(d / "tpl.gif")], check=True)
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=440:duration=20", str(d / "song.mp3")], check=True)
    return d


def test_find_offset(media):
    start, conf = find_offset(media / "section.mp4", media / "ref.m4a", expected_start=8.0)
    assert conf > 0.5
    assert abs(start - 7.3) < 0.1  # AAC encoder priming (1024 samples) can shift by ~64 ms; < 2 frames


def test_logo_inspect_and_mask(media):
    li = inspect_logo(media / "tpl.gif")
    assert li.animated and (li.width, li.height) == (1080, 1920)
    m = content_mask({"mode": "template", "file": str(media / "tpl.gif")}, 108, 192)
    assert 600 < m.sum() < 1000 and m[30:50, 10:50].all()
    pl = inspect_logo(media / "logo.png")
    m2 = content_mask({"mode": "free", "file": str(media / "logo.png"), "image_w": pl.width, "image_h": pl.height, "x": 0.5, "y": 0.25, "width": 0.5}, 108, 192)
    ys, xs = np.nonzero(m2)
    assert 40 < ys.mean() < 56 and 45 < xs.mean() < 63


def test_overlay_ass():
    ass = build_overlay_ass(1080, 1920, hook=HookText("you will not believe this one weird trick that works", 3.5), disclosure=DisclosureText("#ad"), clip_duration=20)
    assert ass and "Style: Hook_box,Anton" in ass and "#ad" in ass and ass.count("Dialogue:") == 2
    assert build_overlay_ass(1080, 1920, hook=None, disclosure=None, clip_duration=5) is None


def _pixel(video: Path, t: float, x: int, y: int) -> np.ndarray:
    raw = subprocess.run([ff.FFMPEG, "-v", "error", "-ss", str(t), "-i", str(video), "-frames:v", "1", "-vf", "scale=90:160", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(160, 90, 3)[y, x]


def test_full_campaign_render(media, tmp_path):
    words = [CapWord(w, i * 0.45, i * 0.45 + 0.35) for i, w in enumerate("you will not believe what 100 dollars did! but actually it worked".split())]
    sfx_dir = builtin_dir(tmp_path / "cache")
    li = inspect_logo(media / "tpl.gif")
    spec = RenderSpec(
        src=media / "src.mp4", start=0.5, end=5.5, src_w=960, src_h=540, src_fps=25, has_audio=True, height=1080, layout="center",
        words=words, style=resolve_style("bold", None), encoder="x264",
        music=media / "song.mp3", music_offset=2.0, sfx=[Cue("pop", 0.05), Cue("ding", 2.3)], sfx_files={k: sfx_dir / f"{k}.wav" for k in ("pop", "ding")}, sfx_gain={"pop": 0.5, "ding": 0.3},
        hook=HookText("A hook title for testing", 3.0), disclosure=DisclosureText("#ad"), logo={"mode": "template", "file": str(media / "tpl.gif"), "animated": li.animated}, blur_burned="auto",
    )
    out = tmp_path / "out.mp4"
    res = render_clip(spec, out)
    info = ff.probe(out)
    assert (info.width, info.height) == (1080, 1920) and info.has_audio and abs(info.duration - 5.0) < 0.15
    assert res.sfx == [{"type": "pop", "t": 0.05}, {"type": "ding", "t": 2.3}]
    # template loops: frame 0 colour (yellow) at 0.2 s and again at 4.2 s; blue at 0.7 s
    assert tuple(_pixel(out, 0.2, 25, 33)) > (200, 200, 0) and _pixel(out, 0.2, 25, 33)[2] < 60
    assert _pixel(out, 0.7, 25, 33)[2] > 200
    assert _pixel(out, 4.2, 25, 33)[0] > 200


def test_burn_campaign_sound_replaces_audio(media, tmp_path):
    spec = RenderSpec(src=media / "src.mp4", start=0, end=4, src_w=960, src_h=540, src_fps=25, has_audio=True, layout="blur", encoder="x264", sound=media / "song.mp3", sound_volume=1.0, original_volume=0.0, normalise_audio=False, voice_enhance=False)
    out = tmp_path / "snd.mp4"
    render_clip(spec, out)
    raw = subprocess.run([ff.FFMPEG, "-v", "error", "-i", str(out), "-ac", "1", "-ar", "8000", "-f", "f32le", "pipe:1"], capture_output=True, check=True).stdout
    x = np.frombuffer(raw, np.float32)
    spec_mag = np.abs(np.fft.rfft(x[:8000]))
    freqs = np.fft.rfftfreq(8000, 1 / 8000)
    assert abs(freqs[int(np.argmax(spec_mag))] - 440) < 5  # the campaign sound, not the 220 Hz source


def test_detect_burned_text_on_video(media):
    band = detect_burned_text(media / "src.mp4", 0, 6)
    # a plain black box without letters is not text; the detector should stay quiet
    assert band is None
