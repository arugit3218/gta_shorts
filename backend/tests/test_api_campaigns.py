"""End-to-end API flow for Phase 1 (synthetic video, pre-seeded transcript, real FFmpeg)."""

import io
import json
import subprocess
import zipfile
from pathlib import Path

import cv2
import numpy as np

from clipstudio.media import ffmpeg as ff

from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload, wait_job  # noqa: F401


def _logo_png(tmp: Path) -> bytes:
    img = np.zeros((200, 600, 4), np.uint8)
    img[20:180, 20:580] = (0, 200, 0, 255)
    p = tmp / "logo.png"
    cv2.imwrite(str(p), img)
    return p.read_bytes()


def test_campaign_lifecycle(client, app_ctx, sample_video, tmp_path):  # noqa: F811
    presets = client.get("/api/campaign-presets").json()
    assert any(p["key"] == "clipster_logo" for p in presets)
    assert set(client.get("/api/safe-zones").json()) == {"reels", "shorts"}

    parsed = client.post("/api/campaigns/parse", json={"text": "Logo campaign. Logo in the top half. Minimum 10 seconds. #brand @brandhq"}).json()
    assert parsed["patch"]["logo"]["zone"] == "top_half"

    r = client.post("/api/campaigns", json={"preset": "generic_logo", "text": "Brand X logo campaign\nLogo in the top half. Minimum 10 seconds.\n#brandx @brandx\nDo not post NSFW", "brief": {"disclosure": {"text": "#ad", "on_screen": True}}})
    assert r.status_code == 200, r.text
    camp = r.json()
    cid = camp["id"]
    assert camp["brief"]["type"] == "logo" and camp["brief"]["min_duration"] == 10
    assert "#brandx" in camp["brief"]["required_hashtags"]
    assert camp["brief"]["disclosure"]["on_screen"] is True

    # logo upload (file path never exposed)
    r = client.post(f"/api/campaigns/{cid}/logo?filename=logo.png", content=_logo_png(tmp_path), headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    lg = r.json()["brief"]["logo"]
    assert lg["has_file"] and lg["mode"] == "free" and "file" not in lg and lg["image_w"] == 600
    assert client.get(f"/api/campaigns/{cid}/logo").status_code == 200
    # client can't point the logo at another file
    client.patch(f"/api/campaigns/{cid}", json={"brief": {"logo": {"file": "/etc/passwd", "y": 0.2}}})
    row = app_ctx.db.one("SELECT brief FROM campaigns WHERE id = ?", (cid,))
    assert row["brief"]["logo"]["file"].endswith("logo.png") and row["brief"]["logo"]["y"] == 0.2

    # sound reference + upload
    client.post(f"/api/campaigns/{cid}/sounds/ref", json={"name": "Song A"})
    # project in the campaign
    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    r = client.patch(f"/api/projects/{p['id']}", json={"campaign_id": cid})
    assert r.json()["campaign_name"] == camp["name"]
    job = client.post(f"/api/projects/{p['id']}/process", json={"options": {"sfx": "auto", "music": "auto"}}).json()["job_id"]
    j = wait_job(client, job, timeout=240)
    assert j["status"] == "done", j
    clips = client.get(f"/api/projects/{p['id']}/clips").json()
    assert clips and clips[0]["features"]["logo"] is True and clips[0]["features"]["disclosure"] is True
    c0 = clips[0]
    assert c0["render_status"] == "done"
    assert c0["analysis"]["output"] == "1080x1920"  # campaign default output height
    assert c0["analysis"]["sfx_used"] and c0["analysis"]["sfx_used"][0]["type"] == "pop"
    assert c0["analysis"]["music_used"] is None  # library is empty

    comp = client.get(f"/api/clips/{c0['id']}/compliance").json()
    ids = {c["id"]: c["status"] for c in comp["checks"]}
    assert ids["render"] == "pass" and ids["logo"] == "pass" and ids["disclosure"] == "pass"
    assert ids["logo_zone"] == "pass"
    assert "reels" in comp["posts"] and "#brandx" in comp["posts"]["reels"]["caption"]
    assert ids["tags_reels"] == "pass"

    # post caption edit doesn't make the render stale
    r = client.patch(f"/api/clips/{c0['id']}", json={"edit": {"post": {"reels": "custom caption #brandx @brandx #ad"}}})
    assert r.json()["render_status"] == "done"
    # visual edit does
    r = client.patch(f"/api/clips/{c0['id']}", json={"edit": {"hook_overlay": {"enabled": False}}})
    assert r.json()["render_status"] == "stale"
    assert {c["id"]: c["status"] for c in client.get(f"/api/clips/{c0['id']}/compliance").json()["checks"]}["render"] == "fail"
    j = wait_job(client, client.post(f"/api/clips/{c0['id']}/render").json()["job_id"])
    assert j["status"] == "done", j

    # export package
    exp = client.post(f"/api/clips/{c0['id']}/export").json()
    folder = Path(exp["path"])
    assert {"clip.mp4", "manifest.json", "checklist.md", "caption_reels.txt", "caption_shorts.txt"} <= set(exp["files"])
    man = json.loads((folder / "manifest.json").read_text())
    assert man["campaign"]["id"] == cid and man["compliance"]["checks"]
    assert "custom caption" in (folder / "caption_reels.txt").read_text()
    z = client.get(f"/api/clips/{c0['id']}/export.zip")
    assert z.status_code == 200
    names = zipfile.ZipFile(io.BytesIO(z.content)).namelist()
    assert any(n.endswith("clip.mp4") for n in names)

    # duplicate memory: same footage in another campaign is flagged
    other = client.post("/api/campaigns", json={"preset": "generic", "name": "Other"}).json()
    client.patch(f"/api/projects/{p['id']}", json={"campaign_id": other["id"]})
    comp2 = client.get(f"/api/clips/{c0['id']}/compliance").json()
    assert {c["id"]: c["status"] for c in comp2["checks"]}["duplicate"] == "fail"

    # campaign detail lists projects/exports; brief change marks renders stale
    d = client.get(f"/api/campaigns/{cid}").json()
    assert d["exported"] == 1
    client.patch(f"/api/projects/{p['id']}", json={"campaign_id": cid})
    client.patch(f"/api/campaigns/{cid}", json={"brief": {"disclosure": {"text": "#sponsored", "on_screen": True}}})
    assert client.get(f"/api/clips/{c0['id']}").json()["render_status"] == "stale"


def test_quick_manual_and_ai_picks(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/process", json={"options": {"quick": True, "quick_start": 2, "quick_end": 14, "auto_render": False}}).json()["job_id"], timeout=240)
    assert j["status"] == "done", j
    clips = client.get(f"/api/projects/{p['id']}/clips").json()
    assert len(clips) == 1 and clips[0]["analysis"]["origin"] == "quick"
    assert 1.5 <= clips[0]["start"] <= 2.5 and 13.5 <= clips[0]["end"] <= 14.5

    r = client.post(f"/api/projects/{p['id']}/clips", json={"start": 5.0, "end": 16.0, "title": "My pick", "render": False})
    assert r.status_code == 200, r.text
    assert r.json()["title"] == "My pick" and r.json()["analysis"]["origin"] == "manual"
    assert client.post(f"/api/projects/{p['id']}/clips", json={"start": 5.0, "end": 6.0}).status_code == 422

    prompt = client.get(f"/api/projects/{p['id']}/ai-prompt").json()["prompt"]
    assert "Here's the secret" in prompt
    answer = '```json\n{"clips":[{"start":"0:00.6","end":"0:18.0","title":"Secret to saving","score":92,"reason":"strong hook","hashtags":["#money"]}]}\n```'
    r = client.post(f"/api/projects/{p['id']}/ai-picks", json={"text": answer, "render": False})
    assert r.status_code == 200, r.text
    made = r.json()["clips"][0]
    assert made["title"] == "Secret to saving" and made["analysis"]["origin"] == "ai" and made["hashtags"] == ["#money"]
    assert any("AI pick" in x for x in made["analysis"]["reasons"])
    assert client.post(f"/api/projects/{p['id']}/ai-picks", json={"text": "no json here"}).status_code == 422


def test_library_and_bulk(client, app_ctx, sample_video, tmp_path):  # noqa: F811
    import subprocess

    from clipstudio.media import ffmpeg as ff

    song = tmp_path / "beat.mp3"
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "sine=frequency=330:duration=40", str(song)], check=True)
    lib = client.post("/api/library/music/energetic?filename=beat.mp3", content=song.read_bytes(), headers={"Content-Type": "application/octet-stream"}).json()
    assert lib["music"]["energetic"][0]["name"] == "beat.mp3"
    assert client.get("/api/library/music/energetic/beat.mp3").status_code == 200
    assert client.get("/api/library/music/energetic/..%2F..%2Fsecret").status_code == 404
    assert client.get("/api/sfx/pop").status_code == 200

    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/process", json={"options": {"music": "auto"}}).json()["job_id"], timeout=240)
    assert j["status"] == "done", j
    c0 = client.get(f"/api/projects/{p['id']}/clips").json()[0]
    assert c0["analysis"]["music_used"] == "beat.mp3"
    # explicit library track, then none
    r = client.patch(f"/api/clips/{c0['id']}", json={"edit": {"music": "lib:energetic/beat.mp3"}})
    assert r.json()["features"]["music"] == "lib:energetic/beat.mp3"
    assert client.patch(f"/api/clips/{c0['id']}", json={"edit": {"music": "lib:../etc"}}).status_code == 422
    client.delete("/api/library/music/energetic/beat.mp3")
    assert client.get("/api/library").json()["music"]["energetic"] == []

    # bulk: invalid entries are reported, not fatal (no network calls: private/invalid URLs fail validation)
    r = client.post("/api/projects/bulk", json={"urls": ["http://127.0.0.1/x", "not a url", "http://127.0.0.1/x"]}).json()
    assert r["created"] == [] and len(r["errors"]) == 2

    s = client.get("/api/system").json()
    assert "downloads" in s and "youtube_ready" in s["downloads"]


def test_smart_download_sections(client, app_ctx, sample_video, tmp_path, monkeypatch):  # noqa: F811
    """Audio-first + per-clip sections, with yt-dlp simulated by local FFmpeg cuts (no network)."""
    import subprocess

    from clipstudio.media import download as dl
    from clipstudio.media import ffmpeg as ff

    audio = tmp_path / "remote_audio.m4a"
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-i", str(sample_video), "-vn", "-c:a", "aac", str(audio)], check=True)
    calls = {"sections": 0}

    monkeypatch.setattr(dl, "validate_url", lambda u: u)
    monkeypatch.setattr(dl, "inspect", lambda u, h=1080: {"url": u, "title": "Remote talk", "duration": 40.0, "width": 640, "height": 360, "fps": 25, "heatmap": [{"start": i * 2.0, "end": i * 2.0 + 2, "value": 0.9 if 3 <= i <= 8 else 0.1} for i in range(20)], "chapters": [], "creator_subtitles": [], "estimated_bytes": 1000})

    def fake_audio(url, dest_dir, on_progress=None, cancel=None):
        out = dest_dir / "remote.audio.m4a"
        out.write_bytes(audio.read_bytes())
        return out

    def fake_section(url, dest, start, end, max_height=1080, cancel=None):
        calls["sections"] += 1
        # simulate a keyframe-aligned cut that starts 0.4 s before the requested time
        real = max(0.0, start - 0.4)
        subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-ss", f"{real:.2f}", "-t", f"{end - real:.2f}", "-i", str(sample_video), "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(dest)], check=True)
        return dest

    monkeypatch.setattr(dl, "download_audio", fake_audio)
    monkeypatch.setattr(dl, "download_section", fake_section)
    seed_transcript(app_ctx, audio)  # fingerprint is identical for the copied audio file

    r = client.post("/api/projects", json={"url": "https://example.com/v", "options": {"duration_preset": "15-30"}})
    pid = r.json()["id"]
    pj = client.get(f"/api/projects/{pid}").json()
    j = wait_job(client, pj["job"]["id"], timeout=240)
    assert j["status"] == "done", j
    assert j["stages"]["sections"]["status"] == "done"
    pj = client.get(f"/api/projects/{pid}").json()
    assert pj["video_mode"] == "sections" and pj["insights"]["heatmap"]
    clips = client.get(f"/api/projects/{pid}/clips").json()
    assert clips and all(c["render_status"] == "done" for c in clips)
    assert calls["sections"] >= 1
    words = client.get(f"/api/clips/{clips[0]['id']}/words").json()
    lo, hi = words["source_range"]
    assert lo <= clips[0]["start"] and hi >= clips[0]["end"] and words["source_offset"] == lo
    sec = json.loads((app_ctx.paths.project_dir(pid) / "sections.json").read_text())
    assert any(s["confidence"] > 0.5 for s in sec)  # aligned via audio correlation
    assert all(abs(s["start"] - max(0.0, s["start"])) < 1e-6 for s in sec)
    assert client.get(f"/api/clips/{clips[0]['id']}/source").status_code == 200
    st = client.get("/api/storage").json()
    assert any(p["smart"] for p in st["projects"])


def test_video_logo_upload(client, tmp_path):  # noqa: F811
    cid = client.post("/api/campaigns", json={"preset": "generic_logo"}).json()["id"]
    p = tmp_path / "logo.mov"  # transparent 9:16 video; the preset already says "place it"
    src = "color=red:s=1080x1920:d=1:r=10,format=rgba,colorchannelmixer=aa=0,drawbox=x=100:y=100:w=300:h=200:color=red@1:t=fill:replace=1"
    subprocess.run([ff.FFMPEG, "-v", "error", "-f", "lavfi", "-i", src, "-c:v", "qtrle", str(p)], check=True)
    r = client.post(f"/api/campaigns/{cid}/logo?filename=logo.mov", content=p.read_bytes(), headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    lg = r.json()["brief"]["logo"]
    assert lg["has_file"] and lg["animated"] and lg["mode"] == "free" and (lg["image_w"], lg["image_h"]) == (300, 200)
    still = client.get(f"/api/campaigns/{cid}/logo")
    assert still.status_code == 200 and still.content.startswith(b"\x89PNG")
    assert client.delete(f"/api/campaigns/{cid}/logo").status_code == 200
    assert client.get(f"/api/campaigns/{cid}/logo").status_code == 404


def test_green_screen_video_logo(client, tmp_path):  # noqa: F811
    cid = client.post("/api/campaigns", json={"preset": "generic_logo"}).json()["id"]
    p = tmp_path / "band.mp4"  # green screen on top, brand band at the bottom
    src = "color=0x2BD93E:s=1080x1920:d=1:r=10,drawbox=x=0:y=1320:w=1080:h=600:color=0x25263A:t=fill"
    subprocess.run([ff.FFMPEG, "-v", "error", "-f", "lavfi", "-i", src, "-pix_fmt", "yuv420p", str(p)], check=True)
    r = client.post(f"/api/campaigns/{cid}/logo?filename=band.mp4", content=p.read_bytes(), headers={"Content-Type": "application/octet-stream"})
    assert r.status_code == 200, r.text
    lg = r.json()["brief"]["logo"]
    assert lg["mode"] == "free" and lg["width"] == 1.0 and lg["image_w"] == 1080 and 590 <= lg["image_h"] <= 610
    assert abs(lg["y"] - 1620 / 1920) < 0.01
    assert any("Green screen" in n for n in r.json()["notes"])
    part = cv2.imdecode(np.frombuffer(client.get(f"/api/campaigns/{cid}/logo").content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert part.shape[:2] == (lg["image_h"], 1080)
    patched = client.patch(f"/api/campaigns/{cid}", json={"brief": {"captions": False, "logo": {"width": 0.5}}}).json()["brief"]
    assert patched["captions"] is False and patched["logo"]["width"] == 0.5 and patched["logo"]["crop"] == lg["crop"]
    still = cv2.imdecode(np.frombuffer(client.get(f"/api/campaigns/{cid}/logo?full=true").content, np.uint8), cv2.IMREAD_UNCHANGED)
    assert still[400, 540, 3] == 0 and still[1600, 540, 3] == 255
