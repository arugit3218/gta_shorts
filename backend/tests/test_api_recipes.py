"""Phase 4A: polish settings flow through projects/clips; recipes CRUD + apply (synthetic media)."""

from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload, wait_job  # noqa: F401


def test_polish_and_recipes(client, app_ctx, sample_video):  # noqa: F811
    # app defaults include polish
    s = client.get("/api/settings").json()
    assert s["polish"]["jumpcut"] == "light" and s["polish"]["zoom"] == "subtle"

    presets = client.get("/api/caption-presets").json()
    assert {"hormozi", "beast", "hype", "neon", "boxed"} <= set(presets)

    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    opts = {"auto_render": False, "polish": {"jumpcut": "tight", "progress": "bottom", "cold_open": False}}
    job = client.post(f"/api/projects/{p['id']}/process", json={"options": opts}).json()["job_id"]
    assert wait_job(client, job, timeout=240)["status"] == "done"
    clips = client.get(f"/api/projects/{p['id']}/clips").json()
    assert clips
    c0 = clips[0]
    pol = c0["features"]["polish"]
    assert pol["jumpcut"] == "tight" and pol["progress"] == "bottom" and pol["zoom"] == "subtle"

    # per-clip partial polish edits merge (don't wipe other fields)
    r = client.patch(f"/api/clips/{c0['id']}", json={"edit": {"polish": {"emoji": True}}})
    assert r.status_code == 200, r.text
    r = client.patch(f"/api/clips/{c0['id']}", json={"edit": {"polish": {"profanity": "beep"}, "hook_overlay": {"enabled": True, "style": "banner"}}})
    f = r.json()["features"]["polish"]
    assert f["emoji"] is True and f["profanity"] == "beep" and f["jumpcut"] == "tight" and f["hook_style"] == "banner"
    bad = client.patch(f"/api/clips/{c0['id']}", json={"edit": {"polish": {"zoom": "wild"}}})
    assert bad.status_code == 422

    # recipes: create, list, update, apply to one clip, bulk apply, save from clip, delete
    r = client.post("/api/recipes", json={"name": "Podcast punchy", "caption_preset": "hormozi", "layout": "center", "polish": {"zoom": "punchy", "emoji": True, "jumpcut": "normal"}})
    assert r.status_code == 200, r.text
    rec = r.json()
    assert rec["polish"] == {"zoom": "punchy", "emoji": True, "jumpcut": "normal"}
    assert client.post("/api/recipes", json={"name": "x", "caption_preset": "nope"}).status_code in (400, 422)
    assert [x["name"] for x in client.get("/api/recipes").json()] == ["Podcast punchy"]
    r = client.put(f"/api/recipes/{rec['id']}", json={"name": "Podcast punchy", "caption_preset": "beast", "polish": {"zoom": "punchy"}})
    assert r.json()["caption_preset"] == "beast" and "emoji" not in r.json()["polish"]

    r = client.post(f"/api/clips/{c0['id']}/apply-recipe", json={"recipe_id": rec["id"]})
    assert r.status_code == 200, r.text
    d = r.json()
    assert d["edit"]["caption_preset"] == "beast" and d["features"]["polish"]["zoom"] == "punchy"
    assert d["features"]["polish"]["emoji"] is True  # kept from the earlier clip edit

    r = client.post(f"/api/projects/{p['id']}/clips/bulk", json={"recipe_id": rec["id"], "render": False, "scope": "all"})
    assert r.json()["updated"] == len(clips)
    for cl in client.get(f"/api/projects/{p['id']}/clips").json():
        assert cl["edit"]["caption_preset"] == "beast" and cl["features"]["polish"]["zoom"] == "punchy"

    r = client.post(f"/api/clips/{c0['id']}/save-recipe", json={"name": "From clip"})
    saved = r.json()
    assert saved["caption_preset"] == "beast" and saved["polish"]["profanity"] == "beep"
    assert client.delete(f"/api/recipes/{saved['id']}").json()["deleted"]
    assert client.get(f"/api/recipes/{saved['id']}").status_code in (404, 405)

    # a recipe used when creating a project (caption style + polish travel through the options)
    p2 = upload(client, sample_video, name="talk2.mp4").json()
    job = client.post(f"/api/projects/{p2['id']}/process", json={"options": {"auto_render": False, "caption_preset": "neon", "caption_style": {"emphasis_color": "#FF0000"}, "polish": {"zoom": "off"}}}).json()["job_id"]
    assert wait_job(client, job, timeout=240)["status"] == "done"
    c = client.get(f"/api/projects/{p2['id']}/clips").json()[0]
    assert c["edit"]["caption_style"] == {"emphasis_color": "#FF0000"} and c["features"]["polish"]["zoom"] == "off"


def test_render_with_polish_and_cold_open(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    opts = {"auto_render": False, "polish": {"jumpcut": "normal", "zoom": "punchy", "progress": "top", "cold_open": True, "emoji": True}}
    job = client.post(f"/api/projects/{p['id']}/process", json={"options": opts}).json()["job_id"]
    assert wait_job(client, job, timeout=240)["status"] == "done"
    c0 = client.get(f"/api/projects/{p['id']}/clips").json()[0]
    job = client.post(f"/api/clips/{c0['id']}/render").json()["job_id"]
    j = wait_job(client, job, timeout=300)
    assert j["status"] == "done", j
    d = client.get(f"/api/clips/{c0['id']}").json()
    a = d["analysis"]
    assert d["render_status"] == "done" and "polish_used" in a and a["output_duration"] > 0
    src_len = c0["end"] - c0["start"]
    assert a["polish_used"].get("cold_open"), a["polish_used"]  # clips here are long enough for a teaser
    ta, tb = a["polish_used"]["cold_open"]
    assert abs(a["output_duration"] - (tb - ta) - (src_len - a["polish_used"]["removed"])) < 0.5
    assert a["sfx_used"] is not None
