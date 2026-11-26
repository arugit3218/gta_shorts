"""Phase 1.5: fill-first Auto framing, webcam detection, stream layout, bulk framing."""

import subprocess
from pathlib import Path

import numpy as np

from clipstudio.media import ffmpeg as ff
from clipstudio.media.reframe import Face, FaceTrack, cover_crop, facecam_rect, median_face_height
from clipstudio.media.render import RenderSpec, choose_layout, render_clip
from tests.test_api import app_ctx, client, sample_video, seed_transcript, upload, wait_job  # noqa: F401


def _track(face: Face | None, n: int = 40) -> FaceTrack:
    t = FaceTrack()
    for i in range(n):
        t.times.append(i * 0.25)
        t.faces.append([Face(face.cx + 0.003 * (i % 3), face.cy, face.w, face.h, 0.9)] if face else [])
    return t


def _spec(**kw) -> RenderSpec:
    return RenderSpec(src=Path("x.mp4"), start=0, end=10, src_w=1920, src_h=1080, src_fps=30, has_audio=True, **kw)


def test_auto_fills_instead_of_blurring():
    assert choose_layout(_spec(), None) == ("center", None)
    assert choose_layout(_spec(), _track(None)) == ("center", None)
    assert choose_layout(_spec(auto_fallback="blur"), _track(None)) == ("blur", None)
    assert choose_layout(_spec(layout="blur"), None) == ("blur", None)  # explicit choice still honoured


def test_big_face_is_tracked():
    tr = _track(Face(0.5, 0.4, 0.12, 0.22, 0.9))
    assert median_face_height(tr) > 0.2
    assert choose_layout(_spec(), tr)[0] == "track"


def test_corner_webcam_gives_stream_layout():
    tr = _track(Face(0.87, 0.78, 0.05, 0.09, 0.9))  # small face, bottom-right corner
    cam = facecam_rect(tr)
    assert cam is not None and cam[0] > 0.6 and cam[1] > 0.5 and cam[2] <= 1 and cam[3] <= 1
    layout, st = choose_layout(_spec(), tr)
    assert layout == "stream" and st["cam"] == cam and st["detected"]
    # turned off → plain fill (the small face isn't worth tracking)
    assert choose_layout(_spec(auto_stream=False), tr)[0] in ("center", "track")
    # a small face in the middle is not a webcam
    assert facecam_rect(_track(Face(0.5, 0.5, 0.05, 0.09, 0.9))) is None


def test_explicit_stream_without_cam_falls_back_to_fill():
    assert choose_layout(_spec(layout="stream"), None) == ("center", None)
    layout, st = choose_layout(_spec(layout="stream", stream={"cam": [0.0, 0.0, 0.3, 0.3]}), None)
    assert layout == "stream" and st["cam_height"] == 0.38


def test_cover_crop():
    w, h, x, y = cover_crop((100, 100, 500, 400), 720 / 486, 1920, 1080)
    assert abs(w / h - 720 / 486) < 0.02 and 100 <= x and x + w <= 500 and 100 <= y and y + h <= 400


def test_stream_render_stacks_webcam_over_gameplay(tmp_path):
    src = tmp_path / "stream.mp4"
    # gameplay = blue frame; "webcam" = red box in the top-left corner
    subprocess.run([ff.FFMPEG, "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=blue:s=1280x720:r=25:d=3", "-f", "lavfi", "-i", "sine=f=300:d=3",
                    "-vf", "drawbox=x=0:y=0:w=320:h=240:color=red:t=fill", "-c:v", "libx264", "-preset", "ultrafast", "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", str(src)], check=True)
    spec = RenderSpec(src=src, start=0, end=2.5, src_w=1280, src_h=720, src_fps=25, has_audio=True, layout="stream", encoder="x264",
                      stream={"cam": [0.0, 0.0, 0.25, 0.333], "cam_height": 0.4, "game_x": 0.6, "cam_top": True})
    out = tmp_path / "o.mp4"
    res = render_clip(spec, out, face_track=False)
    assert res.layout_used == "stream" and res.stream["cam"] == [0.0, 0.0, 0.25, 0.333]
    raw = subprocess.run([ff.FFMPEG, "-v", "error", "-ss", "1", "-i", str(out), "-frames:v", "1", "-vf", "scale=90:160", "-f", "rawvideo", "-pix_fmt", "rgb24", "pipe:1"], capture_output=True, check=True).stdout
    img = np.frombuffer(raw, np.uint8).reshape(160, 90, 3)
    top, bottom = img[30, 45], img[120, 45]
    assert top[0] > 180 and top[2] < 80  # webcam (red) fills the top band edge to edge
    assert bottom[2] > 180 and bottom[0] < 80  # gameplay (blue) fills the bottom, no blurred bars
    assert img[2, 2][0] > 150 and img[157, 2][2] > 150  # corners are content, not letterbox


def test_project_stream_and_bulk_api(client, app_ctx, sample_video):  # noqa: F811
    seed_transcript(app_ctx, sample_video)
    p = upload(client, sample_video).json()
    j = wait_job(client, client.post(f"/api/projects/{p['id']}/process", json={"options": {"auto_render": False, "layout": "auto"}}).json()["job_id"], timeout=240)
    assert j["status"] == "done", j
    assert client.get(f"/api/projects/{p['id']}/frame").headers["content-type"] == "image/jpeg"
    d = client.post(f"/api/projects/{p['id']}/detect-facecam").json()
    assert d["found"] is False and d["samples"] > 0  # synthetic test pattern has no face
    r = client.patch(f"/api/projects/{p['id']}", json={"stream_layout": {"cam": [0.7, 0.6, 1.0, 1.0], "cam_height": 0.35}})
    assert r.status_code == 200 and r.json()["options"]["stream_layout"]["cam"] == [0.7, 0.6, 1.0, 1.0]
    assert client.patch(f"/api/projects/{p['id']}", json={"stream_layout": {"cam": [0.5, 0.5, 0.51, 0.51]}}).status_code == 422
    r = client.post(f"/api/projects/{p['id']}/clips/bulk", json={"layout": "stream", "scope": "all", "render": True}).json()
    assert r["updated"] >= 1
    clips = client.get(f"/api/projects/{p['id']}/clips").json()
    assert all(cl["edit"]["layout"] == "stream" for cl in clips)
    for job in client.get(f"/api/jobs?project_id={p['id']}").json():
        if job["kind"] == "render":
            assert wait_job(client, job["id"])["status"] == "done"
    c0 = client.get(f"/api/clips/{clips[0]['id']}").json()
    assert c0["analysis"]["layout_used"] == "stream" and c0["analysis"]["stream"]["cam"] == [0.7, 0.6, 1.0, 1.0]
    # preview frame works with the stream layout
    pr = client.post(f"/api/clips/{c0['id']}/preview-frame", json={"t": 1.0, "edit": {"layout": "stream"}})
    assert pr.status_code == 200 and pr.headers["content-type"] == "image/jpeg"
    client.patch(f"/api/projects/{p['id']}", json={"clear_stream_layout": True})
    assert "stream_layout" not in client.get(f"/api/projects/{p['id']}").json()["options"]
