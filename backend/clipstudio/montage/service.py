"""Montage projects: song + several source videos → beat-synced 9:16 variants (Phase 3).

A montage is a project with ``source_type = "montage"``. Its options hold the montage settings; each
variant is a clip whose ``edit.montage_plan`` is the cut list. Clips reuse the normal clip features:
campaign logo/disclosure overlays, compliance checks, captions for posting and export packages.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from pathlib import Path

from ..analysis import action as act
from ..analysis.beats import SongAnalysis, cached_analysis
from ..campaigns.service import campaign_brief
from ..cancel import CancelToken
from ..config import MODE_PRESETS
from ..context import AppContext
from ..db import new_id
from ..errors import ClipStudioError, MediaError
from ..jobs.progress import Progress
from ..media import ffmpeg as ff
from ..media.overlays import DisclosureText
from ..media.render import _encoder_args, make_thumbnail
from ..storage import GB, MB, ensure_space
from .planner import Moment, moments_from_curves, plan
from .render import render_montage

log = logging.getLogger(__name__)

MONTAGE_STAGES: list[tuple[str, str, float]] = [
    ("song", "Finding beats and the drop", 0.05),
    ("download", "Downloading linked videos", 0.2),
    ("sources", "Finding the best shots", 0.3),
    ("plan", "Cutting shots on the beat", 0.05),
    ("render", "Rendering variants", 0.4),
]

DEFAULTS = {"length": 15.0, "drop_at": 0.4, "style": "normal", "pov": "", "variants": 3, "audio": "song", "flash": True, "sources": [], "song": {}}

MONTAGE_CRITERIA = {
    "hook": "Opening shot energy",
    "context": "Cuts on the beat",
    "emotion": "Drop impact",
    "value": "Shot variety",
    "story": "Build-up to the drop",
    "visual": "Shot energy",
    "audio": "Song section",
}


def options(project: dict) -> dict:
    return DEFAULTS | ((project.get("options") or {}).get("montage") or {})


def _file_key(p: Path) -> str:
    st = p.stat()
    return hashlib.sha1(f"{p.resolve()}:{st.st_size}:{int(st.st_mtime)}".encode()).hexdigest()[:16]


def song_for(ctx: AppContext, project: dict) -> tuple[Path, float | None, str]:
    """(song file, pinned start or None, display name)."""
    o = options(project)
    song = o.get("song") or {}
    if song.get("kind") == "campaign":
        _, brief = campaign_brief(ctx, project)
        if brief is None:
            raise ClipStudioError("This montage uses a campaign sound, but it isn't in a campaign.")
        s = next((x for x in brief.sounds if x.id == song.get("sound_id")), None) or next((x for x in brief.sounds if x.file), None)
        if s is None or not s.file or not Path(s.file).exists():
            raise ClipStudioError("The campaign has no sound file. Upload it on the campaign page, or upload a song to this montage.")
        return Path(s.file), (s.start if song.get("use_cut") else None), s.name
    if song.get("path") and Path(song["path"]).exists():
        return Path(song["path"]), (float(song["start"]) if song.get("use_cut") and song.get("start") is not None else None), song.get("name") or Path(song["path"]).name
    raise ClipStudioError("Add a song: upload one, or pick a campaign sound.")


def source_files(ctx: AppContext, project: dict) -> list[tuple[Path, str]]:
    """Video files of the selected source projects (full source, or downloaded sections)."""
    from ..jobs.pipeline import _sections_index

    out: list[tuple[Path, str]] = []
    for pid in options(project).get("sources") or []:
        sp = ctx.db.one("SELECT * FROM projects WHERE id = ?", (pid,))
        if sp is None:
            continue
        label = (sp["name"] or pid)[:40]
        if sp["source_path"] and Path(sp["source_path"]).exists():
            out.append((Path(sp["source_path"]), label))
            continue
        _, secs = _sections_index(ctx, pid)
        for i, s in enumerate(secs):
            out.append((Path(s["path"]), f"{label} #{i + 1}"))
    return out


def _needs_download(ctx: AppContext, sp: dict) -> bool:
    from ..api.projects import _has_sections

    if sp["source_type"] != "url" or not sp["source_url"]:
        return False
    if sp["source_path"] and Path(sp["source_path"]).exists():
        return False
    return not _has_sections(ctx, sp)


def download_sources(ctx: AppContext, project: dict, prog: Progress, cancel: CancelToken) -> None:
    """Linked videos added for the montage are downloaded in full here (shots can come from anywhere in them)."""
    from ..media import download as dl

    settings = ctx.settings.get()
    todo = [sp for pid in options(project).get("sources") or [] if (sp := ctx.db.one("SELECT * FROM projects WHERE id = ?", (pid,))) and _needs_download(ctx, sp)]
    if not todo:
        prog.skip("download", "Nothing to download")
        return
    prog.start("download")
    for k, sp in enumerate(todo):
        cancel.check()
        label = f"Video {k + 1} of {len(todo)}"
        info = dl.inspect(sp["source_url"], settings.max_download_height)
        need = info.get("estimated_bytes") or int((info["duration"] or 600) * 1.2 * MB)
        ensure_space(ctx.paths.downloads, need, int(settings.min_free_gb * GB), "the download")
        src = dl.download(sp["source_url"], ctx.paths.downloads, max_height=settings.max_download_height, on_progress=lambda q, k=k: prog.update("download", (k + q) / len(todo), f"{label}: {q * 100:.0f}%"), cancel=cancel)
        vi = ff.probe(src)
        name = sp["name"] if sp["name"] != sp["source_url"] else info["title"]
        ctx.db.update("projects", sp["id"], {"source_path": str(src), "source_owned": 1, "name": name, "duration": vi.duration, "width": vi.width, "height": vi.height, "fps": vi.fps})
    prog.done("download", f"{len(todo)} video(s) downloaded")


def moments_for(ctx: AppContext, files: list[tuple[Path, str]], prog: Progress | None = None, cancel: CancelToken | None = None) -> list[Moment]:
    out: list[Moment] = []
    for k, (f, label) in enumerate(files):
        key = _file_key(f)
        cache = ctx.paths.cache / f"montage_src_{key}.json"
        curves = act.load_curves(cache)
        dur = ff.probe(f).duration
        if curves is None:
            kw = {"cancel": cancel} if cancel is not None else {}
            curves = act.video_curves(f, dur, on_progress=(lambda p, k=k: prog.update("sources", (k + p) / len(files), f"Video {k + 1} of {len(files)}")) if prog else None, **kw)
            act.save_curves(curves, cache)
        out += moments_from_curves(curves, file=str(f), label=label, file_dur=dur)
        if prog:
            prog.update("sources", (k + 1) / len(files), f"Video {k + 1} of {len(files)}")
    return out


def song_analysis(ctx: AppContext, song: Path) -> SongAnalysis:
    return cached_analysis(song, ctx.paths.cache, _file_key(song))


def _explain(p: dict, song: SongAnalysis, song_name: str) -> dict:
    slots = p["slots"]
    files = {s["file"] for s in slots}
    energies = [s["energy"] for s in slots] or [0]
    impact = next((s for s in slots if s["impact"]), None)
    scores = {
        "hook": round(min(100.0, slots[0]["energy"] * 100), 1),
        "context": 100.0,
        "emotion": round(min(100.0, (impact["energy"] if impact else 0.4) * 100), 1),
        "value": round(min(100.0, 40 + 20 * len(files)), 1),
        "story": 90.0 if p["drop"] is not None else 55.0,
        "visual": round(min(100.0, sum(energies) / len(energies) * 120), 1),
        "audio": round(min(100.0, 60 + 40 * song.drop_strength), 1),
    }
    total = round(0.15 * scores["hook"] + 0.15 * scores["context"] + 0.2 * scores["emotion"] + 0.1 * scores["value"] + 0.15 * scores["story"] + 0.15 * scores["visual"] + 0.1 * scores["audio"], 1)
    reasons = [f"{len(slots)} cuts on the beat at {song.tempo:.0f} BPM ({p['style']})", f"Shots from {len(files)} video(s)"]
    if p["drop"] is not None:
        reasons.insert(0, f"The drop lands at {p['drop']:.1f}s with the strongest shot" + (" and a flash" if impact else ""))
    reasons.append(f"Song: {song_name}, from {p['song_start']:.1f}s to {p['song_end']:.1f}s")
    weak = [] if p["drop"] is not None else ["No clear drop found in the song; cuts follow the beat only"]
    if len(files) == 1:
        weak.append("All shots come from one video; add more sources for variety")
    return {"scores": scores, "total": total, "reasons": reasons, "weaknesses": weak}


def run_montage(ctx: AppContext, job: dict, cancel: CancelToken) -> None:
    from ..jobs.pipeline import render_one

    db = ctx.db
    project = db.one("SELECT * FROM projects WHERE id = ?", (job["project_id"],))
    o = options(project)
    settings = ctx.settings.get()
    prog = Progress(db, job["id"], MONTAGE_STAGES)
    db.update("projects", project["id"], {"status": "processing", "error": None})
    try:
        prog.start("song")
        song, pinned, song_name = song_for(ctx, project)
        sa = song_analysis(ctx, song)
        prog.done("song", f"{sa.tempo:.0f} BPM · " + (f"drop at {sa.drop:.1f}s" if sa.drop is not None else "no clear drop"))
        cancel.check()

        download_sources(ctx, project, prog, cancel)
        prog.start("sources")
        files = source_files(ctx, project)
        if not files:
            raise MediaError("None of the selected videos has a downloaded video file. Add videos (upload, link or file).")
        moments = moments_for(ctx, files, prog, cancel)
        if not moments:
            raise MediaError("No usable shots found in the selected videos.")
        prog.done("sources", f"{len(moments)} candidate shots from {len(files)} file(s)")

        prog.start("plan")
        _, brief = campaign_brief(ctx, project)
        length = float(o["length"])
        if brief and brief.min_duration and length < brief.min_duration:
            length = brief.min_duration + 0.5
        if brief and brief.max_duration:
            length = min(length, brief.max_duration)
        from ..campaigns.posting import posted_ids

        posted = posted_ids(db)  # variants already posted stay (with their post history); new ones follow them
        kept = 0
        for old in db.query("SELECT id, render_path, thumb_path FROM clips WHERE project_id = ?", (project["id"],)):
            if old["id"] in posted:
                kept += 1
                continue
            for p in (old["render_path"], old["thumb_path"]):
                if p:
                    Path(p).unlink(missing_ok=True)
            db.execute("DELETE FROM clips WHERE id = ?", (old["id"],))
        rank0 = ((db.one("SELECT MAX(rank) AS r FROM clips WHERE project_id = ?", (project["id"],)) or {}).get("r") or 0) if kept else 0
        avoid: set[str] = set()
        clip_ids = []
        n = max(1, min(8, int(o["variants"])))
        for v in range(n):
            p = plan(sa, moments, length=length, drop_at=float(o["drop_at"]), style=o["style"], seed=v * 7919 + 13, song_start=pinned, avoid=avoid)
            avoid |= {s["moment"] for s in p["slots"]}
            clip_ids.append(insert_variant(ctx, project, p, sa, song_name, rank0 + v + 1, o, brief))
        prog.done("plan", f"{n} variant(s) · {len(p['slots'])} cuts each")
        db.update("projects", project["id"], {"duration": length})

        if (project.get("options") or {}).get("auto_render", True):
            prog.start("render")
            fails = 0
            for k, cid in enumerate(clip_ids):
                cancel.check()
                clip = db.one("SELECT * FROM clips WHERE id = ?", (cid,))
                try:
                    render_one(ctx, db.one("SELECT * FROM projects WHERE id = ?", (project["id"],)), clip, cancel, on_progress=lambda q, k=k: prog.update("render", (k + q) / len(clip_ids), f"Variant {k + 1} of {len(clip_ids)}"))
                except Exception:
                    if cancel.cancelled:
                        raise
                    fails += 1
                    log.exception("Montage render failed for %s", cid)
            prog.done("render", f"{len(clip_ids) - fails} rendered" + (f", {fails} failed" if fails else ""))
        else:
            prog.skip("render", "Auto-render off")
        db.update("projects", project["id"], {"status": "ready", "error": None})
    except Exception as e:
        prog.fail(str(e))
        raise


def insert_variant(ctx: AppContext, project: dict, p: dict, sa: SongAnalysis, song_name: str, rank: int, o: dict, brief) -> str:
    ex = _explain(p, sa, song_name)
    th = ctx.settings.get().thresholds
    from ..analysis.scoring import quality_label

    cid = new_id()
    now = time.time()
    pov = o.get("pov") or ""
    tags = list(brief.required_hashtags) if brief else []
    ctx.db.insert(
        "clips",
        {
            "id": cid,
            "project_id": project["id"],
            "rank": rank,
            "start": 0.0,
            "end": p["length"],
            "score": ex["total"],
            "analysis": {
                "scores": ex["scores"],
                "criteria": MONTAGE_CRITERIA,
                "weights": {"hook": 15, "context": 15, "emotion": 20, "value": 10, "story": 15, "visual": 15, "audio": 10},
                "reasons": ex["reasons"],
                "weaknesses": ex["weaknesses"],
                "label": quality_label(ex["total"], th.excellent, th.strong, th.review),
                "heuristic_score": ex["total"],
                "transcript": "",
                "sentences": [],
                "llm": False,
                "origin": "montage",
                "mode": "montage",
            },
            "title": (pov or f"{project['name']} edit")[:120],
            "hook": pov,
            "description": f"Edit to {song_name}.",
            "category": "Montage",
            "meta": {"hooks": [pov] if pov else [], "hashtags": tags + ["#edit", "#fyp"], "keywords": []},
            "edit": {
                "aspect_ratio": "9:16",
                "layout": "center",
                "caption_preset": "bold",
                "output_height": 1080 if brief else int((project.get("options") or {}).get("output_height") or ctx.settings.get().output_height),
                "captions": False,
                "montage": {"pov": pov, "audio": o.get("audio", "song"), "flash": bool(o.get("flash", True)), "style": p["style"], "drop_at": p["drop_at"], "seed": p["seed"]},
                "montage_plan": p,
            },
            "status": "candidate",
            "render_status": "pending",
            "created_at": now,
            "updated_at": now,
        },
    )
    return cid


def replan(ctx: AppContext, project: dict, clip: dict, changes: dict) -> dict:
    """Re-cut one variant (new style / drop position / shuffle). Returns the updated clip row."""
    o = options(project)
    song, pinned, song_name = song_for(ctx, project)
    sa = song_analysis(ctx, song)
    moments = moments_for(ctx, source_files(ctx, project))
    if not moments:
        raise MediaError("No usable shots found in the selected videos.")
    m = dict((clip["edit"] or {}).get("montage") or {})
    for k in ("pov", "audio", "flash", "style", "drop_at"):
        if changes.get(k) is not None:
            m[k] = changes[k]
    old_plan = (clip["edit"] or {}).get("montage_plan") or {}
    seed = int(m.get("seed", 0)) + (1 if changes.get("reshuffle") else 0) * 104729
    m["seed"] = seed
    others = {s["moment"] for c in ctx.db.query("SELECT edit FROM clips WHERE project_id = ? AND id != ?", (project["id"], clip["id"])) for s in ((c["edit"] or {}).get("montage_plan") or {}).get("slots", [])}
    needs_plan = changes.get("reshuffle") or any(changes.get(k) is not None and changes.get(k) != old_plan.get(k) for k in ("style", "drop_at"))
    p = plan(sa, moments, length=float(old_plan.get("length") or o["length"]), drop_at=float(m.get("drop_at", o["drop_at"])), style=m.get("style", o["style"]), seed=seed, song_start=pinned, avoid=others) if needs_plan or not old_plan else old_plan
    edit = dict(clip["edit"] or {}) | {"montage": m, "montage_plan": p}
    upd = {"edit": edit}
    if needs_plan:
        ex = _explain(p, sa, song_name)
        upd |= {"score": ex["total"], "analysis": (clip["analysis"] or {}) | {"scores": ex["scores"], "reasons": ex["reasons"], "weaknesses": ex["weaknesses"]}}
    if changes.get("pov") is not None:
        upd |= {"hook": m["pov"], "title": (m["pov"] or clip["title"])[:120]}
    if clip["render_status"] in ("done", "failed"):
        upd["render_status"] = "stale"
    ctx.db.update("clips", clip["id"], upd)
    return ctx.db.one("SELECT * FROM clips WHERE id = ?", (clip["id"],))


def add_variant(ctx: AppContext, project: dict) -> str:
    o = options(project)
    song, pinned, song_name = song_for(ctx, project)
    sa = song_analysis(ctx, song)
    moments = moments_for(ctx, source_files(ctx, project))
    used = {s["moment"] for c in ctx.db.query("SELECT edit FROM clips WHERE project_id = ?", (project["id"],)) for s in ((c["edit"] or {}).get("montage_plan") or {}).get("slots", [])}
    rank = ((ctx.db.one("SELECT MAX(rank) AS r FROM clips WHERE project_id = ?", (project["id"],)) or {}).get("r") or 0) + 1
    _, brief = campaign_brief(ctx, project)
    p = plan(sa, moments, length=float(project.get("duration") or o["length"]), drop_at=float(o["drop_at"]), style=o["style"], seed=rank * 7919 + 13, song_start=pinned, avoid=used)
    return insert_variant(ctx, project, p, sa, song_name, rank, o, brief)


def render_variant(ctx: AppContext, project: dict, clip: dict, cancel: CancelToken, on_progress=None) -> dict:
    """Render one montage clip; returns analysis additions."""
    settings = ctx.settings.get()
    edit = clip["edit"] or {}
    p = edit.get("montage_plan")
    if not p:
        raise ClipStudioError("This montage variant has no cut plan. Re-plan it.")
    m = edit.get("montage") or {}
    camp, brief = campaign_brief(ctx, project)
    from ..campaigns.service import features

    feat = features(ctx, project, clip, brief)
    song, _, song_name = song_for(ctx, project)
    out_dir = ctx.paths.project_dir(project["id"]) / "clips"
    out = out_dir / f"{clip['id']}.mp4"
    ensure_space(out_dir, int(p["length"] * 2 * MB), int(settings.min_free_gb * GB), "rendering")
    mode = MODE_PRESETS[settings.mode]
    x264 = mode.get("x264_preset")
    height = int(edit.get("output_height", 1080))

    class _S:  # minimal spec for encoder args
        encoder = "x264" if x264 else "videotoolbox"
        x264_preset = x264 or "veryfast"

    w, h = height, int(round(height * 16 / 9))
    disclosure = DisclosureText(brief.disclosure.text, brief.disclosure.corner) if brief and feat["disclosure"] else None
    logo = brief.logo.model_dump() if brief and feat["logo"] and brief.logo.file and Path(brief.logo.file).exists() else None
    render_montage(p, out, height=height, song=song, audio=m.get("audio", "song"), pov=m.get("pov", ""), flash=bool(m.get("flash", True)), disclosure=disclosure, logo=logo, encoder_args=_encoder_args(_S, w, h), on_progress=on_progress, cancel=cancel)  # type: ignore[arg-type]
    thumb = make_thumbnail(out, out_dir / f"{clip['id']}.jpg", t=min(float(p.get("drop") or 1.0) + 0.3, p["length"] / 2))
    return {"render_path": str(out), "thumb_path": str(thumb), "analysis": {"output": f"{w}x{h}", "layout_used": "montage", "song": song_name, "song_start": p["song_start"], "music_used": None, "sfx_used": []}}
