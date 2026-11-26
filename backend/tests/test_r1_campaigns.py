"""R1 campaign revamp: type-first creation, 'what we understood' preview, required caption text,
Clipster rules only for Clipster briefs, clip length following the campaign."""

from clipstudio.campaigns.compliance import ClipFacts, check_clip
from clipstudio.campaigns.models import Brief
from clipstudio.campaigns.post import build_post
from tests.test_api import app_ctx, client, sample_video, upload  # noqa: F401

ONTARIO = """Logo Campaign — $1.50 per 1,000 views, max payout $500
Videos must be 15–60 seconds. Post on Instagram Reels and YouTube Shorts.
Place the provided logo using the LOGO_PRE_PLACED template, do not move or resize.
Disclose the post as paid: add #ad or #sponsored in the caption
Paste this into every caption exactly as written ↓
“Please Play Responsibly, 19+ and physically located in Ontario. Concerned about your Gambling? Call 1-866-531-2600 or visit connexontario.ca”
No background music. Minimum 3% engagement. Keep posts live for 30 days."""
REQ = "Please Play Responsibly, 19+ and physically located in Ontario. Concerned about your Gambling? Call 1-866-531-2600 or visit connexontario.ca"


def test_preview_groups_what_changes_videos(client):  # noqa: F811
    r = client.post("/api/campaigns/preview", json={"type": "logo", "text": ONTARIO}).json()
    b = r["brief"]
    assert b["type"] == "logo" and b["name"] == "Logo Campaign"
    assert (b["min_duration"], b["max_duration"]) == (15, 60)
    assert b["required_caption_text"] == REQ and b["disclosure"]["text"] == "#ad"
    assert b["allow_music"] is False and b["sound_rule"]["mode"] == "none"  # "no background music" isn't a music campaign
    assert b["logo"]["mode"] == "template"
    keys = {i["key"] for i in r["summary"]["video"]}
    assert {"length", "logo", "disclosure", "required_text", "extras", "platforms"} <= keys
    logo = next(i for i in r["summary"]["video"] if i["key"] == "logo")
    assert logo["todo"] and "LOGO_PRE_PLACED" in (logo["quote"] or "")
    refs = {i["key"]: i["value"] for i in r["summary"]["reference"]}
    assert refs["cpm"] == "$1.5 per 1,000 views" and refs["keep_live"] == "30 days" and refs["min_engagement"] == "3%"
    # not a Clipster brief → no Clipster rules
    assert not b["banned_keywords"] and b["source"] == ""


def test_clipster_rules_only_for_clipster_briefs(client):  # noqa: F811
    r = client.post("/api/campaigns/preview", json={"type": "music", "text": "Clipster music campaign. Use the official sound. $2 per 1k views"}).json()
    b = r["brief"]
    assert b["source"] == "Clipster" and "cricket" in b["banned_keywords"] and b["english_only"] and b["min_duration"] == 10.5
    assert b["sound_rule"]["mode"] == "in_app" and b["hook_overlay"]["enabled"] is False


def test_create_by_type_and_caption(client):  # noqa: F811
    c = client.post("/api/campaigns", json={"type": "logo", "text": ONTARIO, "name": "Ontario"}).json()
    assert c["name"] == "Ontario" and c["brief"]["type"] == "logo" and c["ready"] == []
    cap = client.post("/api/campaigns/caption-preview", json={"brief": c["brief"], "platform": "reels"}).json()
    assert cap["caption"].endswith(REQ) and "#ad" in cap["caption"]
    # a custom template without {required_text} still gets it
    cap2 = build_post({**c["brief"], "caption_template": "{hook}"}, {"hook": "Hi"}, "shorts")
    assert cap2["caption"] == f"Hi\n{REQ}"
    # pasting an updated brief keeps the campaign's type
    c2 = client.post(f"/api/campaigns/{c['id']}/apply-text", json={"text": "Music: use the official sound"}).json()
    assert c2["brief"]["type"] == "logo"


def test_required_text_check():
    b = Brief(required_caption_text=REQ)
    ok = {x.id: x.status for x in check_clip(b, ClipFacts(duration=20, rendered=True, stale=False, posts={"reels": f"hook\n{REQ}"}))}
    bad = {x.id: x.status for x in check_clip(b, ClipFacts(duration=20, rendered=True, stale=False, posts={"reels": "hook " + REQ.replace("19+", "18+")}))}
    assert ok["required_text"] == "pass" and bad["required_text"] == "fail"


def test_clip_length_follows_campaign(client, app_ctx, sample_video):  # noqa: F811
    from clipstudio.jobs.pipeline import campaign_length

    c = client.post("/api/campaigns", json={"type": "clipping", "brief": {"min_duration": 45, "max_duration": 90}}).json()
    p = upload(client, sample_video).json()
    client.patch(f"/api/projects/{p['id']}", json={"campaign_id": c["id"]})
    row = app_ctx.db.one("SELECT * FROM projects WHERE id = ?", (p["id"],))
    assert campaign_length(app_ctx, row, 15, 30, {}) == (47.0, 90.0)  # preset 15–30 doesn't fit: campaign range (+ cut margin)
    assert campaign_length(app_ctx, row, 30, 60, {"polish": {"jumpcut": "off"}}) == (45.5, 90.0)  # campaign range wins over the preset
    client.patch(f"/api/projects/{p['id']}", json={"campaign_id": ""})
    row = app_ctx.db.one("SELECT * FROM projects WHERE id = ?", (p["id"],))
    assert campaign_length(app_ctx, row, 15, 30, {}) == (15, 30)


def test_home_todo(client):  # noqa: F811
    client.post("/api/campaigns", json={"type": "logo", "name": "Needs logo"})
    d = client.get("/api/dashboard").json()
    t = d["todo"]
    assert {"review", "render", "failed_renders", "ready", "failed_videos", "not_started", "campaign_setup"} <= t.keys()
    assert any(c["name"] == "Needs logo" and c["missing"] == ["the logo file"] for c in t["campaign_setup"])
    assert d["campaigns"] >= 1


def test_campaign_framing_and_caption_look(client):  # noqa: F811
    c = client.post("/api/campaigns", json={"type": "clipping"}).json()
    assert c["brief"]["layout"] == "" and c["brief"]["caption_preset"] == ""
    c2 = client.patch(f"/api/campaigns/{c['id']}", json={"brief": {"style": "talking", "layout": "stream", "caption_preset": "neon"}}).json()
    assert (c2["brief"]["layout"], c2["brief"]["caption_preset"]) == ("stream", "neon")
    assert client.patch(f"/api/campaigns/{c['id']}", json={"brief": {"layout": "sideways"}}).status_code == 422


def test_campaign_title_text(app_ctx):  # noqa: F811
    from clipstudio.campaigns.service import features

    b = Brief(hook_overlay={"enabled": True, "text": "Wait for {title}"})
    clip = {"title": "the 500x", "edit": {}}
    f = features(app_ctx, {"options": {}}, clip, b)
    assert f["hook_overlay"]["text"] == "Wait for the 500x"
    f2 = features(app_ctx, {"options": {}}, {"title": "x", "edit": {"hook_overlay": {"text": "My own"}}}, b)
    assert f2["hook_overlay"]["text"] == "My own"  # a clip's own text wins
    assert features(app_ctx, {"options": {}}, clip, Brief())["hook_overlay"]["text"] == "the 500x"


def test_campaign_style_applies_server_side(client, app_ctx, sample_video):  # noqa: F811
    from clipstudio.jobs.pipeline import project_options, with_campaign

    c = client.post("/api/campaigns", json={"type": "clipping", "brief": {"style": "clean", "layout": "blur", "max_clips": 5}}).json()
    p = upload(client, sample_video).json()
    client.patch(f"/api/projects/{p['id']}", json={"campaign_id": c["id"]})
    row = app_ctx.db.one("SELECT * FROM projects WHERE id = ?", (p["id"],))
    o = project_options(app_ctx.settings.get(), with_campaign(app_ctx, row))
    assert (o["caption_preset"], o["layout"], o["max_clips"], o["hook_overlay"], o["polish"]["zoom"]) == ("minimal", "blur", 5, False, "off")
    app_ctx.db.update("projects", p["id"], {"options": {"caption_preset": "neon"}})  # the video's own choice wins
    row = app_ctx.db.one("SELECT * FROM projects WHERE id = ?", (p["id"],))
    assert project_options(app_ctx.settings.get(), with_campaign(app_ctx, row))["caption_preset"] == "neon"
