"""Campaign brief: a platform-neutral description of what a clipping campaign requires.

Any campaign site (Clipster, Whop, Vyro, a brand's own program…) maps onto this one schema;
site-specific defaults live in ``presets.py``. Files (logo, sounds) are uploaded separately and
referenced by their stored path, which users never set directly.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator

CampaignType = Literal["clipping", "logo", "music", "ugc"]
Platform = Literal["reels", "shorts"]
LogoZone = Literal["any", "top_half", "bottom_half", "top", "middle", "bottom", "top_left", "top_right", "bottom_left", "bottom_right"]
Corner = Literal["top_left", "top_right", "bottom_left", "bottom_right"]


class LogoSpec(BaseModel):
    # template = full-frame transparent overlay (e.g. 1080x1920 "LOGO_PRE_PLACED" GIF), never moved/resized
    # free     = a logo image placed by us inside an allowed zone
    mode: Literal["none", "template", "free"] = "none"
    file: str | None = None  # stored path (set by the upload endpoint only)
    file_name: str | None = None
    animated: bool = False
    image_w: int | None = None
    image_h: int | None = None
    zone: LogoZone = "any"
    x: float = Field(0.5, ge=0, le=1, description="logo centre, fraction of output width")
    y: float = Field(0.22, ge=0, le=1, description="logo centre, fraction of output height")
    width: float = Field(0.4, ge=0.05, le=1.0, description="logo width, fraction of output width")
    opacity: float = Field(1.0, ge=0.1, le=1.0)
    max_ui_cover: float = Field(0.5, ge=0, le=1, description="max fraction of the logo covered by platform UI")
    max_nogo_overlap: float = Field(0.25, ge=0, le=1, description="max fraction of the logo inside the no-go safe zone")


class SoundSpec(BaseModel):
    id: str
    name: str = Field("", max_length=200)
    file: str | None = None  # stored path; None = reference only (add the sound in the app)
    file_name: str | None = None
    start: float = Field(0.0, ge=0, description="where in the sound file the clip should start (a specific 'cut')")
    duration: float | None = None


class SoundRule(BaseModel):
    # none   = campaign has no sound requirement
    # in_app = the official sound must be added in Instagram/YouTube when posting (can't be done by code)
    # burn   = mix the supplied sound file into the exported video
    mode: Literal["none", "in_app", "burn"] = "none"
    min_volume: float = Field(0.75, ge=0, le=1, description="minimum sound volume for the whole clip (0.75 = 75%)")
    original_volume: float = Field(0.25, ge=0, le=1, description="original audio level under a burned-in sound (0 = mute)")


class Disclosure(BaseModel):
    text: str = Field("", max_length=80)  # e.g. "#ad", "Paid partnership", "Sponsored"
    on_screen: bool = False
    in_caption: bool = True
    corner: Corner = "top_left"


class HookOverlay(BaseModel):
    enabled: bool = True
    duration: float = Field(3.5, ge=1, le=10)
    # "" = written per clip from what's said; otherwise this text on every clip ({title} = the clip's title)
    text: str = Field("", max_length=120)


EndStyle = Literal["box", "dark", "yellow", "banner", "bold", "outline"]


class EndText(BaseModel):
    """Text over the last seconds of every clip: a call to action or the brand's end screen."""

    enabled: bool = False
    # {title} = the clip's title
    text: str = Field("", max_length=120)
    duration: float = Field(2.5, ge=1, le=6)
    style: EndStyle = "dark"
    look: Literal["overlay", "card"] = "overlay"  # card: video dimmed and blurred behind the text


class Brief(BaseModel):
    name: str = Field("Untitled campaign", max_length=200)
    source: str = Field("", max_length=80, description="where the campaign is from, e.g. 'Clipster' (free text)")
    preset: str = "generic"
    type: CampaignType = "clipping"
    url: str = Field("", max_length=500)
    # money / performance targets (informational; used for the checklist)
    cpm: float | None = Field(None, ge=0, description="pay per 1,000 views")
    min_views: int | None = Field(None, ge=0)
    max_payout: float | None = Field(None, ge=0)
    min_engagement: float | None = Field(None, ge=0, le=100, description="minimum engagement rate, %")
    keep_live_days: int | None = Field(None, ge=0, le=365)
    # output requirements
    platforms: list[Platform] = ["reels", "shorts"]
    min_duration: float | None = Field(None, ge=1, le=600)
    max_duration: float | None = Field(None, ge=1, le=600)
    output_height: Literal[720, 1080] = 1080
    logo: LogoSpec = LogoSpec()
    sounds: list[SoundSpec] = Field(default_factory=list, max_length=12)
    sound_rule: SoundRule = SoundRule()
    disclosure: Disclosure = Disclosure()
    hook_overlay: HookOverlay = HookOverlay()
    end_text: EndText = EndText()
    allow_music: bool = True
    allow_sfx: bool = True
    # text rules
    english_only: bool = False
    caption_template: str = Field("{hook}\n\n{campaign_hashtags} {hashtags}\n{mentions}\n{disclosure}\n{required_text}", max_length=2000)
    required_caption_text: str = Field("", max_length=1500, description="text that must appear in every caption exactly as written")
    required_hashtags: list[str] = Field(default_factory=list, max_length=30)
    required_mentions: list[str] = Field(default_factory=list, max_length=20)
    banned_keywords: list[str] = Field(default_factory=list, max_length=200)
    # free-form
    content_notes: str = Field("", max_length=5000)
    rules: list[str] = Field(default_factory=list, max_length=60)
    checklist: list[str] = Field(default_factory=list, max_length=40)
    raw_brief: str = Field("", max_length=20000)
    brief_sources: dict[str, str] = Field(default_factory=dict)  # topic → the brief line it was read from
    # how this campaign's videos are made (R2 style templates); set on the campaign page
    style: str = Field("", max_length=40)
    intensity: Literal["", "clean", "balanced", "punchy"] = ""
    max_clips: int | None = Field(None, ge=1, le=30)
    # optional overrides of the style's framing and caption look ("" = what the style uses)
    layout: Literal["", "auto", "center", "blur", "split", "stream"] = ""
    caption_preset: str = Field("", max_length=40)
    # where captions sit for every clip (centre, fraction of the height); None = the caption look's own spot
    caption_position: float | None = Field(None, ge=0.1, le=0.92)

    @field_validator("required_hashtags")
    @classmethod
    def _tags(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = re.sub(r"[^\w]", "", t.strip().lstrip("#"))[:60]
            if t and f"#{t}" not in out:
                out.append(f"#{t}")
        return out

    @field_validator("required_mentions")
    @classmethod
    def _mentions(cls, v: list[str]) -> list[str]:
        out = []
        for t in v:
            t = re.sub(r"[^\w.]", "", t.strip().lstrip("@"))[:60]
            if t and f"@{t}" not in out:
                out.append(f"@{t}")
        return out

    @field_validator("banned_keywords", "rules", "checklist")
    @classmethod
    def _clean_list(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(x.strip()[:300] for x in v if x and x.strip()))

    def effective_duration(self) -> tuple[float | None, float | None]:
        return self.min_duration, self.max_duration


# Fields a client may set; file paths are only set by upload endpoints.
PROTECTED = {("logo", "file"), ("logo", "file_name"), ("logo", "animated"), ("logo", "image_w"), ("logo", "image_h")}


def merge_brief(old: Brief, patch: dict) -> Brief:
    """Apply a client patch, keeping server-managed file fields from ``old``."""
    data = old.model_dump()
    for k, v in patch.items():
        if k == "logo" and isinstance(v, dict):
            v = {kk: vv for kk, vv in v.items() if ("logo", kk) not in PROTECTED}
            data["logo"] = data["logo"] | v
        elif k == "sounds" and isinstance(v, list):
            # clients may rename / reorder / set start offsets / remove, but never point at files
            by_id = {s["id"]: s for s in data["sounds"]}
            new = []
            for s in v:
                if not isinstance(s, dict) or s.get("id") not in by_id:
                    continue
                cur = by_id[s["id"]]
                new.append(cur | {kk: s[kk] for kk in ("name", "start") if kk in s})
            data["sounds"] = new
        else:
            data[k] = v
    return Brief.model_validate(data)
