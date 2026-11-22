import numpy as np

from clipstudio.media.captions import PRESETS, CapWord, ass_color, build_ass, paginate, resolve_style
from clipstudio.media.reframe import Face, FaceTrack, crop_size, plan_crop, write_sendcmd
from clipstudio.media.render import RenderSpec

WORDS = [CapWord(w, i * 0.4, i * 0.4 + 0.3) for i, w in enumerate("this is a test of word level captions. new sentence here".split())]


def test_ass_color_is_bgr():
    assert ass_color("#FF0000") == "&H000000FF"
    assert ass_color("bad") == "&H00FFFFFF"


def test_pages_respect_max_words_and_sentences():
    pages = paginate(WORDS, PRESETS["bold"], width=720, height=1280)
    assert all(len(p.words) <= 3 for p in pages)
    assert not any(p.words[-1].w.endswith(".") and p is not pages[-1] and len(p.words) > 3 for p in pages)
    # sentence end closes a page
    assert any(p.words[-1].w == "captions." for p in pages)
    # pages don't overlap in time
    assert all(a.end <= b.start + 1e-6 for a, b in zip(pages, pages[1:]))


def test_highlight_emits_one_event_per_word():
    ass = build_ass(WORDS, PRESETS["highlight"], width=720, height=1280)
    assert ass.count("Dialogue:") == len(WORDS)
    assert "PlayResY: 1280" in ass
    static = build_ass(WORDS, PRESETS["classic"], width=720, height=1280)
    assert static.count("Dialogue:") < len(WORDS)


def test_ass_escapes_override_braces():
    ass = build_ass([CapWord("{\\evil}", 0, 1)], PRESETS["classic"], width=720, height=1280)
    assert "{\\evil}" not in ass


def test_style_overrides():
    s = resolve_style("bold", {"color": "#00FF00", "max_words": 5})
    assert s.color == "#00FF00" and s.max_words == 5 and s.font == PRESETS["bold"].font


def test_output_sizes():
    base = dict(src=None, start=0, end=1, src_w=1920, src_h=1080, src_fps=30, has_audio=True)
    assert RenderSpec(**base, aspect="9:16", height=720).out_size() == (720, 1280)
    assert RenderSpec(**base, aspect="1:1", height=1080).out_size() == (1080, 1080)
    assert RenderSpec(**base, aspect="16:9", height=720).out_size() == (1280, 720)


def test_crop_size_fits_source():
    assert crop_size(1920, 1080, 9 / 16) == (608, 1080)
    assert crop_size(1080, 1920, 9 / 16) == (1080, 1920)
    assert crop_size(1080, 1920, 1.0) == (1080, 1080)


def _track(xs, cuts=()):
    t = FaceTrack()
    for i, x in enumerate(xs):
        t.times.append(i / 4)
        t.faces.append([Face(x, 0.4, 0.1, 0.15, 0.9)] if x is not None else [])
    t.cuts = list(cuts)
    return t


def test_crop_follows_face_and_stays_in_bounds():
    plan = plan_crop(_track([0.75] * 40), src_w=1920, src_h=1080, aspect=9 / 16, duration=10, out_fps=30)
    centre = plan.xs + plan.crop_w / 2
    assert abs(np.median(centre) - 0.75 * 1920) < 60
    assert plan.xs.min() >= 0 and plan.xs.max() <= 1920 - plan.crop_w


def test_small_jitter_does_not_move_camera():
    rng = np.random.default_rng(0)
    xs = list(0.5 + rng.normal(0, 0.01, 60))
    plan = plan_crop(_track(xs), src_w=1920, src_h=1080, aspect=9 / 16, duration=15, out_fps=30)
    assert plan.xs.max() - plan.xs.min() < 40  # dead-zone holds the frame steady


def test_brief_face_glitch_is_ignored():
    xs = [0.3] * 20 + [0.8] * 2 + [0.3] * 20  # 0.5 s blip shouldn't pan the camera
    plan = plan_crop(_track(xs), src_w=1920, src_h=1080, aspect=9 / 16, duration=10.5, out_fps=30)
    assert plan.xs.max() - plan.xs.min() < 80


def test_no_faces_falls_back_to_centre():
    plan = plan_crop(_track([None] * 20), src_w=1920, src_h=1080, aspect=9 / 16, duration=5, out_fps=30)
    assert set(plan.xs.tolist()) == {(1920 - plan.crop_w) // 2}


def test_sendcmd_only_on_change(tmp_path):
    plan = plan_crop(_track([0.5] * 8), src_w=1920, src_h=1080, aspect=9 / 16, duration=2, out_fps=30)
    write_sendcmd(plan, tmp_path / "c.cmd")
    lines = (tmp_path / "c.cmd").read_text().strip().splitlines()
    assert 1 <= len(lines) <= 3 and lines[0].startswith("0.000 crop x ")
