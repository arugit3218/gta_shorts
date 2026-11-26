"""Recipes: saved looks (caption style, framing, finishing, retention polish) applied in one click."""

from __future__ import annotations

import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from ..campaigns.service import campaign_brief, features
from ..context import AppContext
from ..db import new_id
from ..errors import InvalidInput, NotFound
from ..jobs.worker import Worker
from ..media.captions import PRESETS
from .deps import ctx, get_clip, worker
from .schemas import RecipeBody

router = APIRouter(prefix="/api", tags=["recipes"])

CLIP_KEYS = ("caption_preset", "caption_style", "layout", "aspect_ratio", "output_height", "music", "sfx", "blur_burned")


def get_recipe(c: AppContext, rid: str) -> dict:
    r = c.db.one("SELECT * FROM recipes WHERE id = ?", (rid,))
    if r is None:
        raise NotFound("Recipe not found")
    return r


def public(r: dict) -> dict:
    return {"id": r["id"], "name": r["name"], **(r["recipe"] or {}), "created_at": r["created_at"], "updated_at": r["updated_at"]}


def _clean(body: RecipeBody) -> dict:
    d = body.model_dump(exclude_none=True)
    if d.get("caption_preset") and d["caption_preset"] not in PRESETS:
        raise InvalidInput("Unknown caption preset")
    d.pop("name", None)
    d["polish"] = {k: v for k, v in (d.get("polish") or {}).items() if v is not None}
    if "caption_style" in d:
        d["caption_style"] = {k: v for k, v in d["caption_style"].items() if v is not None} or None
        if d["caption_style"] is None:
            d.pop("caption_style")
    return d


def apply_to_edit(recipe: dict, edit: dict, *, campaign: bool) -> dict:
    """A clip's ``edit`` with a recipe applied (fields the recipe doesn't set are kept)."""
    out = dict(edit)
    for k in CLIP_KEYS:
        if k in recipe and recipe[k] is not None:
            if campaign and k in ("aspect_ratio", "output_height"):
                continue  # a campaign decides the format
            out[k] = recipe[k]
    if "caption_preset" in recipe and "caption_style" not in recipe:
        out.pop("caption_style", None)
    if recipe.get("hook_overlay") is not None:
        out["hook_overlay"] = (out.get("hook_overlay") or {}) | {"enabled": bool(recipe["hook_overlay"])}
    if recipe.get("polish"):
        out["polish"] = (out.get("polish") or {}) | recipe["polish"]
    return out


@router.get("/recipes")
def list_recipes(c: AppContext = Depends(ctx)):
    return [public(r) for r in c.db.query("SELECT * FROM recipes ORDER BY name COLLATE NOCASE")]


@router.post("/recipes")
def create_recipe(body: RecipeBody, c: AppContext = Depends(ctx)):
    rid = new_id()
    now = time.time()
    c.db.insert("recipes", {"id": rid, "name": body.name.strip(), "recipe": _clean(body), "created_at": now, "updated_at": now})
    return public(get_recipe(c, rid))


@router.put("/recipes/{rid}")
def update_recipe(rid: str, body: RecipeBody, c: AppContext = Depends(ctx)):
    get_recipe(c, rid)
    c.db.update("recipes", rid, {"name": body.name.strip(), "recipe": _clean(body)})
    return public(get_recipe(c, rid))


@router.delete("/recipes/{rid}")
def delete_recipe(rid: str, c: AppContext = Depends(ctx)):
    get_recipe(c, rid)
    c.db.execute("DELETE FROM recipes WHERE id = ?", (rid,))
    return {"deleted": True}


class ApplyRecipe(BaseModel):
    recipe_id: str = Field(max_length=40)
    render: bool = False


@router.post("/clips/{clip_id}/apply-recipe")
def apply_recipe(clip_id: str, body: ApplyRecipe, c: AppContext = Depends(ctx), w: Worker = Depends(worker)):
    from .clips import clip_detail

    clip = get_clip(c, clip_id)
    project = c.db.one("SELECT * FROM projects WHERE id = ?", (clip["project_id"],))
    recipe = get_recipe(c, body.recipe_id)["recipe"]
    edit = apply_to_edit(recipe, clip["edit"] or {}, campaign=bool(project and project.get("campaign_id")))
    upd: dict = {"edit": edit}
    if edit != clip["edit"] and clip["render_status"] in ("done", "failed"):
        upd["render_status"] = "stale"
    c.db.update("clips", clip_id, upd)
    if body.render:
        w.enqueue(clip["project_id"], "render", {"clip_id": clip_id})
    return clip_detail(clip_id, c)


class SaveRecipe(BaseModel):
    name: str = Field(min_length=1, max_length=80)


@router.post("/clips/{clip_id}/save-recipe")
def save_recipe_from_clip(clip_id: str, body: SaveRecipe, c: AppContext = Depends(ctx)):
    """Save a clip's current look (as it would render now) as a recipe."""
    clip = get_clip(c, clip_id)
    project = c.db.one("SELECT * FROM projects WHERE id = ?", (clip["project_id"],))
    s = c.settings.get()
    e = clip["edit"] or {}
    feat = features(c, project, clip, campaign_brief(c, project)[1])
    music = feat["music"] if feat["music"] in ("none", "auto") else None
    recipe = {
        "caption_preset": e.get("caption_preset", s.caption_preset),
        **({"caption_style": e["caption_style"]} if e.get("caption_style") else {}),
        "layout": e.get("layout", s.layout),
        "aspect_ratio": e.get("aspect_ratio", s.aspect_ratio),
        "output_height": e.get("output_height", s.output_height),
        **({"music": music} if music else {}),
        "sfx": "off" if feat["sfx"] == "off" else "auto",
        "blur_burned": feat["blur_burned"],
        "hook_overlay": feat["hook_overlay"]["enabled"],
        "polish": feat["polish"],
    }
    rid = new_id()
    now = time.time()
    c.db.insert("recipes", {"id": rid, "name": body.name.strip(), "recipe": recipe, "created_at": now, "updated_at": now})
    return public(get_recipe(c, rid))
