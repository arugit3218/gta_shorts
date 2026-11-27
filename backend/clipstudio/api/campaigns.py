"""Campaign endpoints: briefs, presets, paste-to-fill, logo & sound files, safe zones, bulk export."""

from __future__ import annotations

import shutil
import time
from pathlib import Path

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field, ValidationError

from ..campaigns import posting, service
from ..campaigns.models import Brief, SoundSpec, merge_brief
from ..campaigns.parser import parse_brief
from ..campaigns.post import build_post
from ..campaigns.presets import apply_site_rules, base_brief, list_presets, preset_brief
from ..campaigns.summary import summarise_brief
from ..campaigns.safezones import public_zones
from ..context import AppContext
from ..db import new_id
from ..errors import InvalidInput, NotFound
from ..media.library import AUDIO_EXTS, audio_duration
from ..media.logo import LOGO_EXTS, inspect_logo
from ..storage import GB, MB, ensure_space
from ..jobs.worker import Worker
from .deps import ctx, worker
from .sources import safe_filename, save_raw_upload

router = APIRouter(prefix="/api", tags=["campaigns"])


class CreateCampaign(BaseModel):
    type: str | None = Field(None, pattern="^(clipping|logo|music|ugc)$")  # type-first creation (R1); preset is the old way
    preset: str = "generic"
    name: str | None = Field(None, max_length=200)
    brief: dict | None = None  # partial Brief applied on top of the preset
    text: str | None = Field(None, max_length=20000)  # pasted brief to parse


class PatchCampaign(BaseModel):
    name: str | None = Field(None, max_length=200)
    status: str | None = Field(None, pattern="^(active|archived)$")
    brief: dict | None = None


class ParseText(BaseModel):
    text: str = Field(max_length=20000)


class PreviewCampaign(BaseModel):
    type: str | None = Field(None, pattern="^(clipping|logo|music|ugc)$")
    text: str = Field("", max_length=20000)


class CaptionPreview(BaseModel):
    brief: dict
    platform: str = Field("reels", pattern="^(reels|shorts)$")


class SoundRef(BaseModel):
    name: str = Field(max_length=200)


def get_campaign(c: AppContext, cid: str) -> dict:
    row = c.db.one("SELECT * FROM campaigns WHERE id = ?", (cid,))
    if row is None:
        raise NotFound("Campaign not found")
    return row


def _apply_parsed(brief: Brief, patch: dict) -> Brief:
    """Merge a parse result into a brief (sound names become reference sounds)."""
    patch.pop("_sources", None)
    named = patch.pop("sounds_named", [])
    nested = {k: v for k, v in patch.items() if isinstance(v, dict)}
    flat = {k: v for k, v in patch.items() if not isinstance(v, dict)}
    data = brief.model_dump()
    for k, v in nested.items():
        data[k] = (data.get(k) or {}) | v
    for k, v in flat.items():
        if k in ("rules", "checklist", "banned_keywords") and data.get(k):
            data[k] = list(dict.fromkeys([*data[k], *v]))
        else:
            data[k] = v
    for n in named:
        if not any(s["name"].lower() == n.lower() for s in data["sounds"]):
            data["sounds"].append(SoundSpec(id=new_id(), name=n).model_dump())
    return Brief.model_validate(data)


def build_from_type(kind: str | None, text: str) -> tuple[Brief, dict[str, str], list[str]]:
    """Type-first brief: type defaults → pasted brief → site rules (Clipster) when the brief is from that site.
    The type the user picked wins over the one guessed from the text."""
    patch, found = parse_brief(text) if text.strip() else ({}, [])
    sources = patch.pop("_sources", {}) if patch else {}
    guessed = patch.pop("type", None) if patch else None
    brief = base_brief(kind or guessed or "clipping")
    if patch:
        brief = _apply_parsed(brief, patch)
    brief = brief.model_copy(update={"brief_sources": dict(sources)})
    return apply_site_rules(brief), sources, [f for f in found if not f.startswith("Type:")]


_VERDICTS: dict[tuple, tuple[str, int]] = {}  # (clip id, clip updated, campaign updated) → (verdict, fails)


def _verdict(c: AppContext, r: dict, camp_updated) -> tuple[str, int]:
    key = (r["id"], r["updated_at"], camp_updated)
    if key not in _VERDICTS:
        verdict, fails = "unknown", 0
        try:
            proj = c.db.one("SELECT * FROM projects WHERE id = ?", (r["project_id"],))
            res = service.compliance(c, proj, {k: v for k, v in r.items() if k not in ("done_export", "pname")})
            verdict, fails = res["verdict"], res["fails"]
        except Exception:
            pass
        if len(_VERDICTS) > 2000:
            _VERDICTS.clear()
        _VERDICTS[key] = (verdict, fails)
    return _VERDICTS[key]


def _queue(c: AppContext, cid: str, brief: Brief) -> dict:
    """The campaign's post queue: approved clips with a finished render that aren't posted everywhere yet
    (with the check verdict and the caption for each platform), plus the clips already posted.
    Verdicts are cached until the clip or the campaign changes (the page polls while videos process)."""
    camp = c.db.one("SELECT updated_at FROM campaigns WHERE id = ?", (cid,)) or {}
    plats = posting.platforms_of(brief)
    rows = c.db.query(
        "SELECT cl.*, p.name AS pname, EXISTS (SELECT 1 FROM exports e WHERE e.clip_id = cl.id) AS done_export FROM clips cl JOIN projects p ON p.id = cl.project_id "
        "WHERE p.campaign_id = ? AND ((cl.status = 'approved' AND cl.render_status = 'done') OR EXISTS (SELECT 1 FROM posts po WHERE po.clip_id = cl.id)) "
        "ORDER BY cl.updated_at DESC",
        (cid,),
    )
    posted = posting.posted_map(c.db, [r["id"] for r in rows])
    to_post, done = [], []
    for r in rows:
        (done if posting.is_done(posted.get(r["id"]), plats) else to_post).append(r)
    # still to post: approved + rendered, or already posted on some platform (kept visible even if its
    # render went out of date or failed, with a note to re-render)

    def item(r: dict, full: bool) -> dict:
        a = r.get("analysis") or {}
        out = {
            "id": r["id"], "project_id": r["project_id"], "project_name": r["pname"], "title": r["title"],
            "duration": a.get("output_duration") or (r["end"] - r["start"]), "updated_at": r["updated_at"],
            "exported": bool(r["done_export"]), "posted": posted.get(r["id"], {}), "render_status": r["render_status"],
            "has_video": bool(r.get("render_path")) and Path(r["render_path"]).exists(),  # the file, even if outdated
        }
        if full:
            out["verdict"], out["fails"] = _verdict(c, r, camp.get("updated_at"))
            out["captions"] = {p: {"caption": t["caption"], "title": t.get("title", ""), "warnings": t.get("warnings", [])} for p, t in service.post_texts(brief, r).items() if p in plats}
        else:
            out["verdict"], out["fails"] = "unknown", 0
        return out

    done.sort(key=lambda r: -max(x["at"] for x in posted[r["id"]].values()))
    return {
        "platforms": plats,
        "ready": [item(r, True) for r in to_post[:60]],
        "ready_total": len(to_post),
        "posted": [item(r, False) for r in done[:60]],
        "posted_total": len(done),
    }


def _public(c: AppContext, row: dict, *, detail: bool = False) -> dict:
    stats = c.db.one(
        "SELECT COUNT(DISTINCT CASE WHEN p.status != 'new' THEN p.id END) AS projects, COUNT(cl.id) AS clips, SUM(cl.render_status = 'done') AS rendered, SUM(cl.status = 'approved') AS approved "
        "FROM projects p LEFT JOIN clips cl ON cl.project_id = p.id WHERE p.campaign_id = ?",
        (row["id"],),
    ) or {}
    exported = c.db.one("SELECT COUNT(*) AS n FROM exports WHERE campaign_id = ?", (row["id"],)) or {}
    brief = Brief.model_validate(row["brief"])
    b = brief.model_dump()
    b["logo"]["has_file"] = bool(brief.logo.file and Path(brief.logo.file).exists())
    b["logo"].pop("file", None)
    for s in b["sounds"]:
        s["has_file"] = bool(s.get("file") and Path(s["file"]).exists())
        s.pop("file", None)
    out = {
        "id": row["id"],
        "name": row["name"],
        "status": row["status"],
        "brief": b,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "projects": stats.get("projects") or 0,
        "clips": stats.get("clips") or 0,
        "rendered": stats.get("rendered") or 0,
        "approved": stats.get("approved") or 0,
        "exported": exported.get("n") or 0,
    }
    if detail:
        from .projects import _public as project_public
        from .projects import montage_refs

        refs = montage_refs(c)
        listed = [project_public(c, p, refs) for p in c.db.query("SELECT * FROM projects WHERE campaign_id = ? ORDER BY created_at DESC", (row["id"],))]
        out["project_list"] = [p for p in listed if p["stage"] != "footage"]  # montage source videos show inside their montage
        q = _queue(c, row["id"], brief)
        out["ready"] = q["ready"]
        out["ready_total"] = q["ready_total"]
        out["posted_clips"] = q["posted"]
        out["posted_total"] = q["posted_total"]
        out["post_platforms"] = q["platforms"]
        st = c.db.one(
            "SELECT SUM(po.views) AS views, SUM(po.likes) AS likes, COUNT(po.views) AS counted, SUM(po.url IS NOT NULL) AS links, MAX(CASE WHEN po.stats_error IS NULL THEN po.stats_at END) AS at "
            "FROM posts po JOIN clips cl ON cl.id = po.clip_id JOIN projects p ON p.id = cl.project_id WHERE p.campaign_id = ?",
            (row["id"],),
        ) or {}
        out["post_stats"] = {"views": st.get("views") or 0, "likes": st.get("likes") or 0, "counted": st.get("counted") or 0, "links": st.get("links") or 0, "at": st.get("at")}
        out["outdated"] = len(_outdated(c, row["id"]))
        out["summary"] = summarise_brief(brief, brief.brief_sources) if brief.raw_brief.strip() else None
        out["exports"] = c.db.query(
            "SELECT e.id, e.clip_id, e.path, e.created_at, cl.title, cl.project_id FROM exports e LEFT JOIN clips cl ON cl.id = e.clip_id WHERE e.campaign_id = ? ORDER BY e.created_at DESC LIMIT 200",
            (row["id"],),
        )
    return out


def _mark_stale(c: AppContext, cid: str) -> None:
    """Campaign visuals/sound changed: finished renders of its clips no longer match."""
    c.db.execute(
        "UPDATE clips SET render_status = 'stale', updated_at = ? WHERE render_status IN ('done','failed') AND project_id IN (SELECT id FROM projects WHERE campaign_id = ?)",
        (time.time(), cid),
    )


def _save(c: AppContext, row: dict, brief: Brief, *, name: str | None = None, stale: bool = False) -> dict:
    c.db.update("campaigns", row["id"], {"brief": brief.model_dump(), **({"name": name} if name else {})})
    if stale:
        _mark_stale(c, row["id"])
    return _public(c, get_campaign(c, row["id"]), detail=True)


VISUAL_KEYS = {"logo", "disclosure", "hook_overlay", "end_text", "caption_position", "sound_rule", "sounds", "allow_music", "allow_sfx", "output_height"}


@router.get("/campaign-presets")
def presets():
    return list_presets()


@router.get("/safe-zones")
def safe_zones():
    return public_zones()


@router.post("/campaigns/preview")
def preview_campaign(body: PreviewCampaign):
    """'Here's what we understood': the brief a create would make, grouped into what changes the videos
    and what is for reference. Nothing is saved."""
    brief, sources, found = build_from_type(body.type, body.text)
    return {"brief": brief.model_dump(), "summary": summarise_brief(brief, sources), "found": found}


@router.post("/campaigns/caption-preview")
def caption_preview(body: CaptionPreview):
    """The post caption a clip in this campaign gets (uses the unsaved brief from the page)."""
    sample = {"title": "Your clip's title", "hook": "Your clip's hook line goes here", "hashtags": ["#clips", "#viral"]}
    return build_post(body.brief, sample, body.platform)


@router.post("/campaigns/parse")
def parse(body: ParseText):
    patch, found = parse_brief(body.text)
    return {"patch": patch, "found": found}


@router.get("/campaigns")
def list_campaigns(c: AppContext = Depends(ctx), include_archived: bool = False):
    q = "SELECT * FROM campaigns" + ("" if include_archived else " WHERE status = 'active'") + " ORDER BY updated_at DESC"
    return [_public(c, r) for r in c.db.query(q)]


@router.post("/campaigns")
def create_campaign(body: CreateCampaign, c: AppContext = Depends(ctx)):
    found: list[str] = []
    if body.type:
        brief, _, found = build_from_type(body.type, body.text or "")
    else:
        brief = preset_brief(body.preset)
        if body.text:
            patch, found = parse_brief(body.text)
            brief = _apply_parsed(brief, patch)
    if body.brief:
        brief = merge_brief(brief, body.brief)
    if body.name:
        brief = brief.model_copy(update={"name": body.name})
    now = time.time()
    cid = new_id()
    c.db.insert("campaigns", {"id": cid, "name": brief.name, "brief": brief.model_dump(), "status": "active", "created_at": now, "updated_at": now})
    return _public(c, get_campaign(c, cid), detail=True) | {"found": found}


@router.get("/campaigns/{cid}")
def campaign_detail(cid: str, c: AppContext = Depends(ctx)):
    return _public(c, get_campaign(c, cid), detail=True)


@router.patch("/campaigns/{cid}")
def patch_campaign(cid: str, body: PatchCampaign, c: AppContext = Depends(ctx)):
    row = get_campaign(c, cid)
    old = Brief.model_validate(row["brief"])
    try:
        brief = merge_brief(old, body.brief or {})
    except ValidationError as e:  # a bad value from the page → a clear 422, not a server error
        err = e.errors()[0]
        raise InvalidInput(f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}") from e
    if body.name:
        brief = brief.model_copy(update={"name": body.name})
    removed = {s.id for s in old.sounds} - {s.id for s in brief.sounds}
    for s in old.sounds:
        if s.id in removed and s.file:
            Path(s.file).unlink(missing_ok=True)
    if body.status:
        c.db.update("campaigns", cid, {"status": body.status})
    changed = {k for k in VISUAL_KEYS if old.model_dump().get(k) != brief.model_dump().get(k)}
    return _save(c, row, brief, name=brief.name, stale=bool(changed))


@router.post("/campaigns/{cid}/apply-text")
def apply_text(cid: str, body: ParseText, c: AppContext = Depends(ctx)):
    """Paste-to-fill on an existing campaign."""
    row = get_campaign(c, cid)
    patch, found = parse_brief(body.text)
    patch.pop("type", None)  # the campaign keeps the type it was created with
    patch["brief_sources"] = patch.get("_sources", {})
    brief = apply_site_rules(_apply_parsed(Brief.model_validate(row["brief"]), patch))
    return _save(c, row, brief, name=brief.name, stale=True) | {"found": found}


@router.delete("/campaigns/{cid}")
def delete_campaign(cid: str, c: AppContext = Depends(ctx)):
    get_campaign(c, cid)
    c.db.execute("UPDATE projects SET campaign_id = NULL WHERE campaign_id = ?", (cid,))
    c.db.execute("DELETE FROM campaigns WHERE id = ?", (cid,))
    shutil.rmtree(c.paths.campaigns / cid, ignore_errors=True)
    return {"deleted": True}


# ---------------------------------------------------------------------------------- files
@router.post("/campaigns/{cid}/logo")
async def upload_logo(cid: str, request: Request, filename: str = Query(..., max_length=255), c: AppContext = Depends(ctx)):
    row = get_campaign(c, cid)
    name = safe_filename(filename, "logo.png")
    ext = Path(name).suffix.lower()
    if ext not in LOGO_EXTS:
        raise InvalidInput("Logo must be a PNG (transparent background), APNG or GIF")
    ensure_space(c.paths.campaigns, 50 * MB, int(c.settings.get().min_free_gb * GB), "the logo")
    d = c.paths.campaigns / cid
    for old in d.glob("logo.*"):
        old.unlink(missing_ok=True)
    dest = d / f"logo{ext}"
    await save_raw_upload(request, dest, max_bytes=50 * MB)
    try:
        info = inspect_logo(dest)
    except Exception:
        dest.unlink(missing_ok=True)
        raise
    brief = Brief.model_validate(row["brief"])
    logo = brief.logo.model_dump() | {"file": str(dest), "file_name": Path(filename).name[:200], "animated": info.animated, "image_w": info.width, "image_h": info.height}
    if logo["mode"] == "none":
        # A full-frame 9:16 transparent file is a pre-placed template; anything else is a logo to place.
        logo["mode"] = "template" if info.height >= 1000 and abs(info.width / info.height - 9 / 16) < 0.02 else "free"
    brief = brief.model_copy(update={"logo": type(brief.logo).model_validate(logo)})
    notes = []
    if logo["mode"] == "template" and (info.width, info.height) != (1080, 1920):
        notes.append(f"Template is {info.width}x{info.height}; it will be scaled to the 1080x1920 frame.")
    return _save(c, row, brief, stale=True) | {"notes": notes}


@router.get("/campaigns/{cid}/logo")
def get_logo(cid: str, c: AppContext = Depends(ctx)):
    brief = Brief.model_validate(get_campaign(c, cid)["brief"])
    if not brief.logo.file or not Path(brief.logo.file).exists():
        raise NotFound("No logo uploaded")
    return FileResponse(brief.logo.file, headers={"Cache-Control": "no-cache"})


@router.delete("/campaigns/{cid}/logo")
def delete_logo(cid: str, c: AppContext = Depends(ctx)):
    row = get_campaign(c, cid)
    brief = Brief.model_validate(row["brief"])
    if brief.logo.file:
        Path(brief.logo.file).unlink(missing_ok=True)
    logo = brief.logo.model_copy(update={"file": None, "file_name": None, "animated": False, "image_w": None, "image_h": None})
    return _save(c, row, brief.model_copy(update={"logo": logo}), stale=True)


@router.post("/campaigns/{cid}/sounds")
async def upload_sound(cid: str, request: Request, filename: str = Query(..., max_length=255), name: str = Query("", max_length=200), c: AppContext = Depends(ctx)):
    row = get_campaign(c, cid)
    fname = safe_filename(filename, "sound.mp3")
    if Path(fname).suffix.lower() not in AUDIO_EXTS:
        raise InvalidInput(f"Unsupported audio type. Supported: {', '.join(sorted(AUDIO_EXTS))}")
    brief = Brief.model_validate(row["brief"])
    if len(brief.sounds) >= 12:
        raise InvalidInput("A campaign can have at most 12 sounds")
    ensure_space(c.paths.campaigns, 100 * MB, int(c.settings.get().min_free_gb * GB), "the sound")
    sid = new_id()
    dest = c.paths.campaigns / cid / "sounds" / f"{sid}{Path(fname).suffix.lower()}"
    await save_raw_upload(request, dest, max_bytes=100 * MB)
    dur = audio_duration(dest)
    if dur <= 0:
        dest.unlink(missing_ok=True)
        raise InvalidInput("Couldn't read that audio file")
    sounds = [*brief.sounds, SoundSpec(id=sid, name=name or Path(filename).stem[:200], file=str(dest), file_name=Path(filename).name[:200], duration=round(dur, 2))]
    rule = brief.sound_rule
    if rule.mode == "none":
        rule = rule.model_copy(update={"mode": "burn"})
    return _save(c, row, brief.model_copy(update={"sounds": sounds, "sound_rule": rule}), stale=True)


@router.post("/campaigns/{cid}/sounds/ref")
def add_sound_ref(cid: str, body: SoundRef, c: AppContext = Depends(ctx)):
    """A sound you'll add in the app (no file): shows in the checklist."""
    row = get_campaign(c, cid)
    brief = Brief.model_validate(row["brief"])
    sounds = [*brief.sounds, SoundSpec(id=new_id(), name=body.name.strip())]
    rule = brief.sound_rule if brief.sound_rule.mode != "none" else brief.sound_rule.model_copy(update={"mode": "in_app"})
    return _save(c, row, brief.model_copy(update={"sounds": sounds, "sound_rule": rule}))


@router.delete("/campaigns/{cid}/sounds/{sid}")
def delete_sound(cid: str, sid: str, c: AppContext = Depends(ctx)):
    row = get_campaign(c, cid)
    brief = Brief.model_validate(row["brief"])
    for s in brief.sounds:
        if s.id == sid and s.file:
            Path(s.file).unlink(missing_ok=True)
    return _save(c, row, brief.model_copy(update={"sounds": [s for s in brief.sounds if s.id != sid]}), stale=True)


@router.get("/campaigns/{cid}/sounds/{sid}/audio")
def sound_audio(cid: str, sid: str, c: AppContext = Depends(ctx)):
    brief = Brief.model_validate(get_campaign(c, cid)["brief"])
    s = next((x for x in brief.sounds if x.id == sid), None)
    if not s or not s.file or not Path(s.file).exists():
        raise NotFound("Sound file not available")
    return FileResponse(s.file)


# ---------------------------------------------------------------------------------- export
def _outdated(c: AppContext, cid: str) -> list[dict]:
    """Approved clips whose render no longer matches the campaign, except ones already posted everywhere."""
    done = posting.done_ids(c.db)
    rows = c.db.query(
        "SELECT cl.id, cl.project_id FROM clips cl JOIN projects p ON p.id = cl.project_id WHERE p.campaign_id = ? AND cl.status = 'approved' AND cl.render_status = 'stale'",
        (cid,),
    )
    return [r for r in rows if r["id"] not in done]


@router.post("/campaigns/{cid}/posts/stats")
def refresh_campaign_stats(cid: str, c: AppContext = Depends(ctx)):
    """Fetch the public numbers of the campaign's post links (the newest 25)."""
    get_campaign(c, cid)
    rows = c.db.query(
        "SELECT po.clip_id, po.platform, po.url FROM posts po JOIN clips cl ON cl.id = po.clip_id JOIN projects p ON p.id = cl.project_id "
        "WHERE p.campaign_id = ? AND po.url IS NOT NULL ORDER BY po.posted_at DESC LIMIT 25",
        (cid,),
    )
    if not rows:
        raise InvalidInput("No post links yet. Add the link when you mark a clip as posted.")
    return posting.refresh_stats(c.db, rows)


@router.post("/campaigns/{cid}/rerender-outdated")
def rerender_outdated(cid: str, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    """Re-render approved clips whose video no longer matches the campaign (after requirements changed)."""
    get_campaign(c, cid)
    rows = _outdated(c, cid)
    for r in rows:
        w.enqueue(r["project_id"], "render", {"clip_id": r["id"]})
    return {"rendering": len(rows)}


@router.post("/campaigns/{cid}/export-approved")
def export_approved(cid: str, c: AppContext = Depends(ctx)):
    get_campaign(c, cid)
    results = []
    done = posting.done_ids(c.db)  # already posted everywhere: nothing left to export for
    for clip in c.db.query(
        "SELECT cl.* FROM clips cl JOIN projects p ON p.id = cl.project_id WHERE p.campaign_id = ? AND cl.status = 'approved' AND cl.render_status = 'done' ORDER BY p.created_at, cl.rank",
        (cid,),
    ):
        if clip["id"] in done:
            continue
        project = c.db.one("SELECT * FROM projects WHERE id = ?", (clip["project_id"],))
        try:
            r = service.export_clip(c, project, clip)
            results.append({"clip_id": clip["id"], "title": clip["title"], **r})
        except Exception as e:
            results.append({"clip_id": clip["id"], "title": clip["title"], "error": str(e)})
    return {"exported": sum(1 for r in results if "path" in r), "results": results, "folder": str(service.export_root(c))}
