"""Phase 3: beat/drop detection, montage planning and rendering (synthetic song + videos)."""

import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipstudio.analysis.action import ActionCurves
from clipstudio.analysis.beats import SongAnalysis, analyse_song
from clipstudio.media import ffmpeg as ff
from clipstudio.montage.planner import Moment, cut_times, moments_from_curves, plan, song_window
from tests.test_api import app_ctx, client, upload, wait_job  # noqa: F401

BPM = 128
PERIOD = 60 / BPM
DROP = round(0.2 + 16 * 4 * PERIOD, 3)  # bar 17


def make_song(path: Path, dur: float = 45.0) -> None:
    sr = 22050
    t = np.arange(int(dur * sr)) / sr
    x = np.zeros_like(t)
    rng = np.random.default_rng(0)
    for k, bt in enumerate(np.arange(0.2, dur, PERIOD)):
        i, n = int(bt * sr), int(0.15 * sr)
        n = min(n, x.size - i)
        if n <= 0:
            break
        env = np.exp(-np.arange(n) / (0.03 * sr))
        loud = 1.0 if bt >= DROP else 0.25
        x[i : i + n] += loud * np.sin(2 * np.pi * 60 * np.arange(n) / sr) * env * (1.0 if k % 4 == 0 else 0.7)
        if k % 2 == 1:
            x[i : i + n // 3] += 0.15 * rng.standard_normal(n // 3) * env[: n // 3]
    x[t >= DROP] += 0.25 * np.sin(2 * np.pi * 45 * t[t >= DROP])
    x /= np.abs(x).max() * 1.1
    raw = path.with_suffix(".f32")
    raw.write_bytes(x.astype(np.float32).tobytes())
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(raw), str(path)], check=True)


def make_clips(path: Path, colors: list[str], seg: float = 4.0) -> None:
    """A video made of several 'shots' (different colours, a moving box) = scene cuts + motion."""
    parts = []
    for c in colors:
        parts.append(f"color=c={c}:s=640x360:r=25:d={seg}[{c}];")
    fc = "".join(parts)
    fc += "".join(f"[{c}]" for c in colors) + f"concat=n={len(colors)}:v=1:a=0[bg];color=c=white:s=60x60:r=25:d={seg * len(colors)}[box];[bg][box]overlay=x='300+200*sin(t*3)':y='150+80*sin(t*5)':eval=frame[v]"
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "anullsrc=r=16000:cl=mono", "-filter_complex", fc, "-map", "[v]", "-map", "0:a", "-t", str(seg * len(colors)),
                    "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    d = tmp_path_factory.mktemp("mont")
    make_song(d / "song.mp3")
    make_clips(d / "a.mp4", ["red", "green", "blue", "yellow"])
    make_clips(d / "b.mp4", ["purple", "orange", "cyan"])
    return d


def test_song_analysis(media):
    a = analyse_song(media / "song.mp3")
    assert abs(a.tempo - BPM) < 1.5
    assert a.drop is not None and abs(a.drop - DROP) < 0.1
    assert abs(a.beats[1] - a.beats[0] - PERIOD) < 0.03
    assert any(abs(d - DROP) < 0.1 for d in a.downbeats)


def _song() -> SongAnalysis:
    beats = [round(0.2 + k * PERIOD, 3) for k in range(int(44 / PERIOD))]
    return SongAnalysis(45.0, BPM, beats, beats[::4], DROP, 1.0, [0.5] * len(beats))


def _moments(n: int = 30) -> list[Moment]:
    return [Moment(f"f{k % 3}:{k}", f"/v/{k % 3}.mp4", f"v{k % 3}", k * 3.0, k * 3.0 + 3, k * 3.0 + 1.5, round(1 - k / n, 3), 0.5, 200.0) for k in range(n)]


def test_cut_grid_and_drop_slot():
    s0, s1 = song_window(_song(), 15, 0.4)
    assert s0 <= DROP <= s1 and abs((DROP - s0) / 15 - 0.4) < 0.15
    for style, (pre_beats, post_beats) in {"chill": (4, 2), "normal": (2, 1)}.items():
        cuts, drop = cut_times(_song(), s0, s1, style)
        assert drop is not None and any(abs(c - drop) < 0.02 for c in cuts)
        post = [b - a for a, b in zip(cuts, cuts[1:]) if a > drop + 0.01]
        assert post and abs(np.median(post) - post_beats * PERIOD) < 0.05
        # every cut sits on a beat
        beats_rel = [b - s0 for b in _song().beats]
        assert all(min(abs(c - b) for b in beats_rel) < 0.02 for c in cuts[1:])
    p = plan(_song(), _moments(), length=15, style="normal", seed=1)
    impact = [s for s in p["slots"] if s["impact"]]
    assert len(impact) == 1 and abs(impact[0]["at"] - p["drop"]) < 0.02
    assert impact[0]["energy"] == max(s["energy"] for s in p["slots"])  # strongest shot on the drop
    assert abs(sum(s["dur"] for s in p["slots"]) - p["length"]) < 0.01
    files = [s["file"] for s in p["slots"]]
    assert sum(a == b for a, b in zip(files, files[1:])) <= 1  # sources alternate
    p2 = plan(_song(), _moments(80), length=15, style="normal", seed=2, avoid={s["moment"] for s in p["slots"]})
    overlap = {s["moment"] for s in p["slots"]} & {s["moment"] for s in p2["slots"]}
    assert len(overlap) <= len(p["slots"]) // 3  # variants use different shots


def test_moments_from_curves():
    n = 40
    motion = np.full(n, 2.0, np.float32)
    motion[10:14] = 30
    cuts = np.zeros(n, np.float32)
    cuts[20] = 1
    ms = moments_from_curves(ActionCurves(motion=motion, cuts=cuts, mx=np.full(n, 0.7, np.float32), flux=np.zeros(0)), file="/x.mp4", label="x", file_dur=20)
    assert ms and 5.0 <= ms[0].peak <= 7.0 and ms[0].mx == 0.7
    assert all(m.end <= 20 for m in ms)


def test_montage_end_to_end(client, app_ctx, media):  # noqa: F811
    a = upload(client, media / "a.mp4", "a.mp4").json()
    b = upload(client, media / "b.mp4", "b.mp4").json()
    camp = client.post("/api/campaigns", json={"preset": "clipster_music", "name": "Song promo", "brief": {"min_duration": 10.5, "disclosure": {"text": "#ad", "on_screen": True}}}).json()
    r = client.post("/api/montages", json={"name": "Edit", "campaign_id": camp["id"], "options": {"length": 12, "variants": 2, "style": "normal", "pov": "POV: you finally hit the shot", "sources": [a["id"], b["id"]]}})
    assert r.status_code == 200, r.text
    mid = r.json()["id"]
    assert r.json()["video_mode"] == "montage"
    up = client.post(f"/api/montages/{mid}/song?filename=song.mp3", content=(media / "song.mp3").read_bytes(), headers={"Content-Type": "application/octet-stream"})
    assert up.status_code == 200, up.text
    info = client.get(f"/api/montages/{mid}/song-info").json()
    assert abs(info["tempo"] - BPM) < 1.5 and info["drop"] is not None
    j = wait_job(client, client.post(f"/api/projects/{mid}/process", json={"options": {}}).json()["job_id"], timeout=300)
    assert j["status"] == "done", j
    clips = client.get(f"/api/projects/{mid}/clips").json()
    assert len(clips) == 2
    c0 = clips[0]
    assert c0["render_status"] == "done" and c0["analysis"]["mode"] == "montage" and c0["analysis"]["output"] == "1080x1920"
    assert c0["features"]["music"] == "none" and c0["features"]["sfx"] == "off" and not c0["features"]["hook_overlay"]["enabled"]
    info2 = ff.probe(app_ctx.paths.project_dir(mid) / "clips" / f"{c0['id']}.mp4")
    assert abs(info2.duration - 12.0) < 0.1 and info2.has_audio and (info2.width, info2.height) == (1080, 1920)
    assert any("drop" in x.lower() for x in c0["analysis"]["reasons"])
    comp = client.get(f"/api/clips/{c0['id']}/compliance").json()
    st = {x["id"]: x["status"] for x in comp["checks"]}
    assert st["length"] == "pass" and st["disclosure"] == "pass" and "music" not in st and st["song_align"] == "manual"
    # re-cut: hype style + new POV → stale + re-render
    r = client.post(f"/api/clips/{c0['id']}/montage", json={"style": "hype", "pov": "POV: new text", "reshuffle": True})
    assert r.status_code == 200 and r.json()["hook"] == "POV: new text"
    for job in client.get(f"/api/jobs?project_id={mid}").json():
        if job["kind"] == "render":
            assert wait_job(client, job["id"])["status"] == "done"
    v = client.post(f"/api/montages/{mid}/variant")
    assert v.status_code == 200 and v.json()["rank"] == 3
    exp = client.post(f"/api/clips/{c0['id']}/export").json()
    assert "clip.mp4" in exp["files"]


def test_montage_from_links(client, app_ctx, media, monkeypatch):  # noqa: F811
    """Links added on the montage page are checked (no download), then downloaded in full by the montage job.
    The downloader is stubbed with a local synthetic file — no network."""
    import shutil

    from clipstudio.media import download as dl

    calls = {"download": 0}

    def fake_inspect(url, max_height=1080):
        if "bad" in url:
            raise dl.InvalidInput("Unsupported link")
        return {"url": url, "title": f"Clip {url[-1]}", "duration": 20.0, "estimated_bytes": 1_000_000}

    def fake_download(url, dest_dir, *, max_height=1080, on_progress=None, cancel=None):
        calls["download"] += 1
        out = dest_dir / f"{dl.url_key(url)}.mp4"
        shutil.copy(media / ("a.mp4" if url.endswith("1") else "b.mp4"), out)
        if on_progress:
            on_progress(1.0)
        return out

    monkeypatch.setattr(dl, "inspect", fake_inspect)
    monkeypatch.setattr(dl, "download", fake_download)
    camp = client.post("/api/campaigns", json={"preset": "clipster_music", "name": "Song promo"}).json()
    r = client.post("/api/montages/sources", json={"urls": ["https://example.com/v1", "https://example.com/v2", "https://example.com/bad", "https://example.com/v1"], "campaign_id": camp["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["created"]) == 2 and len(body["errors"]) == 1  # duplicate link ignored, bad link reported
    s1, s2 = body["created"]
    assert s1["name"] == "Clip 1" and s1["video_mode"] == "missing" and s1["duration"] == 20.0 and s1["campaign_id"] == camp["id"]
    assert s1["status"] == "new" and calls["download"] == 0  # nothing downloaded or processed yet

    m = client.post("/api/montages", json={"name": "Edit", "campaign_id": camp["id"], "options": {"length": 10, "variants": 1, "sources": [s1["id"], s2["id"]]}}).json()
    client.post(f"/api/montages/{m['id']}/song?filename=song.mp3", content=(media / "song.mp3").read_bytes(), headers={"Content-Type": "application/octet-stream"})
    j = wait_job(client, client.post(f"/api/projects/{m['id']}/process", json={"options": {}}).json()["job_id"], timeout=300)
    assert j["status"] == "done", j
    assert calls["download"] == 2
    assert j["stages"]["download"]["status"] == "done"
    assert client.get(f"/api/projects/{s1['id']}").json()["video_mode"] == "full"
    clips = client.get(f"/api/projects/{m['id']}/clips").json()
    assert len(clips) == 1 and clips[0]["render_status"] == "done"
    # second run: already downloaded → stage skipped
    j2 = wait_job(client, client.post(f"/api/projects/{m['id']}/process", json={"options": {}}).json()["job_id"], timeout=300)
    assert j2["status"] == "done" and j2["stages"]["download"]["status"] == "skipped" and calls["download"] == 2


def test_project_stages_and_footage_delete(client, app_ctx, media):  # noqa: F811
    """Uploads that were never started aren't 'active'; montage sources are 'footage' and can be deleted with the montage."""
    a = upload(client, media / "a.mp4", "a.mp4").json()
    b = upload(client, media / "b.mp4", "b.mp4").json()
    loose = upload(client, media / "a.mp4", "loose.mp4").json()
    assert a["stage"] == "not_started" and a["used_in"] == []
    m = client.post("/api/montages", json={"name": "Edit", "options": {"sources": [a["id"], b["id"]]}}).json()
    m2 = client.post("/api/montages", json={"name": "Edit 2", "options": {"sources": [b["id"]]}}).json()
    stages = {p["id"]: p["stage"] for p in client.get("/api/projects").json()}
    assert stages[a["id"]] == stages[b["id"]] == "footage" and stages[loose["id"]] == "not_started" and stages[m["id"]] == "active"
    assert [p["id"] for p in client.get("/api/projects?stage=not_started").json()] == [loose["id"]]
    assert {p["id"] for p in client.get("/api/projects?stage=footage").json()} == {a["id"], b["id"]}
    assert client.get(f"/api/projects/{b['id']}").json()["used_in"] == [{"id": m["id"], "name": "Edit"}, {"id": m2["id"], "name": "Edit 2"}]
    dash = client.get("/api/dashboard").json()
    assert dash["recent"] == [] or all(p["status"] != "new" for p in dash["recent"])
    assert dash["projects"] == 0  # nothing started yet
    # delete the first montage with its footage: a goes (only used here), b stays (used by Edit 2)
    r = client.delete(f"/api/projects/{m['id']}?delete_exports=true&delete_source=true&delete_footage=true").json()
    assert r["footage_deleted"] == 1
    ids = {p["id"] for p in client.get("/api/projects").json()}
    assert a["id"] not in ids and b["id"] in ids and loose["id"] in ids
    assert client.get(f"/api/projects/{b['id']}").json()["stage"] == "footage"
