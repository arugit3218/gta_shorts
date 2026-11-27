"""Round 5 (ideas adapted from nero-harsh/clipstudio): quality check, two-pass loudness, no weak padding clip,
post stats, captions without libass."""

import subprocess

import numpy as np
import pytest

from clipstudio.campaigns import posting
from clipstudio.errors import MediaError
from clipstudio.media import caption_png, qa
from clipstudio.media import ffmpeg as ff
from clipstudio.media.captions import build_ass, resolve_style
from clipstudio.media.overlays import DisclosureText, EndText, HookText, ProgressBar, build_overlay_ass
from clipstudio.media.render import loudnorm_filter, render_clip, render_preview_frame
from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload  # noqa: F401
from tests.test_api_review import _process
from tests.test_polish import DUR, _spec, _words, talk_video  # noqa: F401


def _make(path, vf: str, af: str | None, dur: float = 4.0):
    cmd = [ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", vf]
    if af:
        cmd += ["-f", "lavfi", "-i", af, "-c:a", "aac"]
    cmd += ["-t", f"{dur}", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", str(path)]
    subprocess.run(cmd, check=True)
    return path


# ----------------------------------------------------------------------------- 2 quality check
def test_quality_check_finds_problems(tmp_path):
    black = _make(tmp_path / "black.mp4", "color=c=black:s=320x568:r=30", None, 3.0)
    r = qa.check(black, has_audio=True)
    groups = {(i["group"], i["level"]) for i in r["issues"]}
    assert ("sound", "fail") in groups  # no audio track at all = silent
    assert ("picture", "fail") in groups  # black from the very start
    assert not r["ok"]
    # rapid full-frame flashing (white/black every 2 frames at 30 fps)
    flash = _make(tmp_path / "flash.mp4", "color=c=black:s=320x568:r=30,geq=lum='if(lt(mod(N\\,4)\\,2)\\,235\\,16)':cb=128:cr=128", "sine=f=440", 2.0)
    assert any(i["group"] == "flashing" for i in qa.check(flash)["issues"])
    assert qa.check(black, has_audio=False)["issues"][0]["group"] == "picture"  # silence on purpose isn't reported
    quiet = _make(tmp_path / "quiet.mp4", "testsrc2=s=320x568:r=30", "anullsrc=r=48000:cl=stereo", 2.0)
    assert any(i["group"] == "sound" and i["level"] == "fail" for i in qa.check(quiet)["issues"])  # digital silence = silent
    loud = _make(tmp_path / "loud.mp4", "testsrc2=s=320x568:r=30", "sine=f=440,volume=8", 2.0)
    assert any(i["group"] == "sound" for i in qa.check(loud)["issues"])
    assert not any(i["group"] == "sound" for i in qa.check(loud, song=True)["issues"])  # a mastered song is loud on purpose


def test_render_lands_on_target_loudness_and_passes_qa(talk_video, tmp_path):  # noqa: F811
    res = render_clip(_spec(talk_video), tmp_path / "loud.mp4")
    r = qa.check(res.path)
    assert r["lufs"] is not None and abs(r["lufs"] - (-14.0)) < 0.7  # two-pass loudness lands on the target
    assert not [i for i in r["issues"] if i["group"] == "sound"]


def test_loudnorm_filter_falls_back():
    assert loudnorm_filter(None) == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert loudnorm_filter({"input_i": "-inf", "input_tp": "-inf", "input_lra": "0", "input_thresh": "-inf", "target_offset": "0"}) == "loudnorm=I=-14:TP=-1.5:LRA=11"
    assert "linear=true" in loudnorm_filter({"input_i": "-22", "input_tp": "-4", "input_lra": "6", "input_thresh": "-32", "target_offset": "0.3"})


def test_quality_checks_show_with_the_clip(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    pid = upload(client, sample_video).json()["id"]
    _process(client, pid)
    a = client.post(f"/api/projects/{pid}/clips", json={"start": 5.0, "end": 12.0, "render": False}).json()
    row = app_ctx.db.one("SELECT * FROM clips WHERE id = ?", (a["id"],))
    fake = app_ctx.paths.project_dir(pid) / "q.mp4"
    _make(fake, "testsrc2=s=320x568:r=30", "sine=f=300", 3.0)
    app_ctx.db.update("clips", a["id"], {"render_status": "done", "render_path": str(fake), "analysis": row["analysis"] | {"qa": qa.check(fake)}})
    checks = {c["id"]: c for c in client.get(f"/api/clips/{a['id']}/compliance").json()["checks"]}
    assert {"qa_sound", "qa_picture", "qa_flashing"} <= checks.keys()
    assert checks["qa_picture"]["status"] == "pass"


# ----------------------------------------------------------------------------- 5 no weak padding clip
def test_no_clip_when_nothing_clears_the_minimum(client, app_ctx, sample_video):  # noqa: F811
    from clipstudio.analysis.highlights import few_reason

    assert "no weak clip was made" in few_reason(0, 5, 45)
    seed_transcript(app_ctx, sample_video)
    client.put("/api/settings", json={"min_score": 100})
    pid = upload(client, sample_video).json()["id"]
    job = client.post(f"/api/projects/{pid}/process", json={"options": {"auto_render": False}}).json()["job_id"]
    from tests.test_api import wait_job

    j = wait_job(client, job, timeout=240)
    assert j["status"] == "done", j  # finished, not failed
    p = client.get(f"/api/projects/{pid}").json()
    assert p["status"] == "ready" and p["clip_count"] == 0
    assert "no weak clip" in (j["stages"]["score"]["message"] or "")
    assert any(v["id"] == pid for v in client.get("/api/dashboard").json()["todo"]["no_clips"])  # shows on Home
    # re-processing with a stricter minimum keeps the clips a video already has
    client.put("/api/settings", json={"min_score": 0})
    before = {c["id"] for c in _process(client, pid)}
    assert before
    client.put("/api/settings", json={"min_score": 100})
    after = {c["id"] for c in _process(client, pid)}
    assert after == before
    client.put("/api/settings", json={"min_score": 45})


# ----------------------------------------------------------------------------- 3 post stats
def test_post_stats_refresh(client, app_ctx, sample_video, monkeypatch):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    camp = client.post("/api/campaigns", json={"type": "clipping", "name": "Stats"}).json()
    pid = upload(client, sample_video).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"campaign_id": camp["id"]})
    _process(client, pid)
    a = client.post(f"/api/projects/{pid}/clips", json={"start": 5.0, "end": 12.0, "render": False}).json()
    assert client.post(f"/api/clips/{a['id']}/posts/stats").status_code == 422  # no link yet
    client.put(f"/api/clips/{a['id']}/posts/shorts", json={"url": "https://youtube.com/shorts/abcdefghijk"})
    client.put(f"/api/clips/{a['id']}/posts/reels", json={"url": "https://www.instagram.com/reel/XYZ"})

    def fake(url):
        if "instagram" in url:
            raise MediaError("Instagram didn't share the numbers (it often needs a login). Check them in the app.")
        return {"views": 12400, "likes": 800, "comments": 31}

    monkeypatch.setattr("clipstudio.media.download.post_stats", fake)
    r = client.post(f"/api/clips/{a['id']}/posts/stats").json()
    assert (r["updated"], r["failed"]) == (1, 1)
    assert r["posted"]["shorts"]["views"] == 12400 and r["posted"]["reels"]["stats_error"].startswith("Instagram")
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    assert d["post_stats"]["views"] == 12400 and d["post_stats"]["counted"] == 1 and d["post_stats"]["links"] == 2
    assert client.post(f"/api/campaigns/{camp['id']}/posts/stats").json() == {"updated": 1, "failed": 1}
    # a failed refresh keeps the last good numbers; a new link clears them
    monkeypatch.setattr("clipstudio.media.download.post_stats", lambda url: (_ for _ in ()).throw(MediaError("offline")))
    client.post(f"/api/clips/{a['id']}/posts/stats")
    sh = client.get(f"/api/clips/{a['id']}").json()["posted"]["shorts"]
    assert sh["views"] == 12400 and sh["stats_error"] == "offline"
    client.put(f"/api/clips/{a['id']}/posts/shorts", json={"url": "https://youtube.com/shorts/zzzzzzzzzzz"})
    assert "views" not in client.get(f"/api/clips/{a['id']}").json()["posted"]["shorts"]
    assert posting.refresh_stats(app_ctx.db, [{"clip_id": a["id"], "platform": "shorts", "url": None}]) == {"updated": 0, "failed": 0}


# ----------------------------------------------------------------------------- 1 captions without libass
def test_parse_our_ass():
    style = resolve_style("hormozi")
    ass = build_ass(_words(), style, width=540, height=960, emphasis={2})
    w, h, events = caption_png.parse_ass(ass)
    assert (w, h) == (540, 960) and events
    assert all(e.pos == (270, round(style.position * 960)) for e in events)
    assert any(seg.scale > 1.0 for e in events for ln in e.lines for seg in ln)  # the emphasised word is larger
    ov = build_overlay_ass(540, 960, hook=HookText("Nobody expected this", 2.0), disclosure=DisclosureText("#ad", "top_left"), clip_duration=8.0, progress=ProgressBar("bottom"), end_text=EndText("Follow for part 2", 2.0))
    _, _, oe = caption_png.parse_ass(ov)
    bars = [e for e in oe if e.bar]
    assert len(bars) == 2 and any("x1" in e.bar for e in bars)
    texts = [" ".join(s.text for ln in e.lines for s in ln) for e in oe if not e.bar]
    assert any("NOBODY" in t for t in texts) and any("FOLLOW" in t for t in texts) and any("#ad" in t for t in texts)
    assert caption_png.find_font("Montserrat ExtraBold", False).endswith("Montserrat-ExtraBold.ttf")
    assert caption_png.find_font("Anton", False).endswith("Anton-Regular.ttf")
    assert "Montserrat" in caption_png.find_font("Montserrat", True)  # same family under another file name
    _, _, emo = caption_png.parse_ass(build_overlay_ass(540, 960, hook=HookText("Wait for it 👀", 2.0), disclosure=None, clip_duration=4.0))
    assert "👀" not in "".join(s.text for e in emo for ln in e.lines for s in ln)  # no empty boxes


def _region_luma(path, t, y0, y1):
    raw = subprocess.run([ff.FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-vf", "scale=180:320,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype=np.uint8).reshape(320, 180)
    return a[int(y0 * 320) : int(y1 * 320)]


@pytest.mark.parametrize("backend", ["images", "libass"])
def test_render_with_captions_both_ways(talk_video, tmp_path, monkeypatch, backend):  # noqa: F811
    monkeypatch.setenv("CLIPSTUDIO_CAPTIONS", backend)
    spec = _spec(
        talk_video,
        style=resolve_style("hormozi"),
        emphasis=True,
        progress="bottom",
        hook=HookText("Nobody expected this", 2.0, style="banner"),
        end_text=EndText("Follow for part 2", 2.0, look="card"),
        disclosure=DisclosureText("#ad", "top_left"),
        jumpcut="normal",
    )
    res = render_clip(spec, tmp_path / f"{backend}.mp4")
    assert ff.probe(res.path).duration > 3
    plain = render_clip(_spec(talk_video, captions=False), tmp_path / f"plain_{backend}.mp4")
    pos = resolve_style("hormozi").position
    # captions are drawn: the caption band differs from the same frame without captions
    t = 1.0
    diff = np.abs(_region_luma(res.path, t, pos - 0.05, pos + 0.05).astype(int) - _region_luma(plain.path, t, pos - 0.05, pos + 0.05).astype(int)).mean()
    assert diff > 4, diff
    out = render_preview_frame(spec, 1.0, tmp_path / f"p_{backend}.jpg")
    assert out.stat().st_size > 1000


def test_backend_choice(monkeypatch):
    monkeypatch.setenv("CLIPSTUDIO_CAPTIONS", "images")
    assert caption_png.use_images() and caption_png.backend_name() == "images"
    monkeypatch.setenv("CLIPSTUDIO_CAPTIONS", "libass")
    assert not caption_png.use_images()
    monkeypatch.delenv("CLIPSTUDIO_CAPTIONS")
    assert caption_png.use_images() is (not caption_png._ffmpeg_has_libass(ff.FFMPEG))
