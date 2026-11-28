"""The processing pipeline: source → audio → transcript → highlights → metadata → [sections] → renders.

Each stage is idempotent and cached on disk, so a job interrupted by a crash or restart can be
re-run and will skip work that's already done (download, transcript, audio profile, sections).
Only one heavy model is ever in memory: transcription runs in a child process that exits
before analysis/rendering start, and the optional LLM is asked to unload after each call.

Smart downloads (default for links): download the audio only (~1 MB/min), transcribe and pick
clips, then download just the video sections the clips need (each clip ± 20 s, for trimming).
Any failure there falls back to downloading the whole video.
"""

from __future__ import annotations

import json
import logging
import shutil
import time
from dataclasses import replace
from pathlib import Path

from ..analysis import action as act
from ..analysis import metadata as meta_gen
from ..analysis.audio_features import AudioProfile, analyse_wav
from ..analysis.highlights import Candidate, _overlap, find_highlights, score_range, target_count
from ..analysis.scoring import CRITERIA_LABELS, quality_label
from ..analysis.segmentation import normalise_sentences
from ..campaigns.posting import posted_ids
from ..campaigns.service import campaign_brief, caption_style_for, features
from ..cancel import CancelToken
from ..config import DURATION_RANGES, MODE_PRESETS, UserSettings
from ..context import AppContext
from ..db import new_id
from ..errors import Cancelled, ClipStudioError, MediaError
from ..llm import ollama
from ..media import download as dl
from ..media import ffmpeg as ff
from ..media.align import find_offset
from ..media.burned_text import TextBand
from ..media.captions import CapWord
from ..media.library import audio_duration, mood_for, pick_track, track_path
from ..media.overlays import DisclosureText, EndText, HookText
from ..media.polish import audio_envelope, peak_window
from ..media.render import RenderResult, RenderSpec, concat_parts, make_thumbnail, plan_timeline, render_clip
from ..media.sfx import GAIN as SFX_GAIN
from ..media.sfx import TYPES as SFX_TYPES
from ..media.sfx import Cue, cues_from_dicts, plan_cues, resolve_file
from ..storage import GB, MB, ensure_space, estimate_processing, fingerprint
from ..transcription.service import TranscriptionService
from ..transcription.subtitles import transcript_from_cues
from ..transcription.types import Transcript, to_srt
from .progress import PROCESS_STAGES, RENDER_STAGES, Progress

log = logging.getLogger(__name__)

SECTION_PAD = 20.0  # seconds of video kept around each clip in smart mode (room to trim)


def with_campaign(ctx: AppContext, project: dict) -> dict:
    """The project's own options, with its campaign's style filling whatever the project didn't set itself."""
    from ..campaigns.service import campaign_brief
    from ..campaigns.styles import campaign_defaults

    own = dict(project.get("options") or {})
    _, brief = campaign_brief(ctx, project)
    if brief is None or project.get("source_type") == "montage":
        return own
    camp = campaign_defaults(brief)
    merged = camp | own
    merged["polish"] = camp["polish"] | (own.get("polish") or {})
    return merged


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
        "music": settings.music_default,
        "sfx": "auto" if settings.sfx_default else "off",
        "blur_burned": settings.blur_burned_default,
        "hook_overlay": settings.hook_overlay_default,
        "quick": False,
        "quick_start": None,
        "quick_end": None,
        "content_type": settings.content_type,
        "polish": settings.polish.model_dump(),
        "caption_style": None,
    }
    allowed = set(base)
    out = base | {k: v for k, v in (overrides or {}).items() if k in allowed and v is not None and k != "polish"}
    if (overrides or {}).get("polish"):
        out["polish"] = base["polish"] | {k: v for k, v in overrides["polish"].items() if v is not None}
    return out


# --------------------------------------------------------------------------- clip records
def insert_clip(ctx: AppContext, project: dict, c: Candidate, rank: int, m: dict, opts: dict, weights: dict, *, total: float | None = None, reasons: list[str] | None = None, origin: str = "auto", llm: bool = False, category: str | None = None) -> str:
    settings = ctx.settings.get()
    th = settings.thresholds
    total = c.score.total if total is None else total
    _, brief = campaign_brief(ctx, project)
    height = opts["output_height"]
    if brief:
        height = 1080 if brief.logo.mode == "template" else brief.output_height
    cid = new_id()
    now = time.time()
    ctx.db.insert(
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
                "criteria": c.extras.get("criteria") or CRITERIA_LABELS,
                "weights": c.extras.get("weights") or {k: round(v * 100, 1) for k, v in weights.items()},
                "mode": c.extras.get("mode", "speech"),
                **({"peak_t": c.extras["peak_t"]} if "peak_t" in c.extras else {}),
                "reasons": reasons if reasons is not None else list(c.score.reasons),
                "weaknesses": c.score.weaknesses,
                "label": quality_label(total, th.excellent, th.strong, th.review),
                "heuristic_score": c.score.total,
                "transcript": c.text,
                "sentences": c.extras.get("sentences", []),
                "llm": llm,
                "origin": origin,
            },
            "title": m.get("title", ""),
            "hook": (m.get("hooks") or [""])[0],
            "description": m.get("description", ""),
            "category": category or c.score.category,
            "meta": {"hooks": m.get("hooks", []), "hashtags": m.get("hashtags", []), "keywords": m.get("keywords", [])},
            "edit": {
                "aspect_ratio": "9:16" if brief else opts["aspect_ratio"],
                "layout": opts["layout"],
                "caption_preset": opts["caption_preset"],
                **({"caption_style": {k: v for k, v in opts["caption_style"].items() if v is not None}} if opts.get("caption_style") else {}),
                "output_height": height,
                "captions": True,
            },
            "status": "candidate",
            "render_status": "pending",
            "created_at": now,
            "updated_at": now,
        },
    )
    return cid


def load_transcript(ctx: AppContext, project_id: str) -> Transcript | None:
    p = ctx.paths.project_dir(project_id) / "transcript.json"
    return Transcript.from_dict(json.loads(p.read_text())) if p.exists() else None


def load_audio_profile(ctx: AppContext, project: dict) -> AudioProfile:
    fp = project.get("fingerprint")
    p = ctx.paths.cache / f"audio_{fp}.json"
    return AudioProfile.load(p) if fp and p.exists() else AudioProfile.empty()


def metadata_for(ctx: AppContext, project: dict, t: Transcript, c: Candidate) -> dict:
    sentences = normalise_sentences(t)
    idf = meta_gen.build_idf(sentences)
    return meta_gen.generate(sentences[c.i0 : c.i1], t.words, c.score.category, idf)


def action_metadata(m: dict, c: Candidate, project_name: str, rank: int) -> dict:
    """Titles/hooks for action clips: prefer the streamer's own reaction, else describe the moment."""
    import re as _re

    words = c.text.split()
    react = None
    low = c.text.lower()
    for p in act.REACTION_PHRASES:
        if p in low:
            react = p
            break
    reasons = " ".join(c.score.reasons).lower()
    kind = "Reaction" if "react" in reasons else "Kill feed moment" if "kill feed" in reasons else "Big moment" if "audio" in reasons else "Action"
    title = (react.upper().rstrip("!") + "!" if react else f"{kind} you have to see")
    hooks = [h for h in [react.capitalize() + "!" if react else None, f"Wait for it… 👀", f"{kind} at its best"] if h]
    tags = list(dict.fromkeys([*(m.get("hashtags") or []), "#gaming", "#highlights", "#clips"]))[:7]
    base_title = _re.sub(r"\s+", " ", m.get("title") or "").strip()
    return m | {
        "title": title if not words or len(words) < 6 else (base_title or title),
        "hooks": [*hooks, *[h for h in m.get("hooks", []) if h not in hooks]][:3],
        "description": m.get("description") or f"{kind} from {project_name}.",
        "hashtags": tags,
    }


def merge_candidates(speech: list[Candidate], action: list[Candidate], duration: float, max_clips: int, min_score: float) -> list[Candidate]:
    """One ranked list from speech and action candidates, without overlaps."""
    pool = sorted([*speech, *action], key=lambda c: c.score.total, reverse=True)
    want = target_count(duration, max_clips)
    out: list[Candidate] = []
    for c in pool:
        if len(out) >= want:
            break
        if c.score.total < min_score:
            break  # strict: no weak clip just to have one (lower the minimum to get more)
        if any(_overlap(c, o) > 0.15 for o in out):
            continue
        out.append(c)
    return out


# --------------------------------------------------------------------------- sections (smart mode)
def _sections_index(ctx: AppContext, project_id: str) -> tuple[Path, list[dict]]:
    p = ctx.paths.project_dir(project_id) / "sections.json"
    items = json.loads(p.read_text()) if p.exists() else []
    items = [s for s in items if Path(s["path"]).exists()]
    return p, items


def find_section(ctx: AppContext, project: dict, start: float, end: float) -> dict | None:
    _, items = _sections_index(ctx, project["id"])
    for s in items:
        if s["start"] <= start - 0.05 and s["end"] >= end + 0.05:
            return s
    return None


def ensure_section(ctx: AppContext, project: dict, start: float, end: float, cancel: CancelToken) -> dict:
    found = find_section(ctx, project, start, end)
    if found:
        return found
    if not project.get("source_url"):
        raise MediaError("Source video is missing (deleted or moved), so this clip can't be rendered.")
    settings = ctx.settings.get()
    total = project.get("duration") or end + SECTION_PAD
    req_s, req_e = max(0.0, start - SECTION_PAD), min(total, end + SECTION_PAD)
    sdir = ctx.paths.project_dir(project["id"]) / "sections"
    sdir.mkdir(exist_ok=True)
    ensure_space(sdir, int((req_e - req_s) * 1.0 * MB), int(settings.min_free_gb * GB), "downloading a clip section")
    dest = sdir / f"s_{int(req_s * 1000)}_{int(req_e * 1000)}.mp4"
    dl.download_section(project["source_url"], dest, req_s, req_e, max_height=settings.max_download_height, cancel=cancel)
    info = ff.probe(dest)
    true_start, conf = req_s, 0.0
    if project.get("audio_path") and Path(project["audio_path"]).exists():
        try:
            true_start, conf = find_offset(dest, Path(project["audio_path"]), req_s)
        except Exception as e:  # alignment is best-effort
            log.warning("Section alignment failed: %s", e)
    if conf < 0.5 or not (req_s - 15.0 <= true_start <= req_s + 1.0):
        log.info("Section %s: alignment not trusted (confidence %.2f, offset %.2f); using requested start", dest.name, conf, true_start - req_s)
        true_start, conf = req_s, 0.0
    sec = {"path": str(dest), "start": round(true_start, 3), "end": round(true_start + info.duration, 3), "confidence": round(conf, 2), "width": info.width, "height": info.height, "fps": info.fps}
    if sec["start"] > start or sec["end"] < end:
        raise MediaError("The downloaded section doesn't cover this clip. Try again, or switch Settings → Downloads to 'Full video'.")
    p, items = _sections_index(ctx, project["id"])
    items.append(sec)
    p.write_text(json.dumps(items, indent=1))
    if not project.get("width"):
        ctx.db.update("projects", project["id"], {"width": info.width, "height": info.height, "fps": info.fps})
    return sec


def _full_download(ctx: AppContext, project: dict, prog: Progress, cancel: CancelToken, stage: str) -> Path:
    settings = ctx.settings.get()
    info = dl.inspect(project["source_url"], settings.max_download_height)
    need = info.get("estimated_bytes") or int((info["duration"] or 600) * 1.2 * MB)
    ensure_space(ctx.paths.downloads, need, int(settings.min_free_gb * GB), "the download")
    src = dl.download(project["source_url"], ctx.paths.downloads, max_height=settings.max_download_height, on_progress=lambda p: prog.update(stage, p, f"{p * 100:.0f}% of the video downloaded"), cancel=cancel)
    vi = ff.probe(src)
    ctx.db.update("projects", project["id"], {"source_path": str(src), "source_owned": 1, "width": vi.width, "height": vi.height, "fps": vi.fps})
    return src


def campaign_length(ctx: AppContext, project: dict, dmin: float, dmax: float, opts: dict) -> tuple[float, float]:
    """Clip length range for finding moments. A campaign's min/max length replaces the project's length preset
    (what the campaign page shows is what's used). Dead-air cuts shorten clips, so a small margin is added
    above the minimum."""
    from ..campaigns.service import campaign_brief

    _, brief = campaign_brief(ctx, project)
    if brief is None or not (brief.min_duration or brief.max_duration):
        return dmin, dmax
    margin = 0.5 if (opts.get("polish") or {}).get("jumpcut", "light") == "off" else 2.0
    lo, hi = brief.min_duration, brief.max_duration
    if lo and hi:
        new_min, new_max = min(lo + margin, hi - 1.0), hi
    elif lo:
        new_min = lo + margin
        new_max = max(dmax, new_min + 15.0)
    else:
        new_max = hi
        new_min = max(5.0, min(dmin, hi - 5.0))
    return float(max(1.0, new_min)), float(new_max)


# --------------------------------------------------------------------------- process job
def run_process(ctx: AppContext, job: dict, cancel: CancelToken) -> None:
    db = ctx.db
    project = db.one("SELECT * FROM projects WHERE id = ?", (job["project_id"],))
    if project is None:
        raise ClipStudioError("Project no longer exists")
    if project["source_type"] == "montage":
        from ..montage.service import run_montage

        return run_montage(ctx, job, cancel)
    settings = ctx.settings.get()
    opts = project_options(settings, with_campaign(ctx, project))
    min_free = int(settings.min_free_gb * GB)
    pdir = ctx.paths.project_dir(project["id"])
    tmp = ctx.paths.temp_dir(project["id"])
    prog = Progress(db, job["id"], PROCESS_STAGES)
    db.update("projects", project["id"], {"status": "processing", "error": None})
    quick = bool(opts.get("quick"))

    try:
        # 1. Source --------------------------------------------------------------------
        src = Path(project["source_path"]) if project["source_path"] else None
        audio_src = Path(project["audio_path"]) if project.get("audio_path") else None
        extra = dict(project.get("extra") or {})
        is_url = project["source_type"] == "url"
        if is_url and (src is None or not src.exists()) and (audio_src is None or not audio_src.exists()):
            prog.start("download", "Contacting source…")
            info = dl.inspect(project["source_url"], settings.max_download_height)
            extra |= {"heatmap": info.get("heatmap") or [], "chapters": info.get("chapters") or [], "creator_subtitles": info.get("creator_subtitles") or [], "title": info["title"], "uploader": info.get("uploader")}
            name = project["name"] if project["name"] != project["source_url"] else info["title"]
            db.update("projects", project["id"], {"extra": extra, "name": name, "duration": info["duration"] or None, "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps")})
            smart = settings.download_strategy == "smart" and not quick
            if smart:
                try:
                    ensure_space(ctx.paths.downloads, int((info["duration"] or 600) * 0.03 * MB) + 20 * MB, min_free, "the audio download")
                    audio_src = dl.download_audio(project["source_url"], ctx.paths.downloads, on_progress=lambda p: prog.update("download", p, f"{p * 100:.0f}% of the audio downloaded"), cancel=cancel)
                    db.update("projects", project["id"], {"audio_path": str(audio_src), "source_owned": 1})
                    prog.done("download", f"Audio only ({audio_src.stat().st_size / MB:,.0f} MB) · video sections come after clips are picked")
                except Cancelled:
                    raise
                except Exception as e:
                    log.warning("Audio-first download failed (%s); downloading the full video", e)
                    smart = False
            if not smart:
                project = db.one("SELECT * FROM projects WHERE id = ?", (project["id"],))
                src = _full_download(ctx, project, prog, cancel, "download")
                prog.done("download", f"{src.stat().st_size / MB:,.0f} MB")
        else:
            prog.skip("download", "Already downloaded" if is_url else "Local file")
        project = db.one("SELECT * FROM projects WHERE id = ?", (project["id"],))
        cancel.check()

        media = src if src is not None and src.exists() else audio_src
        if media is None or not media.exists():
            raise MediaError("Source video is missing. It may have been moved or deleted.")
        if media == src:
            vinfo = ff.probe(src)
            duration, has_audio = vinfo.duration, vinfo.has_audio
            db.update("projects", project["id"], {"duration": vinfo.duration, "width": vinfo.width, "height": vinfo.height, "fps": vinfo.fps})
        else:
            duration, has_audio = (project.get("duration") or audio_duration(media)), True
            if not project.get("duration"):
                db.update("projects", project["id"], {"duration": duration})
        if not has_audio and not quick:
            raise MediaError("This video has no audio track, so there's nothing to transcribe. Use Quick clip mode for silent clips.")
        fp = project["fingerprint"] or fingerprint(media)
        db.update("projects", project["id"], {"fingerprint": fp})
        project = db.one("SELECT * FROM projects WHERE id = ?", (project["id"],))
        dmin, dmax = DURATION_RANGES[opts["duration_preset"]]
        dmin, dmax = campaign_length(ctx, project, dmin, dmax, opts)
        est = estimate_processing(duration, clips=1 if quick else opts["max_clips"], clip_seconds=(dmin + dmax) / 2, output_height=opts["output_height"])
        ensure_space(ctx.paths.work, est.total, min_free, "processing")

        # 2–3. Audio + transcript ---------------------------------------------------------
        transcript: Transcript | None = None
        profile_path = ctx.paths.cache / f"audio_{fp}.json"
        action_audio_path = ctx.paths.cache / f"action_audio_{fp}.json"
        action_video_path = ctx.paths.cache / f"action_video_{fp}.json"
        if has_audio:
            svc = TranscriptionService(ctx.paths.cache, allow_downloads=ctx.env.allow_model_downloads)
            model_id = settings.transcription_model
            cached_tr = svc.cached(fp, model_id)
            wav = tmp / "audio.wav"
            subs_tr = None
            if cached_tr is None and settings.use_creator_subtitles and is_url and extra.get("creator_subtitles"):
                try:
                    vtt = dl.download_subtitles(project["source_url"], tmp, extra["creator_subtitles"][0])
                    if vtt:
                        cues = dl.parse_vtt(vtt.read_text(errors="replace"))
                        if len(cues) >= 5:
                            subs_tr = transcript_from_cues(cues, duration)
                except Exception as e:
                    log.warning("Creator subtitles unavailable (%s); transcribing instead", e)
            if (cached_tr is not None or subs_tr is not None) and profile_path.exists() and action_audio_path.exists():
                prog.skip("audio", "Cached")
            else:
                prog.start("audio")
                ff.extract_audio(media, wav, duration=duration, cancel=cancel, on_progress=lambda p: prog.update("audio", p))
                AudioProfile.save(analyse_wav(wav), profile_path)
                act.save_curves(act.audio_curves(wav), action_audio_path)
                prog.done("audio")
            cancel.check()
            if cached_tr is not None:
                transcript = cached_tr
                prog.skip("transcribe", "Using cached transcript")
            elif subs_tr is not None:
                transcript = subs_tr
                prog.skip("transcribe", f"Used the uploader's subtitles ({len(subs_tr.words):,} words)")
            else:
                prog.start("transcribe", "Loading speech model…")
                chunk = settings.transcription_chunk_seconds if settings.mode.value == "low" else MODE_PRESETS[settings.mode]["transcription_chunk_seconds"]
                t0 = time.time()
                transcript = svc.transcribe(wav, fingerprint=fp, model_id=model_id, duration=duration, chunk_seconds=chunk, on_progress=lambda p: prog.update("transcribe", p, f"{p * 100:.0f}% of audio"), cancel=cancel)
                prog.done("transcribe", f"{len(transcript.words):,} words in {time.time() - t0:.0f}s")
            (pdir / "transcript.json").write_text(json.dumps(transcript.to_dict()))
            (pdir / "transcript.srt").write_text(to_srt(transcript))
            if not settings.keep_intermediates:
                wav.unlink(missing_ok=True)
        else:
            prog.skip("audio", "No audio track")
            prog.skip("transcribe", "No audio track")
            transcript = Transcript(model="none", language="en", duration=duration)
            (pdir / "transcript.json").write_text(json.dumps(transcript.to_dict()))
        cancel.check()

        # 4. Analyse: what kind of video is this? -------------------------------------------
        prog.start("analyse")
        audio = AudioProfile.load(profile_path) if profile_path.exists() else AudioProfile.empty()
        wpm = len(transcript.words) / max(1.0, duration / 60)
        content = opts.get("content_type") or "auto"
        mode = content if content in ("talk", "gameplay") else ("talk" if wpm >= 110 else "gameplay")
        prog.done("analyse", f"{len(transcript.sentences)} sentences · {wpm:.0f} words/min · {'talk' if mode == 'talk' else 'gameplay / action'} mode")
        db.update("projects", project["id"], {"extra": extra | {"mode": mode, "wpm": round(wpm)}})
        if not quick and mode == "talk" and len(transcript.words) < 10:
            raise ClipStudioError("Almost no speech was detected. Set the content type to Gameplay to find action moments, or use Quick clip.")

        # 5. Action analysis (audio always; video for gameplay) --------------------------
        curves = act.load_curves(action_audio_path) or act.ActionCurves()
        if mode == "gameplay" and not quick:
            vcurves = act.load_curves(action_video_path)
            if vcurves is None:
                prog.start("action", "Measuring on-screen motion…")
                vsrc = src if src is not None and src.exists() else None
                if vsrc is None and is_url:
                    try:
                        prog.update("action", 0.02, "Downloading a small preview for motion analysis…")
                        vsrc = dl.download_proxy(project["source_url"], pdir / "proxy.mp4", cancel=cancel)
                    except Cancelled:
                        raise
                    except Exception as e:
                        log.warning("Preview download failed (%s); using audio only", e)
                if vsrc is not None:
                    try:
                        vcurves = act.video_curves(vsrc, duration, on_progress=lambda p: prog.update("action", p, f"{p * 100:.0f}% of the video"), cancel=cancel)
                        act.save_curves(vcurves, action_video_path)
                    except Cancelled:
                        raise
                    except Exception as e:
                        log.warning("Motion analysis failed: %s", e)
                prog.done("action", "Audio + motion" if vcurves else "Audio only (no video available)")
            else:
                prog.skip("action", "Cached")
            if vcurves is not None:
                curves = curves.merged(vcurves)
        else:
            prog.skip("action", "Talk mode: audio peaks only" if not quick else "Quick clip")
        aw = act.ActionWeights(**settings.action_weights.model_dump()).normalised()
        heat_all = extra.get("heatmap") if settings.use_heatmap else None
        excite = act.excitement(curves, transcript, aw, duration=duration) if curves.n() else None
        weights = settings.weights.normalised()
        height = project.get("height") or 1080
        if quick:
            qs = float(opts.get("quick_start") or 0.0)
            qe = float(opts.get("quick_end") or duration)
            qs, qe = max(0.0, min(qs, duration)), max(0.0, min(qe, duration))
            if qe - qs < 1.0:
                qs, qe = 0.0, duration
            prog.start("highlights")
            candidates = [score_range(transcript, audio, qs, qe, source_height=height, weights=weights)]
            prog.done("highlights", "Quick clip: using your range")
            prog.skip("score", "Quick clip")
        else:
            prog.start("highlights")
            heat = heat_all
            chapters = [c["start"] for c in (extra.get("chapters") or [])[1:]]
            if len(transcript.words) >= 10:
                candidates, stats = find_highlights(transcript, audio, source_height=height, weights=weights, min_dur=dmin, max_dur=dmax, max_clips=opts["max_clips"], min_score=opts["min_score"], heat=heat, chapters=chapters)
            else:
                candidates, stats = [], {"windows": 0}
            for c in candidates:  # speech clips that also contain a big moment get a small boost
                b = act.action_bonus(excite, c.start, c.end)
                if b >= 1:
                    c.score.total = round(min(100.0, c.score.total + b), 1)
                    c.score.reasons.append(f"Contains a high-energy moment (+{b:g})")
            if mode == "gameplay":
                acands, astats = act.find_action_highlights(curves, transcript, duration=duration, min_dur=dmin, max_dur=dmax, max_clips=opts["max_clips"], min_score=opts["min_score"], weights=act.ActionWeights(**settings.action_weights.model_dump()), heat=heat)
                candidates = merge_candidates(candidates, acands, duration, opts["max_clips"], opts["min_score"])
                stats = stats | {"windows": stats.get("windows", 0) + astats.get("windows", 0), "action": len([c for c in candidates if c.extras.get("mode") == "action"])}
                if not candidates:
                    from ..analysis.highlights import few_reason

                    # moments of either kind existed but none was strong enough → say that, not "no action peaks"
                    stats["reason"] = few_reason(0, target_count(duration, opts["max_clips"]), opts["min_score"]) if stats["windows"] else (astats.get("reason") or stats.get("reason"))
                elif len(candidates) < target_count(duration, opts["max_clips"]):
                    from ..analysis.highlights import few_reason

                    stats["reason"] = few_reason(len(candidates), target_count(duration, opts["max_clips"]), opts["min_score"])
                else:
                    stats.pop("reason", None)
            prog.done("highlights", f"{stats['windows']:,} windows evaluated" + (f" · {stats['action']} action moments" if stats.get("action") else "") + (" · using YouTube 'Most replayed'" if stats.get("heatmap") else ""))
            prog.start("score")
            if not candidates and not stats.get("windows"):  # no moment of that length at all: a real failure
                raise ClipStudioError(stats.get("reason") or f"No {dmin:.0f}–{dmax:.0f}s moments found. Try a different clip length.")
            # moments existed but none cleared the minimum score: finish with no clips and say why
            prog.done("score", f"{len(candidates)} clip{'' if len(candidates) == 1 else 's'} selected" + (f" — {stats['reason']}" if stats.get("reason") else ""))

        # 7. Metadata (extractive; optional local LLM) -----------------------------------
        prog.start("metadata")
        sentences = normalise_sentences(transcript)
        idf = meta_gen.build_idf(sentences) if sentences else {}
        use_llm = bool(opts["llm_enabled"]) and not quick
        llm_note = ""
        if use_llm:
            ok, why = ollama.available(settings.llm_url, settings.llm_model)
            if not ok:
                use_llm, llm_note = False, f"Local AI unavailable ({why}); used extractive titles."
        # Replace previous clips of this project (re-processing). With keep_clips, approved clips and
        # clips the user made (manual / AI picks) survive, and new candidates that overlap them are skipped.
        # Clips already posted somewhere always survive: their post history (and links) must not be lost.
        keep = bool((job.get("payload") or {}).get("keep_clips"))
        kept: list[dict] = []
        posted = posted_ids(db)
        for old in db.query("SELECT id, rank, start, \"end\", status, analysis, render_path, thumb_path FROM clips WHERE project_id = ?", (project["id"],)):
            # nothing new cleared the minimum score: keep every existing clip rather than leave the video empty
            if not candidates or old["id"] in posted or (keep and (old["status"] == "approved" or (old["analysis"] or {}).get("origin") in ("manual", "ai"))):
                kept.append(old)
                continue
            for p in (old["render_path"], old["thumb_path"]):
                if p:
                    Path(p).unlink(missing_ok=True)
            db.execute("DELETE FROM clips WHERE id = ?", (old["id"],))
        if kept:
            def _covered(c: Candidate) -> bool:
                for k in kept:
                    inter = min(c.end, k["end"]) - max(c.start, k["start"])
                    if inter > 0.5 * min(c.end - c.start, k["end"] - k["start"]):
                        return True
                return False

            candidates = [c for c in candidates if not _covered(c)]
        clip_ids: list[str] = []
        rank0 = max((k["rank"] for k in kept), default=0)  # new clips are numbered after every kept one (no duplicate #)
        for rank, c in enumerate(candidates, rank0 + 1):
            cancel.check()
            if c.i1 > c.i0:
                m = meta_gen.generate(sentences[c.i0 : c.i1], transcript.words, c.score.category, idf)
            else:
                m = {"title": project["name"][:80], "hooks": [], "description": "", "hashtags": []}
            if c.extras.get("mode") == "action":
                m = action_metadata(m, c, project["name"], rank)
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
            prog.update("metadata", (rank - rank0) / max(1, len(candidates)))
            clip_ids.append(insert_clip(ctx, project, c, rank, m, opts, weights, total=total, reasons=reasons, origin="quick" if quick else "auto", llm=bool(ai), category=(ai or {}).get("category")))
        prog.done("metadata", llm_note or ("Local AI + extractive" if use_llm else "Extractive (from the speaker's own words)"))

        # 8. Captions: word tracks are burned in during render ------------------------
        prog.start("captions")
        prog.done("captions", f"Word-level captions for {len(clip_ids)} clips")

        # 9. Sections (smart mode only) ------------------------------------------------------
        project = db.one("SELECT * FROM projects WHERE id = ?", (project["id"],))
        if not (project["source_path"] and Path(project["source_path"]).exists()) and is_url:
            prog.start("sections")
            try:
                for k, cid in enumerate(clip_ids):
                    cancel.check()
                    clip = db.one("SELECT * FROM clips WHERE id = ?", (cid,))
                    ensure_section(ctx, project, clip["start"], clip["end"], cancel)
                    project = db.one("SELECT * FROM projects WHERE id = ?", (project["id"],))
                    prog.update("sections", (k + 1) / len(clip_ids), f"Clip {k + 1} of {len(clip_ids)}")
                prog.done("sections", f"{len(clip_ids)} short sections instead of the full video")
            except Cancelled:
                raise
            except Exception as e:
                log.warning("Section downloads failed (%s); falling back to the full video", e)
                prog.update("sections", 0.0, "Sections unavailable for this site; downloading the full video…")
                _full_download(ctx, project, prog, cancel, "sections")
                prog.done("sections", "Downloaded the full video (sections weren't available)")
        else:
            prog.skip("sections", "Full video available")

        # 10. Render ----------------------------------------------------------------------
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
        t = load_transcript(ctx, project["id"])
        if t is None:
            return []
        src = [{"w": w.w, "s": w.s, "e": w.e} for w in t.words]
    return [CapWord(w["w"], max(0.0, w["s"] - start), min(end, w["e"]) - start) for w in src if w["e"] > start + 0.05 and w["s"] < end - 0.05]


def media_for_clip(ctx: AppContext, project: dict, start: float, end: float, *, cancel: CancelToken | None = None) -> tuple[Path, float, int, int, float]:
    """(video file, its offset in the source timeline, width, height, fps) covering [start, end].
    With ``cancel`` set, a missing section is downloaded; otherwise it raises."""
    src = Path(project["source_path"] or "")
    if project["source_path"] and src.exists():
        return src, 0.0, project["width"], project["height"], project["fps"] or 30.0
    sec = find_section(ctx, project, start, end)
    if sec is None:
        if cancel is None or not project.get("source_url"):
            if project.get("source_url"):
                raise MediaError("The video for this part isn't downloaded yet. Render the clip to fetch it.")
            raise MediaError("Source video is missing (deleted or moved), so this clip can't be rendered.")
        sec = ensure_section(ctx, project, start, end, cancel)
    return Path(sec["path"]), sec["start"], sec["width"], sec["height"], sec["fps"] or 30.0


def stream_config(project: dict) -> dict | None:
    st = (project.get("options") or {}).get("stream_layout")
    return dict(st) if st else None


def render_spec(ctx: AppContext, project: dict, clip: dict, *, cancel: CancelToken | None = None, for_preview: bool = False) -> RenderSpec:
    settings = ctx.settings.get()
    edit = clip["edit"]
    src, offset, sw, sh, sfps = media_for_clip(ctx, project, clip["start"], clip["end"], cancel=cancel)
    mode = MODE_PRESETS[settings.mode]
    x264 = mode.get("x264_preset")
    camp, brief = campaign_brief(ctx, project)
    feat = features(ctx, project, clip, brief)
    words = clip_words(ctx, project, clip)
    duration = clip["end"] - clip["start"]
    analysis = clip.get("analysis") or {}
    lib = ctx.paths.library

    height = int(edit.get("output_height", settings.output_height))
    aspect = edit.get("aspect_ratio", settings.aspect_ratio)
    if brief and brief.logo.mode == "template" and feat["logo"]:
        height, aspect = 1080, "9:16"  # templates are 1080x1920 and must not be resized relative to the frame

    # Music
    music: Path | None = None
    music_offset = 0.0
    mchoice = feat["music"] or "none"
    if mchoice == "upload" and edit.get("music_path") and Path(edit["music_path"]).exists():
        music = Path(edit["music_path"])
    elif mchoice == "auto" or mchoice.startswith("mood:"):
        mood = mchoice.split(":", 1)[1] if mchoice.startswith("mood:") else mood_for(clip.get("category") or "", analysis.get("scores") or {})
        picked = pick_track(lib, mood, clip["id"], duration)
        if picked:
            music, music_offset = picked
    elif mchoice.startswith("lib:"):
        mood, _, name = mchoice[4:].partition("/")
        music = track_path(lib, mood, name)

    # Sound effects
    cues = []
    if feat["sfx"] == "auto":
        starts = [s["start"] - clip["start"] for s in analysis.get("sentences", [])]
        cues = plan_cues(words, duration, hook_overlay=feat["hook_overlay"]["enabled"], sentence_starts=starts)
    elif feat["sfx"] == "custom":
        cues = cues_from_dicts(feat["sfx_cues"], duration)
    sfx_files = {k: resolve_file(k, cache=ctx.paths.cache, library=lib, seed=clip["id"]) for k in SFX_TYPES} if feat["sfx"] != "off" else {}

    # Campaign overlays / sound
    pol = feat["polish"]
    tl = feat["text_layout"]  # positions that keep text clear of the logo
    hook = HookText(feat["hook_overlay"]["text"], feat["hook_overlay"]["duration"], y=tl["hook_y"], style=pol.get("hook_style", "box")) if feat["hook_overlay"]["enabled"] and feat["hook_overlay"]["text"] else None
    et = feat["end_text"]
    end_text = EndText(et["text"], et["duration"], y=tl.get("end_y", 0.45), style=et["style"], look=et["look"]) if et["enabled"] and et["text"] else None
    disclosure = DisclosureText(brief.disclosure.text, brief.disclosure.corner) if brief and feat["disclosure"] else None
    logo = brief.logo.model_dump() if brief and feat["logo"] and brief.logo.file and Path(brief.logo.file).exists() else None
    sound = None
    sound_offset = 0.0
    if brief and feat["sound_id"]:
        s = next((x for x in brief.sounds if x.id == feat["sound_id"]), None)
        if s and s.file and Path(s.file).exists():
            sound, sound_offset = Path(s.file), s.start
    stream = stream_config(project)
    if for_preview and not (stream or {}).get("cam") and (analysis.get("stream") or {}).get("cam"):
        stream = analysis["stream"]  # preview what the last render detected
    bt = analysis.get("burned_text")
    band = TextBand(bt["y0"], bt["y1"], bt.get("presence", 1.0), [tuple(x) for x in bt.get("intervals", [])]) if bt and feat["blur_burned"] == "auto" else None

    return RenderSpec(
        src=src,
        start=clip["start"] - offset,
        end=clip["end"] - offset,
        src_w=sw,
        src_h=sh,
        src_fps=sfps,
        has_audio=bool(project.get("audio_path") or project.get("source_path")) and ff_has_audio(src),
        aspect=aspect,
        height=height,
        layout=edit.get("layout", settings.layout),
        stream=stream,
        follow_action=settings.follow_action,
        auto_fallback=settings.auto_fallback,
        auto_stream=settings.auto_stream,
        words=words,
        style=caption_style_for(ctx, clip, brief)[0].model_copy(update={"position": tl["caption_position"]}),
        captions=feat["captions"],
        normalise_audio=settings.normalise_audio,
        max_fps=60.0 if settings.mode.value == "high" else 30.0,
        encoder="x264" if x264 else "videotoolbox",
        x264_preset=x264 or "veryfast",
        voice_enhance=feat["voice_enhance"],
        music=music,
        music_volume=feat["music_volume"],
        music_offset=music_offset,
        sound=sound,
        sound_offset=sound_offset,
        sound_volume=feat["sound_volume"],
        original_volume=feat["original_volume"],
        sfx=cues,
        sfx_files=sfx_files,
        sfx_gain=SFX_GAIN,
        sfx_on_cuts=feat["sfx"] == "auto",
        hook=hook,
        end_text=end_text,
        disclosure=disclosure,
        logo=logo,
        blur_burned=feat["blur_burned"],
        burned_band=band,
        jumpcut=pol.get("jumpcut", "off"),
        fillers=bool(pol.get("fillers", True)),
        zoom=pol.get("zoom", "off"),
        emphasis=bool(pol.get("emphasis", False)),
        emoji=bool(pol.get("emoji", False)),
        progress=pol.get("progress", "off"),
        profanity=pol.get("profanity", "off"),
        denoise=pol.get("denoise", "off"),
    )


_AUDIO_CACHE: dict[str, bool] = {}


def ff_has_audio(path: Path) -> bool:
    key = f"{path}:{path.stat().st_mtime if path.exists() else 0}"
    if key not in _AUDIO_CACHE:
        try:
            _AUDIO_CACHE[key] = ff.probe(path).has_audio
        except Exception:
            _AUDIO_CACHE[key] = False
    return _AUDIO_CACHE[key]


def render_one(ctx: AppContext, project: dict, clip: dict, cancel: CancelToken, on_progress=None) -> None:
    db = ctx.db
    settings = ctx.settings.get()
    out_dir = ctx.paths.project_dir(project["id"]) / "clips"
    out = out_dir / f"{clip['id']}.mp4"
    db.update("clips", clip["id"], {"render_status": "rendering", "render_error": None})
    if project["source_type"] == "montage":
        from ..montage.service import render_variant

        try:
            r = render_variant(ctx, project, clip, cancel, on_progress)
            silent = ((clip.get("edit") or {}).get("montage") or {}).get("audio", "song") == "silent"
            analysis = clip["analysis"] | r["analysis"] | {"qa": quality_check(settings, Path(r["render_path"]), not silent, song=True)}
            db.update("clips", clip["id"], {"render_status": "done", "render_path": r["render_path"], "thumb_path": r["thumb_path"], "analysis": analysis})
        except Exception as e:
            db.update("clips", clip["id"], {"render_status": "failed" if not cancel.cancelled else "pending", "render_error": str(e)[:500]})
            raise
        return
    try:
        spec = render_spec(ctx, project, clip, cancel=cancel)
        ensure_space(out_dir, int(spec.duration * 1.6 * MB), int(settings.min_free_gb * GB), "rendering")
        face = MODE_PRESETS[settings.mode].get("face_tracking", True)
        feat = features(ctx, project, clip, campaign_brief(ctx, project)[1])
        cold = None
        if feat["polish"].get("cold_open"):
            cold = render_cold_open(spec, out, clip, on_progress=on_progress, cancel=cancel, face_track=face)
        res = cold or render_clip(spec, out, on_progress=on_progress, cancel=cancel, face_track=face)
        thumb = make_thumbnail(out, out_dir / f"{clip['id']}.jpg", t=min(1.0, res.out_duration / 2 if res.out_duration else spec.duration / 2))
        analysis = clip["analysis"] | {
            "layout_used": res.layout_used,
            "face_ratio": round(res.face_ratio, 2),
            "burned_text": res.burned_text,
            "sfx_used": res.sfx,
            "music_used": spec.music.name if spec.music else None,
            "output": f"{spec.out_size()[0]}x{spec.out_size()[1]}",
            "stream": res.stream,
            "polish_used": res.polish,
            "output_duration": res.out_duration or round(spec.duration, 3),
            # silence on purpose (original muted for an in-app sound, no music) isn't a problem
            # music / campaign sound are only mixed into a source that has an audio track
            "qa": quality_check(settings, out, spec.has_audio and (spec.original_volume > 0.01 or bool(spec.music or spec.sound))),
        }
        db.update("clips", clip["id"], {"render_status": "done", "render_path": str(out), "thumb_path": str(thumb), "analysis": analysis})
    except Exception as e:
        db.update("clips", clip["id"], {"render_status": "failed" if not cancel.cancelled else "pending", "render_error": str(e)[:500]})
        raise


def quality_check(settings: UserSettings, path: Path, has_audio: bool, song: bool = False) -> dict | None:
    """Quality check of the finished file (sound level, frozen / black picture, flashing). Never fails a render.
    ``song``: the audio is a mastered song (music edits), loud by design: only silence is reported."""
    if not settings.qa_check:
        return None
    try:
        from ..media.qa import check

        return check(path, has_audio=has_audio, song=song)
    except Exception as e:  # a check that can't run is reported as "not checked", not as a broken clip
        log.warning("Quality check failed for %s: %s", path.name, e)
        return None


def render_cold_open(spec: RenderSpec, out: Path, clip: dict, *, on_progress=None, cancel: CancelToken, face_track: bool = True) -> RenderResult | None:
    """Teaser (the strongest 2–3 s) + the whole clip, joined. None when the clip has no clear peak."""
    env = None
    if spec.has_audio:
        try:
            env = audio_envelope(spec.src, spec.start, spec.duration)
        except Exception as e:
            log.warning("Cold open: audio envelope failed: %s", e)
    peak = (clip.get("analysis") or {}).get("peak_t")
    win = peak_window(spec.words, spec.duration, env, peak_t=(peak - clip["start"]) if peak is not None else None)
    if win is None:
        return None
    a, b = win
    tl = plan_timeline(spec, env)
    total = (b - a) + tl.out_duration
    split = (b - a) / total
    teaser = replace(
        spec,
        start=spec.start + a,
        end=spec.start + b,
        words=[CapWord(w.w, round(w.s - a, 3), round(w.e - a, 3)) for w in spec.words if w.s >= a - 0.05 and w.e <= b + 0.05],
        jumpcut="off",
        segments=None,
        sfx=[],
        sfx_on_cuts=False,
        progress_span=(0.0, split),
        music_fade_out=False,
        hook=replace(spec.hook, duration=min(spec.hook.duration, b - a)) if spec.hook else None,
        end_text=None,  # the end text belongs to the end of the whole clip (the main part), not the teaser
    )
    whoosh = [Cue("whoosh", 0.0)] if "whoosh" in spec.sfx_files else []
    main = replace(
        spec,
        segments=tl.segments if tl.cut else None,
        progress_span=(split, 1.0),
        flash_in=True,
        hook=None,
        music_fade_in=False,
        music_offset=spec.music_offset + (b - a),
        sfx=whoosh + [c for c in spec.sfx if c.t > 0.5],
    )
    parts = [out.with_name(out.stem + ".cold0.mp4"), out.with_name(out.stem + ".cold1.mp4")]
    try:
        t = render_clip(teaser, parts[0], on_progress=(lambda p: on_progress(0.25 * p)) if on_progress else None, cancel=cancel, face_track=face_track)
        m = render_clip(main, parts[1], on_progress=(lambda p: on_progress(0.25 + 0.75 * p)) if on_progress else None, cancel=cancel, face_track=face_track)
        concat_parts(parts, out)
    finally:
        for x in parts:
            x.unlink(missing_ok=True)
    m.path = out
    m.out_duration = round(t.out_duration + m.out_duration, 3)
    m.polish = m.polish | {"cold_open": [a, b]}
    shifted = [x | {"t": round(x["t"] + t.out_duration, 2)} for x in m.sfx]
    m.sfx = t.sfx + shifted
    return m


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
