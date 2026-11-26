"""UI-1 / UI-2 backend: re-process keeping clips, bulk clip actions, zip download, source preview info."""

import io
import zipfile

from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload, wait_job  # noqa: F401


def _process(client, pid, **body):
    job = client.post(f"/api/projects/{pid}/process", json={"options": {"auto_render": False}, **body}).json()["job_id"]
    j = wait_job(client, job, timeout=240)
    assert j["status"] == "done", j
    return client.get(f"/api/projects/{pid}/clips").json()


def test_review_flow(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    pid = p["id"]
    clips = _process(client, pid)
    assert clips
    auto = clips[0]
    # a hand-made clip that will be approved (ids[0]) next to the auto clip (ids[1])
    m1 = client.post(f"/api/projects/{pid}/clips", json={"start": 20.0, "end": 27.0, "render": False}).json()
    ids = [m1["id"], auto["id"]]
    clips = [m1, auto]

    # bulk approve / reject / unreview
    r = client.post(f"/api/projects/{pid}/clips/actions", json={"ids": ids[:2], "action": "approve"}).json()
    assert r["updated"] == 2
    st = {c["id"]: c["status"] for c in client.get(f"/api/projects/{pid}/clips").json()}
    assert st[ids[0]] == st[ids[1]] == "approved"
    client.post(f"/api/projects/{pid}/clips/actions", json={"ids": [ids[1]], "action": "unreview"})
    assert client.get(f"/api/clips/{ids[1]}").json()["status"] == "candidate"
    assert client.post(f"/api/projects/{pid}/clips/actions", json={"ids": ids, "action": "explode"}).status_code == 422
    # ids from another project are ignored
    assert client.post(f"/api/projects/{pid}/clips/actions", json={"ids": ["nope"], "action": "approve"}).json()["updated"] == 0

    # source info for previewing an unrendered clip (uploaded file = whole source, offset 0)
    si = client.get(f"/api/clips/{ids[0]}/source-info").json()
    assert si["available"] and abs(si["start"] - clips[0]["start"]) < 1e-3 and si["end"] > si["start"]

    # zip: nothing rendered yet → clear error; then render one and download
    assert client.get(f"/api/projects/{pid}/clips.zip").status_code in (400, 422)
    r = client.post(f"/api/projects/{pid}/clips/actions", json={"ids": [ids[0]], "action": "render"}).json()
    assert r["rendering"] == 1
    for _ in range(240):
        c0 = client.get(f"/api/clips/{ids[0]}").json()
        if c0["render_status"] in ("done", "failed"):
            break
        import time

        time.sleep(0.5)
    assert c0["render_status"] == "done", c0.get("render_error")
    z = client.get(f"/api/projects/{pid}/clips.zip")  # default: rendered approved clips
    assert z.status_code == 200 and z.headers["content-type"] == "application/zip"
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert len(names) == 1 and names[0].endswith(".mp4") and names[0].startswith(f"{c0['rank']:02d}_")
    z2 = client.get(f"/api/projects/{pid}/clips.zip?ids={ids[0]},{ids[1]}")
    assert len(zipfile.ZipFile(io.BytesIO(z2.content)).namelist()) == 1  # only rendered ones
    leftovers = [f for f in app_ctx.paths.project_dir(pid).glob("clips_*.zip")]
    assert not leftovers  # temp zip removed after sending

    # re-process keeping approved + hand-made clips
    manual = client.post(f"/api/projects/{pid}/clips", json={"start": 30.0, "end": 38.0, "render": False}).json()  # not approved, still kept
    before = {c["id"] for c in client.get(f"/api/projects/{pid}/clips").json()}
    after = _process(client, pid, keep_clips=True)
    after_ids = {c["id"] for c in after}
    assert ids[0] in after_ids and manual["id"] in after_ids  # approved + manual kept
    assert ids[1] not in after_ids  # un-reviewed auto clip replaced
    assert any(c["analysis"].get("origin") == "auto" for c in after) or len(after) == 2
    kept0 = next(c for c in after if c["id"] == ids[0])
    assert kept0["render_status"] == "done"  # its render survived
    for c in after:  # new clips don't duplicate kept ones
        if c["id"] not in before:
            for k in (kept0, manual):
                inter = min(c["end"], k["end"]) - max(c["start"], k["start"])
                assert inter <= 0.5 * min(c["end"] - c["start"], k["end"] - k["start"]) + 1e-6

    # re-process without keeping → everything replaced
    final = _process(client, pid)
    assert not ({c["id"] for c in final} & after_ids)


def test_bulk_framing_selected_clips(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    pid = upload(client, sample_video).json()["id"]
    _process(client, pid)
    a = client.post(f"/api/projects/{pid}/clips", json={"start": 5.0, "end": 12.0, "render": False}).json()
    b = client.post(f"/api/projects/{pid}/clips", json={"start": 20.0, "end": 27.0, "render": False}).json()
    r = client.post(f"/api/projects/{pid}/clips/bulk", json={"layout": "blur", "ids": [a["id"]], "render": False}).json()
    assert r["updated"] == 1
    assert client.get(f"/api/clips/{a['id']}").json()["edit"]["layout"] == "blur"
    assert client.get(f"/api/clips/{b['id']}").json()["edit"].get("layout") != "blur"
