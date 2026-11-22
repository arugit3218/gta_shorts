"""The processing pipeline: source → audio → transcript → highlights → metadata → renders.

Each stage is idempotent and cached on disk, so a job interrupted by a crash or restart can be
re-run and will skip work that's already done (download, transcript, audio profile).
Only one heavy model is ever in memory: transcription runs in a child process that exits
before analysis/rendering start, and the optional LLM is asked to unload after each call.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from pathlib import Path

from ..analysis import metadata as meta_gen
from ..analysis.audio_features import AudioProfile, analyse_wav
from ..analysis.highlights import find_highlights
from ..analysis.scoring import CRITERIA_LABELS, quality_label
from ..analysis.segmentation import normalise_sentences
from ..cancel import CancelToken
from ..config import DURATION_RANGES, MODE_PRESETS, UserSettings
from ..context import AppContext
from ..db import new_id
from ..errors import Cancelled, ClipStudioError, MediaError
from ..llm import ollama
from ..media import download as dl
from ..media import ffmpeg as ff
from ..media.captions import CapWord, resolve_style
from ..media.render import RenderSpec, make_thumbnail, render_clip
from ..storage import GB, MB, ensure_space, estimate_processing, fingerprint
from ..transcription.service import TranscriptionService
from ..transcription.types import Transcript, to_srt
from .progress import PROCESS_STAGES, RENDER_STAGES, Progress

log = logging.getLogger(__name__)


def project_options(settings: UserSettings, overrides: dict | None) -> dict:
    base = {
        "duration_preset": settings.duration_preset,
        "max_clips": settings.max_clips,
        "min_score": settings.min_score,
        "aspect_ratio": settings.aspect_ratio,
        "layout": settings.layout,
        "caption_preset": settings.caption_preset,
        "output_height": settings.output_height,
        "auto_render": settings.auto_render,
        "llm_enabled": settings.llm_enabled,
    }
    allowed = set(base)
    return base | {k: v for k, v in (overrides or {}).items() if k in allowed and v is not None}


# --------------------------------------------------------------------------- process job
def run_process(ctx: AppContext, job: dict, cancel: CancelToken) -> None:
    db = ctx.db
    project = db.one("SELECT * FROM projects WHERE id = ?", (job["project_id"],))
    if project is None:
        raise ClipStudioError("Project no longer exists")
    settings = ctx.settings.get()
    opts = project_options(settings, project["options"])
    min_free = int(settings.min_free_gb * GB)
    pdir = ctx.paths.project_dir(project["id"])
    tmp = ctx.paths.temp_dir(project["id"])
    prog = Progress(db, job["id"], PROCESS_STAGES)
    db.update("projects", project["id"], {"status": "processing", "error": None})

    try:
        # 1. Source --------------------------------------------------------------------
        src = Path(project["source_path"]) if project["source_path"] else None
        if project["source_type"] == "url" and (src is None or not src.exists()):
            prog.start("download", "Contacting source…")
            info = dl.inspect(project["source_url"], settings.max_download_height)
            need = info.get("estimated_bytes") or int((info["duration"] or 600) * 1.2 * MB)
            ensure_space(ctx.paths.downloads, need, min_free, "the download")
            src = dl.download(
                project["source_url"],
                ctx.paths.downloads,
                max_height=settings.max_download_height,
                on_progress=lambda p: prog.update("download", p, f"{p * 100:.0f}% downloaded"),
                cancel=cancel,
            )
            db.update("projects", project["id"], {"source_path": str(src), "source_owned": 1, "name": project["name"] if project["name"] != project["source_url"] else info["title"]})
            prog.done("download", f"{src.stat().st_size / MB:,.0f} MB")
        else:
            prog.skip("download", "Local file")
        if src is None or not src.exists():
            raise MediaError("Source video is missing. It may have been moved or deleted.")
        cancel.check()

        info = ff.probe(src)
        if not info.has_audio:
            raise MediaError("This video has no audio track, so there's nothing to transcribe.")
        fp = project["fingerprint"] or fingerprint(src)
        db.update("projects", project["id"], {"duration": info.duration, "width": info.width, "height": info.height, "fps": info.fps, "fingerprint": fp})
        dmin, dmax = DURATION_RANGES[opts["duration_preset"]]
        est = estimate_processing(info.duration, clips=opts["max_clips"], clip_seconds=(dmin + dmax) / 2, output_height=opts["output_height"])
        ensure_space(ctx.paths.work, est.total, min_free, "processing")

        # 2. Audio ----------------------------------------------------------------------
        svc = TranscriptionService(ctx.paths.cache, allow_downloads=ctx.env.allow_model_downloads)
        model_id = settings.transcription_model
        profile_path = ctx.paths.cache / f"audio_{fp}.json"
        cached_tr = svc.cached(fp, model_id)
        wav = tmp / "audio.wav"
        if cached_tr is not None and profile_path.exists():
            prog.skip("audio", "Cached")
        else:
            prog.start("audio")
            ff.extract_audio(src, wav, duration=info.duration, cancel=cancel, on_progress=lambda p: prog.update("audio", p))
            AudioProfile.save(analyse_wav(wav), profile_path)
            prog.done("audio")
        cancel.check()

        # 3. Transcribe -----------------------------------------------------------------
        if cached_tr is not None:
            transcript = cached_tr
            prog.skip("transcribe", "Using cached transcript")
        else:
            prog.start("transcribe", "Loading speech model…")
            # Low mode always uses the user's (smaller) chunk size to cap RAM; other modes use the preset.
            chunk = settings.transcription_chunk_seconds if settings.mode.value == "low" else MODE_PRESETS[settings.mode]["transcription_chunk_seconds"]
            t0 = time.time()
            transcript = svc.transcribe(
                wav,
                fingerprint=fp,
                model_id=model_id,
                duration=info.duration,
                chunk_seconds=chunk,
                on_progress=lambda p: prog.update("transcribe", p, f"{p * 100:.0f}% of audio"),
                cancel=cancel,
            )
            prog.done("transcribe", f"{len(transcript.words):,} words in {time.time() - t0:.0f}s")
        (pdir / "transcript.json").write_text(json.dumps(transcript.to_dict()))
        (pdir / "transcript.srt").write_text(to_srt(transcript))
        if not settings.keep_intermediates:
            wav.unlink(missing_ok=True)
        cancel.check()
        if len(transcript.words) < 10:
            raise ClipStudioError("Almost no speech was detected, so no clips could be found.")

        # 4–6. Analyse / highlights / score --------------------------------------------
        prog.start("analyse")
        audio = AudioProfile.load(profile_path) if profile_path.exists() else AudioProfile.empty()
        prog.done("analyse", f"{len(transcript.sentences)} sentences")
        prog.start("highlights")
        weights = settings.weights.normalised()
        candidates, stats = find_highlights(
            transcript,
            audio,
            source_height=info.height,
            weights=weights,
            min_dur=dmin,
            max_dur=dmax,
            max_clips=opts["max_clips"],
            min_score=opts["min_score"],
        )
        prog.done("highlights", f"{stats['windows']:,} windows evaluated")
        prog.start("score")
        if not candidates:
            raise ClipStudioError(stats.get("reason") or f"No {dmin:.0f}–{dmax:.0f}s moments found. Try a different clip length.")
        prog.done("score", f"{len(candidates)} clips selected" + (f" — {stats['reason']}" if stats.get("reason") else ""))

        # 7. Metadata (extractive; optional local LLM) -----------------------------------
        prog.start("metadata")
        sentences = normalise_sentences(transcript)
        idf = meta_gen.build_idf(sentences)
        use_llm = bool(opts["llm_enabled"])
        llm_note = ""
        if use_llm:
            ok, why = ollama.available(settings.llm_url, settings.llm_model)
            if not ok:
                use_llm, llm_note = False, f"Local AI unavailable ({why}); used extractive titles."
        # Replace previous clips of this project (re-processing).
        for old in db.query("SELECT id, render_path, thumb_path FROM clips WHERE project_id = ?", (project["id"],)):
            for p in (old["render_path"], old["thumb_path"]):
                if p:
                    Path(p).unlink(missing_ok=True)
        db.execute("DELETE FROM clips WHERE project_id = ?", (project["id"],))
        clip_ids: list[str] = []
        th = settings.thresholds
        for rank, c in enumerate(candidates, 1):
            cancel.check()
            clip_sents = sentences[c.i0 : c.i1]
            m = meta_gen.generate(clip_sents, transcript.words, c.score.category, idf)
            total = c.score.total
            reasons = list(c.score.reasons)
            ai = None
            if use_llm:
                ai = ollama.enrich(settings.llm_url, settings.llm_model, c.text)
                if ai:
                    m["hooks"] = ai.get("hooks", m["hooks"])
                    m["title"] = ai.get("title", m["title"])
                    m["description"] = ai.get("description", m["description"])
                    m["hashtags"] = ai.get("hashtags", m["hashtags"])
                    if "rating" in ai:
                        adj = max(-5.0, min(5.0, (ai["rating"] - 5.5) * 1.2))
                        total = round(max(0.0, min(100.0, total + adj)), 1)
                        reasons.append(f"Local AI review {ai['rating']}/10: {ai.get('rating_reason', '')}".strip())
            prog.update("metadata", rank / len(candidates))
            cid = new_id()
            clip_ids.append(cid)
            now = time.time()
            db.insert(
                "clips",
                {
                    "id": cid,
                    "project_id": project["id"],
                    "rank": rank,
                    "start": c.start,
                    "end": c.end,
                    "score": total,
                    "analysis": {
                        "scores": c.score.scores,
                        "criteria": CRITERIA_LABELS,
                        "weights": {k: round(v * 100, 1) for k, v in weights.items()},
                        "reasons": reasons,
                        "weaknesses": c.score.weaknesses,
                        "label": quality_label(total, th.excellent, th.strong, th.review),
                        "heuristic_score": c.score.total,
                        "transcript": c.text,
                        "sentences": c.extras.get("sentences", []),
                        "llm": bool(ai),
                    },
                    "title": m["title"],
                    "hook": m["hooks"][0] if m["hooks"] else "",
                    "description": m["description"],
                    "category": (ai or {}).get("category") or c.score.category,
                    "meta": {"hooks": m["hooks"], "hashtags": m["hashtags"], "keywords": m.get("keywords", [])},
                    "edit": {
                        "aspect_ratio": opts["aspect_ratio"],
                        "layout": opts["layout"],
                        "caption_preset": opts["caption_preset"],
                        "output_height": opts["output_height"],
                        "captions": True,
                    },
                    "status": "candidate",
                    "render_status": "pending",
                    "created_at": now,
                    "updated_at": now,
                },
            )
        prog.done("metadata", llm_note or ("Local AI + extractive" if use_llm else "Extractive (from the speaker's own words)"))

        # 8. Captions: build word tracks (burned in during render) ---------------------
        prog.start("captions")
        prog.done("captions", f"Word-level captions for {len(clip_ids)} clips")

        # 9. Render -----------------------------------------------------------------------
        if opts["auto_render"]:
            prog.start("render")
            failures = 0
            for k, cid in enumerate(clip_ids):
                cancel.check()
                clip = db.one("SELECT * FROM clips WHERE id = ?", (cid,))
                assert clip is not None
                try:
                    render_one(
                        ctx, db.one("SELECT * FROM projects WHERE id = ?", (project["id"],)), clip, cancel,
                        on_progress=lambda p, k=k: prog.update("render", (k + p) / len(clip_ids), f"Clip {k + 1} of {len(clip_ids)}"),
                    )
                except Cancelled:
                    raise
                except Exception:
                    if cancel.cancelled:
                        raise
                    failures += 1  # one bad clip shouldn't fail the whole project
                    log.exception("Render failed for clip %s", cid)
            prog.done("render", f"{len(clip_ids) - failures} rendered" + (f", {failures} failed" if failures else ""))
        else:
            prog.skip("render", "Auto-render off — render clips from the gallery")

        db.update("projects", project["id"], {"status": "ready", "error": None})
    except Exception as e:
        prog.fail(str(e))
        raise
    finally:
        if not settings.keep_intermediates:
            shutil.rmtree(tmp, ignore_errors=True)


# --------------------------------------------------------------------------- render
def clip_words(ctx: AppContext, project: dict, clip: dict) -> list[CapWord]:
    """Caption words relative to clip start; user caption edits take precedence."""
    edited = clip["edit"].get("caption_words")
    start, end = clip["start"], clip["end"]
    if edited:
        src = edited
    else:
        tpath = ctx.paths.project_dir(project["id"]) / "transcript.json"
        if not tpath.exists():
            return []
        t = Transcript.from_dict(json.loads(tpath.read_text()))
        src = [{"w": w.w, "s": w.s, "e": w.e} for w in t.words]
    return [CapWord(w["w"], max(0.0, w["s"] - start), min(end, w["e"]) - start) for w in src if w["e"] > start + 0.05 and w["s"] < end - 0.05]


def render_spec(ctx: AppContext, project: dict, clip: dict) -> RenderSpec:
    settings = ctx.settings.get()
    edit = clip["edit"]
    src = Path(project["source_path"] or "")
    if not src.exists():
        raise MediaError("Source video is missing (deleted or moved), so this clip can't be rendered.")
    mode = MODE_PRESETS[settings.mode]
    x264 = mode.get("x264_preset")
    music = edit.get("music_path")
    return RenderSpec(
        src=src,
        start=clip["start"],
        end=clip["end"],
        src_w=project["width"],
        src_h=project["height"],
        src_fps=project["fps"] or 30.0,
        has_audio=True,
        aspect=edit.get("aspect_ratio", settings.aspect_ratio),
        height=int(edit.get("output_height", settings.output_height)),
        layout=edit.get("layout", settings.layout),
        words=clip_words(ctx, project, clip),
        style=resolve_style(edit.get("caption_preset", settings.caption_preset), edit.get("caption_style")),
        captions=bool(edit.get("captions", True)),
        normalise_audio=settings.normalise_audio,
        max_fps=60.0 if settings.mode.value == "high" else 30.0,
        encoder="x264" if x264 else "videotoolbox",
        x264_preset=x264 or "veryfast",
        music=Path(music) if music and Path(music).exists() else None,
        music_volume=float(edit.get("music_volume", 0.12)),
    )


def render_one(ctx: AppContext, project: dict, clip: dict, cancel: CancelToken, on_progress=None) -> None:
    db = ctx.db
    settings = ctx.settings.get()
    out_dir = ctx.paths.project_dir(project["id"]) / "clips"
    out = out_dir / f"{clip['id']}.mp4"
    db.update("clips", clip["id"], {"render_status": "rendering", "render_error": None})
    try:
        spec = render_spec(ctx, project, clip)
        ensure_space(out_dir, int(spec.duration * 1.2 * MB), int(settings.min_free_gb * GB), "rendering")
        res = render_clip(spec, out, on_progress=on_progress, cancel=cancel, face_track=MODE_PRESETS[settings.mode].get("face_tracking", True))
        thumb = make_thumbnail(out, out_dir / f"{clip['id']}.jpg", t=min(1.0, spec.duration / 2))
        analysis = clip["analysis"] | {"layout_used": res.layout_used, "face_ratio": round(res.face_ratio, 2)}
        db.update("clips", clip["id"], {"render_status": "done", "render_path": str(out), "thumb_path": str(thumb), "analysis": analysis})
    except Exception as e:
        db.update("clips", clip["id"], {"render_status": "failed" if not cancel.cancelled else "pending", "render_error": str(e)[:500]})
        raise


def run_render(ctx: AppContext, job: dict, cancel: CancelToken) -> None:
    db = ctx.db
    prog = Progress(db, job["id"], RENDER_STAGES)
    clip = db.one("SELECT * FROM clips WHERE id = ?", (job["payload"]["clip_id"],))
    if clip is None:
        raise ClipStudioError("Clip no longer exists")
    project = db.one("SELECT * FROM projects WHERE id = ?", (clip["project_id"],))
    prog.start("render")
    try:
        render_one(ctx, project, clip, cancel, on_progress=lambda p: prog.update("render", p))
    except Exception as e:
        prog.fail(str(e))
        raise
    prog.done("render")
