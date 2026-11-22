"""Integration tests through the HTTP API with real FFmpeg rendering.

Transcription is pre-seeded into the cache for speed/determinism (the real model is exercised in
test_transcription.py, marked slow).
"""

import json
import shutil
import subprocess
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from clipstudio.api.app import create_app
from clipstudio.config import Env
from clipstudio.context import AppContext
from clipstudio.jobs.worker import Worker
from clipstudio.media import ffmpeg as ff
from clipstudio.storage import fingerprint

SCRIPT = [
    "Here's the secret nobody tells you about saving money.",
    "Most people think they need a bigger salary to get rich.",
    "But the truth is your daily habits matter far more than your income.",
    "I remember when I was twenty two and I had nothing in the bank.",
    "I was spending eight dollars on coffee every single day.",
    "That adds up to almost three thousand dollars a year.",
    "So I decided to track every single purchase for one month.",
    "You should try it because it will shock you how much you waste.",
    "And that's why I tell everyone to start with tracking, not budgeting.",
]


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory) -> Path:
    d = tmp_path_factory.mktemp("media")
    out = d / "talk.mp4"
    subprocess.run(
        [ff.FFMPEG, "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc2=size=640x360:rate=25", "-f", "lavfi",
         "-i", "sine=frequency=220:sample_rate=16000", "-t", "40", "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(out)],
        check=True,
    )
    return out


def seed_transcript(ctx: AppContext, video: Path) -> None:
    """Write a cached transcript for ``video`` so processing skips the speech model."""
    words, sents, t = [], [], 0.6
    for text in SCRIPT:
        i0 = len(words)
        for w in text.split():
            words.append({"w": w, "s": round(t, 3), "e": round(t + 0.3, 3), "c": 1.0})
            t += 0.36
        sents.append({"start": words[i0]["s"], "end": words[-1]["e"], "text": text, "i0": i0, "i1": len(words)})
        t += 0.4
    model = ctx.settings.get().transcription_model
    path = ctx.paths.cache / f"transcript_{fingerprint(video)}_{model.split('/')[-1]}.json"
    path.write_text(json.dumps({"model": model, "language": "en", "duration": 40.0, "words": words, "sentences": sents}))


@pytest.fixture
def app_ctx(tmp_path):
    ctx = AppContext.create(Env(home=tmp_path / "home", frontend_dist=tmp_path / "nodist"))
    ctx.settings.update({"min_free_gb": 0.2, "duration_preset": "15-30", "max_clips": 3, "min_score": 0})
    return ctx


@pytest.fixture
def client(app_ctx):
    app = create_app(app_ctx)
    with TestClient(app) as c:
        yield c


def upload(client, video: Path, name="talk.mp4"):
    return client.post(f"/api/projects/upload?filename={name}", content=video.read_bytes(), headers={"Content-Type": "application/octet-stream"})


def wait_job(client, job_id, timeout=120):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = client.get(f"/api/jobs/{job_id}").json()
        if j["status"] in ("done", "failed", "cancelled"):
            return j
        time.sleep(0.3)
    raise AssertionError("job timed out")


def test_health_and_system(client):
    assert client.get("/api/health").json() == {"ok": True}
    s = client.get("/api/system").json()
    assert s["tools"]["ffmpeg"] and s["mode"] == "low"
    assert s["privacy"]["model_downloads"] is False


def test_rejects_foreign_host_header(app_ctx):
    with TestClient(create_app(app_ctx, start_worker=False), base_url="http://evil.example") as c:
        assert c.get("/api/health").status_code == 400


def test_full_pipeline_upload_process_render_edit(client, app_ctx, sample_video):
    r = upload(client, sample_video)
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["duration"] == pytest.approx(40, abs=0.5) and p["width"] == 640
    seed_transcript(app_ctx, Path(app_ctx.db.one("SELECT source_path FROM projects WHERE id=?", (p["id"],))["source_path"]))

    est = client.get(f"/api/projects/{p['id']}/estimate").json()
    assert est["estimate"]["total_mb"] > 0

    job = client.post(f"/api/projects/{p['id']}/process", json={"options": {"caption_preset": "highlight"}}).json()
    j = wait_job(client, job["job_id"])
    assert j["status"] == "done", j
    assert j["stages"]["transcribe"]["status"] == "skipped"  # cache hit: no re-transcription
    assert j["stages"]["render"]["status"] == "done"

    clips = client.get(f"/api/projects/{p['id']}/clips").json()
    assert 1 <= len(clips) <= 3
    top = clips[0]
    assert "secret" in top["analysis"]["transcript"].lower() or "truth" in top["analysis"]["transcript"].lower()
    assert set(top["analysis"]["scores"]) == {"hook", "context", "emotion", "value", "story", "visual", "audio"}
    assert top["analysis"]["label"] and top["hooks"] and top["title"] and top["hashtags"]
    assert top["render_status"] == "done" and top["has_video"]

    # rendered file is a real 9:16 720p MP4 of the right length
    path = Path(app_ctx.db.one("SELECT render_path FROM clips WHERE id=?", (top["id"],))["render_path"])
    info = ff.probe(path)
    assert (info.width, info.height) == (720, 1280)
    assert info.duration == pytest.approx(top["duration"], abs=0.25)
    assert info.has_audio
    assert client.get(f"/api/clips/{top['id']}/download").headers["content-disposition"].startswith("attachment")
    r = client.get(f"/api/clips/{top['id']}/video", headers={"Range": "bytes=0-99"})
    assert r.status_code == 206 and len(r.content) == 100

    # transcript exports
    assert client.get(f"/api/projects/{p['id']}/transcript.srt").text.startswith("1\n")

    # edit: trim, change aspect + preset, edit a caption word → stale → re-render
    words = client.get(f"/api/clips/{top['id']}/words").json()
    edited = [dict(w) for w in words["words"]]
    edited[0]["w"] = "HERE'S"
    r = client.patch(f"/api/clips/{top['id']}", json={"start": top["start"] + 0.5, "edit": {"aspect_ratio": "1:1", "caption_preset": "minimal"}, "caption_words": edited, "title": "My title", "status": "approved"})
    assert r.status_code == 200, r.text
    c2 = r.json()
    assert c2["render_status"] == "stale" and c2["status"] == "approved" and c2["edit"]["has_caption_edits"]
    pf = client.post(f"/api/clips/{top['id']}/preview-frame", json={"t": 1.0})
    assert pf.status_code == 200 and pf.headers["content-type"] == "image/jpeg" and len(pf.content) > 1000
    j = wait_job(client, client.post(f"/api/clips/{top['id']}/render").json()["job_id"])
    assert j["status"] == "done", j
    info = ff.probe(path)
    assert (info.width, info.height) == (720, 720)
    assert info.duration == pytest.approx(top["duration"] - 0.5, abs=0.25)

    # dashboard + storage
    d = client.get("/api/dashboard").json()
    assert d["projects"] == 1 and d["clips"] >= 1
    st = client.get("/api/storage").json()
    assert st["projects"][0]["clips_mb"] > 0
    assert client.post("/api/storage/cleanup", json={"targets": ["temp", "partial"]}).status_code == 200

    # delete project but keep exports; uploaded source deleted on request
    r = client.delete(f"/api/projects/{p['id']}?delete_source=true").json()
    assert r["deleted"] and path.exists()


def test_invalid_inputs(client, tmp_path):
    assert client.post("/api/projects/upload?filename=notes.txt", content=b"hello").status_code == 422
    r = client.post("/api/projects/upload?filename=fake.mp4", content=b"not a video at all" * 100)
    assert r.status_code == 422 and "readable" in r.json()["error"]
    r = client.post("/api/sources/inspect-url", json={"url": "http://127.0.0.1:8765/x.mp4"})
    assert r.status_code == 422 and "private" in r.json()["error"]
    assert client.post("/api/sources/inspect-url", json={"url": "file:///etc/passwd"}).status_code == 422
    assert client.post("/api/projects/import-path", json={"path": "/etc/hosts"}).status_code == 422
    assert client.get("/api/projects/nope").status_code == 404
    assert client.put("/api/settings", json={"llm_url": "http://example.com:11434"}).status_code == 422
    assert client.put("/api/settings", json={"work_dir": "/etc/clipstudio"}).status_code == 422


def test_insufficient_storage(client, app_ctx, sample_video):
    app_ctx.settings.update({"min_free_gb": 100000})
    r = upload(client, sample_video)
    assert r.status_code == 507 and "disk space" in r.json()["error"]


def test_missing_model_fails_cleanly(client, app_ctx, sample_video):
    p = upload(client, sample_video).json()
    app_ctx.settings.update({"transcription_model": "mlx-community/does-not-exist"})
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/process", json={}).json()["job_id"])
    assert j["status"] == "failed" and "not installed" in j["error"]
    assert client.get(f"/api/projects/{p['id']}").json()["status"] == "failed"
    # temp audio was cleaned up even though the job failed
    assert not any((app_ctx.paths.work / "projects" / p["id"] / "tmp").glob("*.wav"))


def test_cancel_queued_and_running(app_ctx, sample_video):
    app = create_app(app_ctx, start_worker=False)
    with TestClient(app) as client:
        p = upload(client, sample_video).json()
        jid = client.post(f"/api/projects/{p['id']}/process", json={}).json()["job_id"]
        assert client.post(f"/api/jobs/{jid}/cancel").status_code == 200
        assert client.get(f"/api/jobs/{jid}").json()["status"] == "cancelled"

        # running cancel: start the worker and cancel mid-job
        seed_transcript(app_ctx, Path(app_ctx.db.one("SELECT source_path FROM projects WHERE id=?", (p["id"],))["source_path"]))
        jid = client.post(f"/api/projects/{p['id']}/process", json={}).json()["job_id"]
        w: Worker = app.state.worker
        w.start()
        t0 = time.time()
        while client.get(f"/api/jobs/{jid}").json()["status"] != "running" and time.time() - t0 < 20:
            time.sleep(0.05)
        client.post(f"/api/jobs/{jid}/cancel")
        j = wait_job(client, jid)
        assert j["status"] in ("cancelled", "done")  # may finish first on a fast machine
        w.stop()


def test_restart_recovery(app_ctx, sample_video):
    db = app_ctx.db
    now = time.time()
    db.insert("projects", {"id": "p1", "name": "x", "source_type": "upload", "source_path": str(sample_video), "status": "processing", "created_at": now, "updated_at": now})
    db.insert("jobs", {"id": "j1", "project_id": "p1", "kind": "process", "status": "running", "created_at": now})
    assert Worker(app_ctx).recover() == 1
    assert db.one("SELECT status FROM jobs WHERE id='j1'")["status"] == "queued"
    assert db.one("SELECT status FROM projects WHERE id='p1'")["status"] == "queued"
