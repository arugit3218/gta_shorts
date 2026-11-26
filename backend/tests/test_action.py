"""Phase 2: action detection and action-following crops (synthetic media)."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from clipstudio.analysis import action as act
from clipstudio.media import ffmpeg as ff
from clipstudio.media.reframe import MotionCollector, plan_motion_crop, scan_frames
from clipstudio.storage import fingerprint
from tests.test_api import app_ctx, client, upload, wait_job  # noqa: F401

BURST = (24.0, 31.0)


@pytest.fixture(scope="module")
def gameplay(tmp_path_factory) -> Path:
    """60 s 'gameplay': calm scene, then 24–31 s a box races across the right side + loud bangs."""
    d = tmp_path_factory.mktemp("game")
    sr = 16000
    t = np.arange(60 * sr) / sr
    rng = np.random.default_rng(3)
    audio = 0.02 * rng.standard_normal(t.size)
    for k in np.arange(BURST[0], BURST[1], 0.45):
        i = int(k * sr)
        audio[i : i + 1600] += rng.standard_normal(1600) * np.exp(-np.arange(1600) / 300) * 0.9
    (d / "a.f32").write_bytes(audio.astype(np.float32).tobytes())
    fc = (
        "color=c=white:s=120x120:r=25:d=60[box];"
        f"[0:v][box]overlay=x='if(between(t,{BURST[0]},{BURST[1]}),900+250*sin(t*9),1100)':y='if(between(t,{BURST[0]},{BURST[1]}),300+200*sin(t*7),300)':eval=frame[v]"
    )
    out = d / "game.mp4"
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=0x224466:s=1280x720:r=25:d=60", "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", str(d / "a.f32"),
                    "-filter_complex", fc, "-map", "[v]", "-map", "1:a", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(out)], check=True)
    wav = d / "a.wav"
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-i", str(out), "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(wav)], check=True)
    return out


def test_curves_and_highlight_find_the_burst(gameplay):
    wav = gameplay.parent / "a.wav"
    ac = act.audio_curves(wav)
    vc = act.video_curves(gameplay, 60)
    assert abs(ac.n() - 120) <= 2 and abs(vc.n() - 120) <= 3
    curves = ac.merged(vc)
    burst = slice(int(BURST[0] / 0.5), int(BURST[1] / 0.5))
    assert curves.motion[burst].mean() > 5 * (curves.motion[:40].mean() + 0.01)
    ex = act.excitement(curves, None, act.ActionWeights().normalised(), duration=60)
    assert BURST[0] - 2 <= float(np.argmax(ex.total)) * 0.5 <= BURST[1] + 2
    cands, stats = act.find_action_highlights(curves, None, duration=60, min_dur=10, max_dur=20, max_clips=3, min_score=0)
    assert cands, stats
    best = cands[0]
    inter = min(best.end, BURST[1]) - max(best.start, BURST[0])
    assert inter >= 5, (best.start, best.end)
    assert best.extras["mode"] == "action" and best.score.reasons
    assert best.extras["criteria"]["visual"] == "On-screen action"
    # audio-only analysis still finds it
    cands2, _ = act.find_action_highlights(ac, None, duration=60, min_dur=10, max_dur=20, max_clips=3, min_score=0)
    assert cands2 and min(cands2[0].end, BURST[1]) - max(cands2[0].start, BURST[0]) >= 4


def test_motion_crop_follows_the_action(gameplay):
    mc = MotionCollector(crop_frac=(9 / 16) / (16 / 9))
    scan_frames(gameplay, BURST[0], BURST[1], [mc.add])
    assert len(mc.times) > 10
    plan = plan_motion_crop(mc, src_w=1280, src_h=720, aspect=9 / 16, duration=BURST[1] - BURST[0], out_fps=25)
    centre = (1280 - plan.crop_w) / 2
    assert plan.xs[len(plan.xs) // 2] > centre + 150  # moved right, towards the box
    assert np.abs(np.diff(plan.xs)).max() < 40  # smooth, no jumps
    assert plan_motion_crop(None, src_w=1280, src_h=720, aspect=9 / 16, duration=2, out_fps=25).xs[0] == int(centre)


def test_gameplay_project_end_to_end(client, app_ctx, gameplay):  # noqa: F811
    app_ctx.settings.update({"duration_preset": "15-30", "max_clips": 2})
    # no speech at all: an empty cached transcript
    model = app_ctx.settings.get().transcription_model
    (app_ctx.paths.cache / f"transcript_{fingerprint(gameplay)}_{model.split('/')[-1]}.json").write_text(json.dumps({"model": model, "language": "en", "duration": 60.0, "words": [], "sentences": []}))
    p = upload(client, gameplay, "game.mp4").json()
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/process", json={"options": {"content_type": "gameplay"}}).json()["job_id"], timeout=300)
    assert j["status"] == "done", j
    assert "gameplay" in j["stages"]["analyse"]["message"] and j["stages"]["action"]["status"] == "done"
    clips = client.get(f"/api/projects/{p['id']}/clips").json()
    assert clips
    c0 = clips[0]
    assert c0["analysis"]["mode"] == "action" and c0["category"] in ("Action", "Reaction")
    assert min(c0["end"], BURST[1]) - max(c0["start"], BURST[0]) >= 5
    assert c0["render_status"] == "done" and c0["title"]
    # auto mode with no speech also picks gameplay
    p2 = upload(client, gameplay, "game2.mp4").json()
    j2 = wait_job(client, client.post(f"/api/projects/{p2['id']}/process", json={"options": {"auto_render": False}}).json()["job_id"], timeout=300)
    assert j2["status"] == "done" and "gameplay" in j2["stages"]["analyse"]["message"]
