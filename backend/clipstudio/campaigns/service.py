"""Glue between campaigns and clips: effective per-clip settings, compliance facts, export package."""

from __future__ import annotations

import json
import os
import re
import shutil
import time
import zipfile
from pathlib import Path

from ..context import AppContext
from ..db import new_id
from ..errors import NotFound
from ..media import ffmpeg as ff
from .compliance import ClipFacts, check_clip, summarise
from .models import Brief
from .post import build_post
from .safezones import GRID_H, GRID_W


def load_campaign(ctx: AppContext, campaign_id: str | None) -> dict | None:
    if not campaign_id:
        return None
    return ctx.db.one("SELECT * FROM campaigns WHERE id = ?", (campaign_id,))


def campaign_brief(ctx: AppContext, project: dict) -> tuple[dict | None, Brief | None]:
    camp = load_campaign(ctx, project.get("campaign_id"))
    if camp is None:
        return None, None
    return camp, Brief.model_validate(camp["brief"])


def features(ctx: AppContext, project: dict, clip: dict, brief: Brief | None) -> dict:
    """Effective feature settings for a clip: clip edit → project options → app settings, then
    campaign rules on top (a campaign that forbids music/SFX always wins)."""
    s = ctx.settings.get()
    opts = project.get("options") or {}
    e = clip.get("edit") or {}

    def pick(key: str, default):
        if key in e and e[key] is not None:
            return e[key]
        if key in opts and opts[key] is not None:
            return opts[key]
        return default

    music = pick("music", s.music_default)
    if e.get("music_path") and "music" not in e:
        music = "upload"
    sfx = pick("sfx", "auto" if s.sfx_default else "off")
    if isinstance(sfx, bool):
        sfx = "auto" if sfx else "off"
    hook_cfg = e.get("hook_overlay") or {}
    hook_on_default = brief.hook_overlay.enabled if brief else s.hook_overlay_default
    hook_on = hook_cfg.get("enabled", opts.get("hook_overlay", hook_on_default))
    camp_text = (brief.hook_overlay.text or "").strip() if brief else ""
    if camp_text:  # the campaign's own title text, unless this clip was given its own in the editor
        camp_text = camp_text.replace("{title}", clip.get("title") or "").strip()
    hook_text = hook_cfg.get("text") or camp_text or clip.get("title") or clip.get("hook") or ""
    hook_dur = brief.hook_overlay.duration if brief else 3.5
    out = {
        "music": music,
        "music_volume": float(e.get("music_volume", 0.25)),
        "sfx": sfx,
        "sfx_cues": e.get("sfx_cues") or [],
        "blur_burned": pick("blur_burned", s.blur_burned_default),
        "voice_enhance": s.voice_enhance,
        "hook_overlay": {"enabled": bool(hook_on), "text": hook_text, "duration": float(hook_cfg.get("duration", hook_dur))},
        "captions": bool(e.get("captions", True)) and (brief is None or brief.captions),
        "logo": False,
        "disclosure": False,
        "sound_id": None,
        "sound_volume": 1.0,
        "original_volume": 1.0,
        "locked": [],
        # retention polish: app settings → project options → clip edit (each may be partial)
        "polish": s.polish.model_dump() | {k: v for k, v in (opts.get("polish") or {}).items() if v is not None} | {k: v for k, v in (e.get("polish") or {}).items() if v is not None},
    }
    if hook_cfg.get("style"):
        out["polish"]["hook_style"] = hook_cfg["style"]
    if brief:
        if brief.logo.mode != "none" and brief.logo.file:
            out["logo"] = bool(e.get("logo", True))
        if brief.disclosure.text and brief.disclosure.on_screen:
            out["disclosure"] = bool(e.get("disclosure", True))
        if brief.sound_rule.mode == "burn":
            sounds = [x for x in brief.sounds if x.file]
            sid = e.get("sound_id") or (sounds[0].id if sounds else None)
            if sid and any(x.id == sid for x in sounds):
                out["sound_id"] = sid
            out["sound_volume"] = float(e.get("sound_volume", 1.0))
            out["original_volume"] = float(e.get("original_volume", brief.sound_rule.original_volume))
        if not brief.allow_music:
            out["music"] = "none"
            out["locked"].append("music")
        if not brief.allow_sfx:
            out["sfx"] = "off"
            out["locked"].append("sfx")
    # end text: clip edit → campaign ({title} = the clip's title)
    end_cfg = e.get("end_text") or {}
    camp_end = brief.end_text if brief else None
    end_text = (end_cfg.get("text") or ((camp_end.text or "").replace("{title}", clip.get("title") or "").strip() if camp_end else "")).strip()
    out["end_text"] = {
        "enabled": bool(end_cfg.get("enabled", camp_end.enabled if camp_end else False)) and bool(end_text),
        "text": end_text,
        "duration": float(end_cfg.get("duration") or (camp_end.duration if camp_end else 2.5)),
        "style": end_cfg.get("style") or (camp_end.style if camp_end else "dark"),
        "look": end_cfg.get("look") or (camp_end.look if camp_end else "overlay"),
        "required": bool(camp_end and camp_end.enabled and camp_end.text.strip()),
    }
    if project.get("source_type") == "montage":
        # a montage's audio is the song; no extra music, effects or hook card
        out |= {"music": "none", "sfx": "off", "sound_id": None, "hook_overlay": out["hook_overlay"] | {"enabled": False}}
        out["polish"] |= {"jumpcut": "off", "zoom": "off", "cold_open": False, "emoji": False}
        out["locked"] = sorted(set(out["locked"]) | {"music", "sfx", "hook", "end"})
        out["end_text"] |= {"enabled": False, "required": False}
    out["text_layout"] = text_layout(ctx, clip, brief, out)
    return out


def caption_style_for(ctx: AppContext, clip: dict, brief: Brief | None):
    """The clip's caption look with the campaign's caption position applied (unless the clip set its own).
    Returns (style, set_by_clip)."""
    from ..media.captions import resolve_style

    e = clip.get("edit") or {}
    own = e.get("caption_style") or {}
    style = resolve_style(e.get("caption_preset", ctx.settings.get().caption_preset), own)
    by_clip = own.get("position") is not None
    if not by_clip and brief and brief.caption_position is not None:
        style = style.model_copy(update={"position": brief.caption_position})
    return style, by_clip


def text_layout(ctx: AppContext, clip: dict, brief: Brief | None, feat: dict) -> dict:
    """Where captions, the title text and the end text go so the logo doesn't cover them."""
    from .text_layout import layout

    style, by_clip = caption_style_for(ctx, clip, brief)
    logo = brief.logo.model_dump() if (brief and feat["logo"] and brief.logo.file) else None
    hook = feat["hook_overlay"]["text"] if feat["hook_overlay"]["enabled"] else None
    end = feat["end_text"]["text"] if feat["end_text"]["enabled"] else None
    try:
        out = layout(logo=logo, caption_style=style, caption_set_by_clip=by_clip, captions_on=feat["captions"], hook_text=hook, end_text=end)
    except Exception:  # never block a render or the clip list on this
        out = {"caption_position": style.position, "caption_moved": False, "hook_y": 0.2, "hook_moved": False, "end_y": 0.45, "end_moved": False, "logo_band": None, "caption_logo_overlap": 0.0}
    out["caption_source"] = "clip" if by_clip else ("campaign" if brief and brief.caption_position is not None else "look")
    return out


def post_texts(brief: Brief | None, clip: dict) -> dict[str, dict]:
    platforms = list(brief.platforms) if brief and brief.platforms else ["reels", "shorts"]
    overrides = (clip.get("edit") or {}).get("post") or {}
    meta = clip.get("meta") or {}
    c = {"title": clip.get("title", ""), "hook": clip.get("hook", ""), "description": clip.get("description", ""), "hashtags": meta.get("hashtags", [])}
    out = {}
    for p in platforms:
        built = build_post(brief.model_dump() if brief else None, c, p)
        if overrides.get(p):
            built = built | {"caption": overrides[p], "edited": True}
        out[p] = built
    return out


def source_key(project: dict) -> str:
    return project.get("source_url") or project.get("fingerprint") or project["id"]


def _duplicates(ctx: AppContext, project: dict, clip: dict, campaign_id: str | None) -> tuple[list[str], int]:
    rows = ctx.db.query("SELECT e.*, c.name AS campaign_name FROM exports e LEFT JOIN campaigns c ON c.id = e.campaign_id WHERE e.source_key = ? AND e.clip_id != ?", (source_key(project), clip["id"]))
    other: list[str] = []
    same = 0
    for r in rows:
        inter = max(0.0, min(r["end"], clip["end"]) - max(r["start"], clip["start"]))
        if inter / max(1e-6, min(r["end"] - r["start"], clip["end"] - clip["start"])) < 0.5:
            continue
        if (r["campaign_id"] or None) == (campaign_id or None):
            same += 1
        else:
            other.append(r["campaign_name"] or "a clip with no campaign")
    # the same clip exported for a different campaign also counts
    for r in ctx.db.query("SELECT e.campaign_id, c.name AS campaign_name FROM exports e LEFT JOIN campaigns c ON c.id = e.campaign_id WHERE e.clip_id = ?", (clip["id"],)):
        if (r["campaign_id"] or None) != (campaign_id or None):
            other.append(r["campaign_name"] or "a clip with no campaign")
    return list(dict.fromkeys(other)), same


def compliance(ctx: AppContext, project: dict, clip: dict) -> dict:
    from ..jobs.pipeline import clip_words
    from ..media.logo import content_mask

    camp, brief = campaign_brief(ctx, project)
    feat = features(ctx, project, clip, brief)
    path = Path(clip["render_path"]) if clip.get("render_path") else None
    rendered = bool(path and path.exists())
    w = h = None
    has_audio = True
    if rendered:
        try:
            info = ff.probe(path)
            w, h, has_audio = info.width, info.height, info.has_audio
        except Exception:
            rendered = False
    words = clip_words(ctx, project, clip)
    logo_mask = None
    if brief and feat["logo"] and brief.logo.file and Path(brief.logo.file).exists():
        try:
            logo_mask = content_mask(brief.logo.model_dump(), GRID_W, GRID_H)
        except Exception:
            logo_mask = None
    posts = post_texts(brief, clip)
    dup_other, dup_same = _duplicates(ctx, project, clip, camp["id"] if camp else None)
    analysis = clip.get("analysis") or {}
    facts = ClipFacts(
        # jump cuts / a cold open change the real length: use the rendered length when it's current
        duration=analysis["output_duration"] if (rendered and clip.get("render_status") == "done" and analysis.get("output_duration")) else clip["end"] - clip["start"],
        rendered=rendered,
        stale=clip.get("render_status") == "stale",
        width=w,
        height=h,
        has_audio=has_audio,
        caption_text=" ".join(x.w for x in words) if feat["captions"] else "",
        hook_text=feat["hook_overlay"]["text"] if feat["hook_overlay"]["enabled"] else "",
        end_text=feat["end_text"]["text"] if feat["end_text"]["enabled"] else "",
        end_required=feat["end_text"]["required"],
        caption_logo_overlap=float(feat["text_layout"].get("caption_logo_overlap") or 0.0),
        caption_set_by_clip=feat["text_layout"].get("caption_source") == "clip",
        qa=analysis.get("qa") if (rendered and clip.get("render_status") == "done") else None,
        disclosure_on_screen=brief.disclosure.text if (brief and feat["disclosure"]) else "",
        transcript=analysis.get("transcript", ""),
        title=clip.get("title", ""),
        posts={p: v["caption"] for p, v in posts.items()},
        logo_enabled=feat["logo"],
        logo_mask=logo_mask,
        music=bool(analysis.get("music_used")) if rendered else feat["music"] not in ("none", None, ""),
        sfx=bool(analysis.get("sfx_used")) if rendered else feat["sfx"] != "off",
        sound_burned=bool(feat["sound_id"]),
        sound_volume=feat["sound_volume"],
        burned_text_detected=bool(analysis.get("burned_text")),
        burned_text_blurred=feat["blur_burned"] == "auto",
        duplicates_other=dup_other,
        duplicates_same=dup_same,
    )
    montage = project.get("source_type") == "montage"
    if montage:
        m = (clip.get("edit") or {}).get("montage") or {}
        plan_ = (clip.get("edit") or {}).get("montage_plan") or {}
        song = ((project.get("options") or {}).get("montage") or {}).get("song") or {}
        facts.caption_text = m.get("pov", "")
        facts.music = False
        facts.sfx = False
        facts.sound_burned = m.get("audio", "song") == "song" and song.get("kind") == "campaign"
        facts.sound_volume = 1.0
    checks = check_clip(brief, facts)
    if montage and plan_:
        from .compliance import Check

        start = float(plan_.get("song_start", 0))
        checks.append(Check("song_section", "Song section", "info", f"Uses the song from {int(start // 60)}:{start % 60:04.1f} for {plan_.get('length', 0):.1f}s" + (f"; drop at {plan_['drop']:.1f}s" if plan_.get("drop") is not None else "")))
        if brief and brief.sound_rule.mode == "in_app":
            checks.append(Check("song_align", "Line up the in-app sound", "manual", f"When adding the official sound, start it at {int(start // 60)}:{start % 60:04.1f} so the cuts land on the beat, and mute the original audio."))
    result = summarise(checks)
    result["campaign"] = {"id": camp["id"], "name": camp["name"]} if camp else None
    result["posts"] = posts
    result["checked_at"] = time.time()
    return result


# ----------------------------------------------------------------------------- export (A9)
def _slug(s: str, n: int = 50) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", s or "").strip("-")[:n].lower() or "clip"


def export_root(ctx: AppContext) -> Path:
    p = ctx.paths.work / "exports"
    p.mkdir(parents=True, exist_ok=True)
    return p


def export_clip(ctx: AppContext, project: dict, clip: dict) -> dict:
    if not clip.get("render_path") or not Path(clip["render_path"]).exists():
        raise NotFound("Render the clip before exporting")
    camp, brief = campaign_brief(ctx, project)
    report = compliance(ctx, project, clip)
    folder = export_root(ctx) / (_slug(camp["name"]) if camp else "no-campaign") / f"{clip['rank']:02d}-{_slug(clip.get('title') or '')}-{clip['id']}"
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)
    video = folder / "clip.mp4"
    try:
        os.link(clip["render_path"], video)  # hard link: no extra disk space
    except OSError:
        shutil.copy2(clip["render_path"], video)
    if clip.get("thumb_path") and Path(clip["thumb_path"]).exists():
        shutil.copy2(clip["thumb_path"], folder / "thumbnail.jpg")
    for p, post in report["posts"].items():
        name = "Instagram Reels" if p == "reels" else "YouTube Shorts"
        body = (f"Title: {post['title']}\n\n" if p == "shorts" else "") + post["caption"] + "\n"
        (folder / f"caption_{p}.txt").write_text(body, encoding="utf-8")
        if post["warnings"]:
            (folder / f"caption_{p}.txt").write_text(body + "\n# " + name + " notes:\n" + "\n".join(f"# - {w}" for w in post["warnings"]) + "\n", encoding="utf-8")
    feat = features(ctx, project, clip, brief)
    analysis = clip.get("analysis") or {}
    manifest = {
        "clip_id": clip["id"],
        "title": clip.get("title"),
        "hook": clip.get("hook"),
        "hooks": (clip.get("meta") or {}).get("hooks", []),
        "source": {"project": project["name"], "url": project.get("source_url"), "start": clip["start"], "end": clip["end"], "duration": round(clip["end"] - clip["start"], 2)},
        "score": clip.get("score"),
        "scores": analysis.get("scores"),
        "reasons": analysis.get("reasons"),
        "weaknesses": analysis.get("weaknesses"),
        "render": {
            "layout": analysis.get("layout_used"),
            "music": analysis.get("music_used"),
            "sfx": analysis.get("sfx_used"),
            "burned_text_blurred": bool(analysis.get("burned_text")) and feat["blur_burned"] == "auto",
            "hook_overlay": feat["hook_overlay"],
            "logo": feat["logo"],
            "campaign_sound": feat["sound_id"],
        },
        "campaign": {"id": camp["id"], "name": camp["name"], "source": brief.source, "type": brief.type} if camp and brief else None,
        "compliance": {k: report[k] for k in ("verdict", "fails", "warnings", "manual", "checks")},
        "exported_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }
    (folder / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    (folder / "checklist.md").write_text(checklist_md(clip, camp, brief, report), encoding="utf-8")
    db = ctx.db
    db.execute("DELETE FROM exports WHERE clip_id = ? AND ((campaign_id IS NULL AND ? IS NULL) OR campaign_id = ?)", (clip["id"], camp["id"] if camp else None, camp["id"] if camp else None))
    db.insert("exports", {"id": new_id(), "clip_id": clip["id"], "campaign_id": camp["id"] if camp else None, "fingerprint": project.get("fingerprint"), "source_key": source_key(project), "start": clip["start"], "end": clip["end"], "path": str(folder), "created_at": time.time()})
    meta = dict(clip.get("meta") or {})
    meta["exported"] = {"path": str(folder), "at": time.time(), "verdict": report["verdict"]}
    db.update("clips", clip["id"], {"meta": meta})
    return {"path": str(folder), "verdict": report["verdict"], "files": sorted(p.name for p in folder.iterdir())}


def checklist_md(clip: dict, camp: dict | None, brief: Brief | None, report: dict) -> str:
    icon = {"pass": "✅", "warn": "⚠️", "fail": "❌", "manual": "☐", "info": "ℹ️"}
    lines = [f"# {clip.get('title') or 'Clip'}", ""]
    if camp and brief:
        lines += [f"Campaign: **{camp['name']}**" + (f" ({brief.source})" if brief.source else ""), ""]
        if brief.url:
            lines += [f"Brief: {brief.url}", ""]
        money = []
        if brief.cpm:
            money.append(f"${brief.cpm:g} per 1,000 views")
        if brief.min_views:
            money.append(f"minimum {brief.min_views:,} views")
        if brief.min_engagement:
            money.append(f"≥ {brief.min_engagement:g}% engagement")
        if brief.keep_live_days:
            money.append(f"keep live {brief.keep_live_days} days")
        if money:
            lines += ["Targets: " + " · ".join(money), ""]
    lines += [f"## Compliance: {report['verdict'].upper()}", ""]
    for c in report["checks"]:
        lines.append(f"- {icon.get(c['status'], '-')} **{c['label']}**" + (f": {c['detail']}" if c["detail"] else ""))
    lines += ["", "## Before you post", ""]
    steps = ["Upload clip.mp4 as a Reel / Short (don't let the app crop it)", "Paste the caption from caption_reels.txt / caption_shorts.txt"]
    if brief and brief.sound_rule.mode == "in_app":
        steps.append(f"Add the official campaign sound in the app at ≥ {brief.sound_rule.min_volume * 100:.0f}% volume")
    if brief and brief.rules:
        lines += ["Campaign rules:", *[f"- {r}" for r in brief.rules], ""]
    lines += [f"- ☐ {s}" for s in steps]
    return "\n".join(lines) + "\n"


def zip_export(folder: Path, dest: Path) -> Path:
    tmp = dest.with_suffix(".part.zip")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_STORED) as z:  # video is already compressed
        for p in sorted(folder.iterdir()):
            if p.is_file():
                z.write(p, arcname=f"{folder.name}/{p.name}")
    tmp.replace(dest)
    return dest
