"""Phase 4A retention polish — synthetic media only (no real videos)."""

import subprocess

import numpy as np
import pytest

from clipstudio.media import ffmpeg as ff
from clipstudio.media.captions import PRESETS, CapWord, build_ass, resolve_style
from clipstudio.media.overlays import HookText, ProgressBar, build_overlay_ass
from clipstudio.media.polish import (
    ENV_RATE,
    Timeline,
    audio_envelope,
    censor,
    is_filler,
    is_profane,
    mask,
    peak_window,
    pick_emoji,
    pick_emphasis,
    plan_segments,
    plan_zooms,
    render_emoji_png,
)
from clipstudio.media.render import RenderSpec, concat_parts, plan_timeline, render_clip

# talking 0–3 s, silence 3–5.5 s, talking 5.5–9 s, silence 9–12 s, talking 12–16 s
TALK = [(0.0, 3.0), (5.5, 9.0), (12.0, 16.0)]
DUR = 16.0


def _words() -> list[CapWord]:
    text = {
        0: "so this is the insane part of the story",
        1: "um we made a million dollars in fuck one week",
        2: "and honestly nobody expected that kind of money",
    }
    out = []
    for k, (a, b) in enumerate(TALK):
        toks = text[k].split()
        step = (b - a - 0.2) / len(toks)
        for i, t in enumerate(toks):
            out.append(CapWord(t, round(a + 0.1 + i * step, 3), round(a + 0.1 + (i + 0.8) * step, 3)))
    return out


def _env() -> np.ndarray:
    n = int(DUR * ENV_RATE)
    env = np.full(n, -70.0, np.float32)
    for a, b in TALK:
        env[int(a * ENV_RATE) : int(b * ENV_RATE)] = -18.0
    return env


@pytest.fixture(scope="module")
def talk_video(tmp_path_factory):
    d = tmp_path_factory.mktemp("polish")
    vol = "+".join(f"between(t,{a},{b})" for a, b in TALK)
    out = d / "talk.mp4"
    subprocess.run(
        [
            ff.FFMPEG, "-v", "error", "-y",
            "-f", "lavfi", "-i", f"testsrc2=size=960x540:rate=30:duration={DUR}",
            "-f", "lavfi", "-i", f"sine=frequency=300:sample_rate=48000:duration={DUR}",
            "-filter_complex", f"[1:a]volume='if({vol},0.5,0)':eval=frame[a]",
            "-map", "0:v", "-map", "[a]", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out),
        ],
        check=True,
    )
    return out


def _duration(path) -> float:
    return ff.probe(path).duration


# ----------------------------------------------------------------------------- planning
def test_vocab():
    assert is_filler("Um,") and is_filler("uh") and not is_filler("umbrella")
    assert is_profane("Fucking") and is_profane("bullshit!") and not is_profane("cocktail") and not is_profane("class")
    assert mask("fuck!") == "f***!" and mask("Shit") == "S***"


def test_plan_segments_cuts_quiet_pauses_and_fillers():
    tl = plan_segments(_words(), DUR, _env(), level="normal", fillers=True, fps=30)
    assert tl.cut
    # both long silences removed, the talking kept
    assert 3.5 < tl.removed < 6.0
    for a, b in TALK:
        mid = (a + b) / 2
        assert tl.map_time(mid) is not None
    assert tl.map_time(4.2) is None and tl.map_time(10.5) is None
    # the filler "um" is gone from the output words
    assert not any(is_filler(w.w) for w in tl.map_words(_words()))
    # boundaries are on the 30 fps grid
    for s, e in tl.segments:
        assert abs(s * 30 - round(s * 30)) < 1e-3 and abs(e * 30 - round(e * 30)) < 1e-3


def test_plan_segments_keeps_loud_pauses():
    env = np.full(int(DUR * ENV_RATE), -18.0, np.float32)  # gameplay noise between sentences
    tl = plan_segments(_words(), DUR, env, level="tight", fillers=False)
    assert not tl.cut


def test_plan_segments_no_words_is_identity():
    assert not plan_segments([], DUR, _env()).cut


def test_timeline_mapping():
    tl = Timeline([(0, 2), (5, 8)], 10)
    assert tl.out_duration == 5 and tl.cut_points() == [2]
    assert tl.map_time(6) == 3 and tl.map_time(3) is None
    ws = tl.map_words([CapWord("a", 1, 1.5), CapWord("b", 3, 4), CapWord("c", 5.5, 6)])
    assert [w.w for w in ws] == ["a", "c"] and ws[1].s == 2.5
    assert "between(t," in tl.select_expr(1 / 30)


def test_emphasis_emoji_and_zooms():
    words = _words()
    style = resolve_style("hormozi")
    emph = pick_emphasis(words, style, width=1080, height=1920)
    picked = {words[i].w for i in emph}
    assert {"insane", "million"} & picked
    emoji = pick_emoji(words, emph)
    assert emoji and all(e[1] > e[0] for e in emoji)
    zooms = plan_zooms(words, emph, DUR, level="punchy", cut_points=[3.2, 6.9])
    assert zooms and sum(b - a for a, b in zooms) <= DUR * 0.45 + 1e-6
    assert plan_zooms(words, emph, DUR, level="off") == []


def test_censor():
    words, spans = censor(_words())
    assert any(w.w == "f***" for w in words) and len(spans) == 1


def test_peak_window_prefers_hint_and_snaps_to_words():
    words = _words()
    a, b = peak_window(words, DUR, _env(), peak_t=13.0)
    assert 10 < a < 13 < b <= DUR and 1.2 <= b - a <= 4.0
    assert peak_window(words, 6.0, _env()) is None


def test_emoji_png(tmp_path):
    p = render_emoji_png("🔥", tmp_path / "e.png", 96)
    if p is None:
        pytest.skip("no colour emoji font on this machine")
    assert p.exists()


def test_new_caption_presets_and_emphasis_tags():
    for k in ("hormozi", "beast", "hype", "neon", "boxed"):
        assert k in PRESETS
    words = _words()
    ass = build_ass(words, resolve_style("neon"), width=1080, height=1920, emphasis={2})
    assert "\\blur" in ass and "\\fscx112" in ass


def test_hook_styles_and_progress_bar():
    for st in ("box", "dark", "yellow", "banner", "bold", "outline"):
        ass = build_overlay_ass(1080, 1920, hook=HookText("Wait for it", 3, style=st), disclosure=None, clip_duration=10)
        assert f"Hook_{st}," in ass
    ass = build_overlay_ass(1080, 1920, hook=None, disclosure=None, clip_duration=10, progress=ProgressBar("bottom", "#FF0000", 0.2, 1.0))
    assert "\\clip(0,1908,216,1920)" in ass and "\\t(0,10000," in ass


# ----------------------------------------------------------------------------- rendering
def test_envelope_from_file(talk_video):
    env = audio_envelope(talk_video, 0, DUR)
    assert env.size > DUR * ENV_RATE * 0.9
    assert env[int(1.5 * ENV_RATE)] > env[int(4.2 * ENV_RATE)] + 20


def _spec(src, **kw) -> RenderSpec:
    base = dict(src=src, start=0.0, end=DUR, src_w=960, src_h=540, src_fps=30, has_audio=True, height=360, layout="center", words=_words(), style=resolve_style("hormozi"), encoder="x264", x264_preset="ultrafast", follow_action=False)
    return RenderSpec(**(base | kw))


def test_render_with_full_polish(talk_video, tmp_path):
    spec = _spec(
        talk_video,
        jumpcut="normal",
        zoom="punchy",
        emphasis=True,
        emoji=True,
        progress="top",
        profanity="beep",
        denoise="light",
        hook=HookText("Nobody expected this", 2.5, style="banner"),
    )
    res = render_clip(spec, tmp_path / "out.mp4")
    d = _duration(res.path)
    assert res.polish["cuts"] >= 2 and res.polish["removed"] > 3
    assert res.polish["censored"] == 1 and res.polish["emphasis"] >= 1 and res.polish["zooms"] >= 1
    assert abs(d - res.out_duration) < 0.25
    assert d < DUR - 3


def test_render_without_polish_keeps_duration(talk_video, tmp_path):
    res = render_clip(_spec(talk_video), tmp_path / "plain.mp4")
    assert abs(_duration(res.path) - DUR) < 0.25 and res.polish["cuts"] == 0


def test_cold_open_concat(talk_video, tmp_path):
    main = _spec(talk_video, jumpcut="normal", progress="top")
    tl = plan_timeline(main)
    teaser_len = 2.4
    total = teaser_len + tl.out_duration
    teaser = _spec(talk_video, start=12.5, end=12.5 + teaser_len, words=[], progress="top", progress_span=(0, teaser_len / total))
    main.segments = tl.segments
    main.progress_span = (teaser_len / total, 1.0)
    main.flash_in = True
    a = render_clip(teaser, tmp_path / "a.mp4")
    b = render_clip(main, tmp_path / "b.mp4")
    out = concat_parts([a.path, b.path], tmp_path / "joined.mp4")
    assert abs(_duration(out) - total) < 0.35
