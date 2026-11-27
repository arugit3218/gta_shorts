"""Round 4: same-link detection (#4), captions clear of the logo (#3), end text (#2)."""

import subprocess

import numpy as np

from clipstudio.campaigns import text_layout as tl
from clipstudio.campaigns.compliance import ClipFacts, check_clip
from clipstudio.campaigns.models import Brief
from clipstudio.campaigns.parser import parse_brief
from clipstudio.campaigns.safezones import GRID_H, GRID_W
from clipstudio.media import ffmpeg as ff
from clipstudio.media.captions import resolve_style
from clipstudio.media.download import same_video_key
from clipstudio.media.overlays import EndText, HookText, build_overlay_ass
from clipstudio.media.render import render_clip, render_preview_frame
from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload  # noqa: F401
from tests.test_api_review import _process
from tests.test_polish import DUR, _spec, talk_video  # noqa: F401


# ----------------------------------------------------------------------------- #4 same link
def test_same_video_key_variants():
    yt = {same_video_key(u) for u in ("https://youtu.be/dQw4w9WgXcQ?si=x", "https://www.youtube.com/watch?v=dQw4w9WgXcQ&t=30s", "https://m.youtube.com/shorts/dQw4w9WgXcQ", "https://youtube.com/live/dQw4w9WgXcQ")}
    assert yt == {"youtube:dQw4w9WgXcQ"}
    assert same_video_key("https://www.twitch.tv/videos/123/?utm_source=a") == same_video_key("https://twitch.tv/videos/123")
    assert same_video_key("https://kick.com/x?clip=1") != same_video_key("https://kick.com/x?clip=2")


def test_link_already_in_campaign_is_not_added_twice(client, app_ctx, monkeypatch):  # noqa: F811
    from clipstudio.jobs.worker import Worker
    from clipstudio.media import download as dl

    monkeypatch.setattr(dl, "validate_url", lambda u: u.strip())  # no DNS lookups in tests
    monkeypatch.setattr(Worker, "enqueue", lambda self, *a, **k: "job")  # and no downloads
    camp = client.post("/api/campaigns", json={"type": "clipping", "name": "Dup test"}).json()
    other = client.post("/api/campaigns", json={"type": "clipping", "name": "Other"}).json()
    r = client.post("/api/projects/bulk", json={"urls": ["https://youtu.be/dQw4w9WgXcQ"], "campaign_id": camp["id"]}).json()
    assert len(r["created"]) == 1 and r["existing"] == []
    # the same video, spelled differently, from the campaign page again → reported, not added
    r2 = client.post("/api/projects/bulk", json={"urls": ["https://www.youtube.com/watch?v=dQw4w9WgXcQ&si=abc"], "campaign_id": camp["id"]}).json()
    assert r2["created"] == [] and r2["existing"][0]["project"]["id"] == r["created"][0]["id"]
    # Create → Clips with the campaign picked: refused unless the user asks for a second copy
    body = {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ", "campaign_id": camp["id"]}
    assert client.post("/api/projects", json=body).status_code == 422
    assert client.post("/api/projects", json=body | {"allow_duplicate": True}).status_code == 200
    # another campaign may use the same video
    assert client.post("/api/projects", json=body | {"campaign_id": other["id"]}).status_code == 200
    assert len(client.get(f"/api/campaigns/{camp['id']}").json()["project_list"]) == 2


# ----------------------------------------------------------------------------- #3 captions vs logo
def _mask(y0, y1, x0=0.3, x1=0.7):
    m = np.zeros((GRID_H, GRID_W), dtype=bool)
    m[int(y0 * GRID_H) : int(y1 * GRID_H), int(x0 * GRID_W) : int(x1 * GRID_W)] = True
    return m


def test_captions_move_clear_of_a_logo(monkeypatch):
    style = resolve_style("bold")  # centre 0.70
    monkeypatch.setattr(tl, "logo_mask", lambda logo: _mask(0.64, 0.76))
    out = tl.layout(logo={"file": "x"}, caption_style=style, caption_set_by_clip=False, captions_on=True, hook_text="Nobody expected this", end_text="Follow for part 2")
    h = tl.caption_height(style)
    assert out["caption_moved"] and out["caption_logo_overlap"] == 0
    y = out["caption_position"]
    assert y + h / 2 <= 0.64 or y - h / 2 >= 0.76  # above or below the logo
    assert abs(y - 0.70) < 0.25  # the nearest free spot, not across the screen
    # a position the user set on the clip is kept; the overlap is reported instead
    mine = tl.layout(logo={"file": "x"}, caption_style=style, caption_set_by_clip=True, captions_on=True, hook_text=None)
    assert mine["caption_position"] == 0.70 and not mine["caption_moved"] and mine["caption_logo_overlap"] > 0.5
    # a logo at the top pushes the title text down instead
    monkeypatch.setattr(tl, "logo_mask", lambda logo: _mask(0.15, 0.27))
    top = tl.layout(logo={"file": "x"}, caption_style=style, caption_set_by_clip=False, captions_on=True, hook_text="Nobody expected this")
    assert top["hook_moved"] and not top["caption_moved"] and top["hook_y"] > 0.27
    # a logo in a corner doesn't touch centred captions
    monkeypatch.setattr(tl, "logo_mask", lambda logo: _mask(0.64, 0.76, 0.85, 0.98))
    assert not tl.layout(logo={"file": "x"}, caption_style=style, caption_set_by_clip=False, captions_on=True, hook_text=None)["caption_moved"]


def test_caption_logo_check():
    b = Brief(type="logo", logo={"mode": "free", "file": "/x.png"})
    base = dict(duration=20, rendered=True, stale=False, width=1080, height=1920, caption_text="hello there", logo_enabled=True, logo_mask=_mask(0.6, 0.8))
    by = {c.id: c for c in check_clip(b, ClipFacts(**base, caption_logo_overlap=0.6))}
    assert by["caption_logo"].status == "fail"
    by = {c.id: c for c in check_clip(b, ClipFacts(**base, caption_logo_overlap=0.0))}
    assert by["caption_logo"].status == "pass"


def test_campaign_caption_position_and_bulk(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    camp = client.post("/api/campaigns", json={"type": "clipping", "brief": {"caption_position": 0.5}}).json()
    assert camp["brief"]["caption_position"] == 0.5
    assert client.patch(f"/api/campaigns/{camp['id']}", json={"brief": {"caption_position": 0.99}}).status_code == 422
    pid = upload(client, sample_video).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"campaign_id": camp["id"]})
    _process(client, pid)
    a = client.post(f"/api/projects/{pid}/clips", json={"start": 5.0, "end": 12.0, "render": False}).json()
    lay = client.get(f"/api/clips/{a['id']}").json()["features"]["text_layout"]
    assert lay["caption_source"] == "campaign" and lay["caption_position"] == 0.5
    # selected clips: own height, then back to automatic
    r = client.post(f"/api/projects/{pid}/clips/bulk", json={"ids": [a["id"]], "caption_position": 0.3, "render": False}).json()
    assert r["updated"] == 1
    lay = client.get(f"/api/clips/{a['id']}").json()["features"]["text_layout"]
    assert lay["caption_source"] == "clip" and lay["caption_position"] == 0.3
    assert client.post(f"/api/projects/{pid}/clips/bulk", json={"ids": [a["id"]], "caption_position": 0.3, "render": False}).json()["updated"] == 0  # no change, no re-render
    client.post(f"/api/projects/{pid}/clips/bulk", json={"ids": [a["id"]], "caption_position_auto": True, "render": False})
    assert client.get(f"/api/clips/{a['id']}").json()["features"]["text_layout"]["caption_source"] == "campaign"


# ----------------------------------------------------------------------------- #2 end text
def test_end_text_from_brief_and_features(client, app_ctx, sample_video):  # noqa: F811
    p, found = parse_brief('Clip rules\n- End screen: "Download Stake now"\n- no music')
    assert p["end_text"] == {"enabled": True, "text": "Download Stake now"}
    p2, found2 = parse_brief("End each video with a CTA")
    assert "end_text" not in p2 and any("End screen mentioned" in f for f in found2)

    seed_transcript(app_ctx, sample_video)
    camp = client.post("/api/campaigns", json={"type": "clipping", "brief": {"end_text": {"enabled": True, "text": "Part 2: {title}", "duration": 3}}}).json()
    pid = upload(client, sample_video).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"campaign_id": camp["id"]})
    _process(client, pid)
    a = client.post(f"/api/projects/{pid}/clips", json={"start": 5.0, "end": 12.0, "render": False}).json()
    client.patch(f"/api/clips/{a['id']}", json={"title": "the comeback"})
    f = client.get(f"/api/clips/{a['id']}").json()["features"]["end_text"]
    assert f["enabled"] and f["text"] == "Part 2: the comeback" and f["required"] and f["duration"] == 3
    # turned off on the clip → the check fails; the clip's own words win over the campaign's
    client.patch(f"/api/clips/{a['id']}", json={"edit": {"end_text": {"enabled": False}}})
    checks = {c["id"]: c for c in client.get(f"/api/clips/{a['id']}/compliance").json()["checks"]}
    assert checks["end_text"]["status"] == "fail"
    client.patch(f"/api/clips/{a['id']}", json={"edit": {"end_text": {"enabled": True, "text": "Link in bio", "look": "card"}}})
    f = client.get(f"/api/clips/{a['id']}").json()["features"]["end_text"]
    assert (f["text"], f["look"], f["duration"]) == ("Link in bio", "card", 3)  # duration still from the campaign
    checks = {c["id"]: c for c in client.get(f"/api/clips/{a['id']}/compliance").json()["checks"]}
    assert checks["end_text"]["status"] == "pass"


def test_end_text_overlay_timing():
    both = build_overlay_ass(1080, 1920, hook=HookText("Wait for it", 3.0), disclosure=None, clip_duration=20.0, end_text=EndText("Follow", 2.0))
    assert "WAIT FOR IT" in both and "FOLLOW" in both
    ass = build_overlay_ass(1080, 1920, hook=None, disclosure=None, clip_duration=20.0, end_text=EndText("Follow for part 2", 2.5))
    line = next(x for x in ass.splitlines() if "FOLLOW" in x)
    assert line.startswith("Dialogue: 2,0:00:17.50,")
    short = build_overlay_ass(1080, 1920, hook=None, disclosure=None, clip_duration=1.5, end_text=EndText("x y", 4))
    assert any(x.startswith("Dialogue: 2,0:00:00.00,") for x in short.splitlines())  # never before 0


def _luma(path, t):
    raw = subprocess.run([ff.FFMPEG, "-v", "error", "-ss", f"{t}", "-i", str(path), "-frames:v", "1", "-vf", "scale=32:18,format=gray", "-f", "rawvideo", "-"], capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.uint8).mean()


def test_render_end_card(talk_video, tmp_path):  # noqa: F811
    # with the title text on too: both overlays in one render
    over = render_clip(_spec(talk_video, hook=HookText("Nobody expected this", 2.0), end_text=EndText("Follow for part 2", 2.0, look="overlay")), tmp_path / "over.mp4")
    card = render_clip(_spec(talk_video, end_text=EndText("Follow for part 2", 2.0, look="card"), jumpcut="normal"), tmp_path / "card.mp4")
    d = ff.probe(card.path).duration
    assert abs(ff.probe(over.path).duration - DUR) < 0.3  # the clip keeps its length
    assert _luma(card.path, d - 0.6) < _luma(card.path, 1.0) * 0.8  # dimmed behind the text at the end
    out = render_preview_frame(_spec(talk_video, end_text=EndText("Follow", 2.0, look="card")), DUR - 1.0, tmp_path / "p.jpg")
    assert out.exists() and out.stat().st_size > 1000
