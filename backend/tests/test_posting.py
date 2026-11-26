"""Mark as posted + the campaign post queue."""

from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload  # noqa: F401
from tests.test_api_review import _process


def _setup(client, app_ctx, sample_video, platforms):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    camp = client.post("/api/campaigns", json={"type": "clipping", "name": "Post test", "brief": {"platforms": platforms, "required_hashtags": ["#brand"]}}).json()
    pid = upload(client, sample_video).json()["id"]
    client.patch(f"/api/projects/{pid}", json={"campaign_id": camp["id"]})
    _process(client, pid)
    a = client.post(f"/api/projects/{pid}/clips", json={"start": 5.0, "end": 12.0, "render": False}).json()
    b = client.post(f"/api/projects/{pid}/clips", json={"start": 20.0, "end": 27.0, "render": False}).json()
    client.post(f"/api/projects/{pid}/clips/actions", json={"ids": [a["id"], b["id"]], "action": "approve"})
    for x in (a, b):  # pretend they're rendered (rendering is covered elsewhere and slow)
        app_ctx.db.update("clips", x["id"], {"render_status": "done"})
    return camp, pid, a, b


def test_mark_posted_and_queue(client, app_ctx, sample_video):  # noqa: F811
    camp, pid, a, b = _setup(client, app_ctx, sample_video, ["reels", "shorts"])
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    assert d["post_platforms"] == ["reels", "shorts"]
    assert d["ready_total"] == 2 and d["posted_total"] == 0
    row = next(r for r in d["ready"] if r["id"] == a["id"])
    assert set(row["captions"]) == {"reels", "shorts"} and "#brand" in row["captions"]["reels"]["caption"]
    assert row["posted"] == {}

    # posted on Instagram only: still in the queue (YouTube to go)
    r = client.put(f"/api/clips/{a['id']}/posts/reels", json={"url": "https://instagram.com/reel/abc"})
    assert r.status_code == 200 and r.json()["posted"]["reels"]["url"] == "https://instagram.com/reel/abc"
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    assert d["ready_total"] == 2 and next(r for r in d["ready"] if r["id"] == a["id"])["posted"].keys() == {"reels"}

    # and on YouTube (no link): done, moves to posted
    assert client.put(f"/api/clips/{a['id']}/posts/shorts", json={}).status_code == 200
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    assert d["ready_total"] == 1 and d["posted_total"] == 1 and d["posted_clips"][0]["id"] == a["id"]
    assert client.get(f"/api/clips/{a['id']}").json()["posted"].keys() == {"reels", "shorts"}
    todo = client.get("/api/dashboard").json()["todo"]["ready"]
    assert next(t for t in todo if t["campaign_id"] == camp["id"])["n"] == 1

    # editing the link keeps the original time; undo puts it back in the queue
    at = client.get(f"/api/clips/{a['id']}").json()["posted"]["reels"]["at"]
    p2 = client.put(f"/api/clips/{a['id']}/posts/reels", json={"url": "https://instagram.com/reel/xyz"}).json()["posted"]["reels"]
    assert p2["url"].endswith("xyz") and p2["at"] == at
    client.delete(f"/api/clips/{a['id']}/posts/shorts")
    assert client.get(f"/api/campaigns/{camp['id']}").json()["ready_total"] == 2

    # bad input
    assert client.put(f"/api/clips/{a['id']}/posts/tiktok", json={}).status_code == 422
    assert client.put(f"/api/clips/{a['id']}/posts/reels", json={"url": "instagram.com/x"}).status_code == 422

    # outdated clips posted everywhere aren't offered for re-render
    client.put(f"/api/clips/{a['id']}/posts/shorts", json={})
    fake = app_ctx.paths.project_dir(pid) / "fake_render.mp4"
    fake.write_bytes(b"x")  # stands in for the clip's (now outdated) render
    for x in (a, b):
        app_ctx.db.update("clips", x["id"], {"render_status": "stale", "render_path": str(fake)})
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    assert d["outdated"] == 1 and d["posted_total"] == 1  # still listed as posted while stale
    assert d["posted_clips"][0]["has_video"]  # its (older) file can still be downloaded
    assert client.post(f"/api/campaigns/{camp['id']}/rerender-outdated").json()["rendering"] == 1


def test_one_platform_campaign_and_reprocess_keeps_posted(client, app_ctx, sample_video):  # noqa: F811
    camp, pid, a, b = _setup(client, app_ctx, sample_video, ["reels"])
    row = client.get(f"/api/campaigns/{camp['id']}").json()["ready"][0]
    assert set(row["captions"]) == {"reels"}
    client.put(f"/api/clips/{a['id']}/posts/reels", json={})
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    assert d["posted_total"] == 1 and d["ready_total"] == 1  # one platform is enough here

    # re-processing (without "keep clips") never deletes a posted clip
    clips = _process(client, pid)
    after = {c["id"] for c in clips}
    assert a["id"] in after and b["id"] not in after
    ranks = [c["rank"] for c in clips]
    assert len(ranks) == len(set(ranks))  # new clips are numbered after the kept one
    assert client.get(f"/api/clips/{a['id']}").json()["posted"].keys() == {"reels"}


def test_archived_campaign_not_on_home(client, app_ctx, sample_video):  # noqa: F811
    camp, pid, a, b = _setup(client, app_ctx, sample_video, ["reels"])
    ready = lambda: [t for t in client.get("/api/dashboard").json()["todo"]["ready"] if t["campaign_id"] == camp["id"]]  # noqa: E731
    assert ready() and ready()[0]["n"] == 2
    client.patch(f"/api/campaigns/{camp['id']}", json={"status": "archived"})
    assert not ready()

    # partly posted + outdated stays in To post (flagged), not lost between the two lists
    client.patch(f"/api/campaigns/{camp['id']}", json={"status": "active", "brief": {"platforms": ["reels", "shorts"]}})
    client.put(f"/api/clips/{a['id']}/posts/reels", json={})
    app_ctx.db.update("clips", a["id"], {"render_status": "stale"})
    d = client.get(f"/api/campaigns/{camp['id']}").json()
    row = next(r for r in d["ready"] if r["id"] == a["id"])
    assert row["render_status"] == "stale" and row["posted"].keys() == {"reels"}
