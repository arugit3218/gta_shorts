"""Rule presets: starting points for a campaign brief.

Presets only pre-fill a brief; every value stays editable. "Generic" presets describe common campaign
types on any site. Site presets (Clipster) encode that site's published general rules
(https://www.clipster.com/general-campaign-rules, as shared by the user in Nov 2026); a specific
campaign's brief always wins over them.
"""

from __future__ import annotations

from .models import Brief

COMMON_CHECKLIST = [
    "Post from an account whose niche matches the campaign",
    "Keep the account's view counts and stats public",
    "Submit the post link to the campaign before the deadline",
]

CLIPSTER_GENERAL_RULES = [
    "No duplicate content: every post must be a unique clip",
    "Keep posts live for at least 30 days",
    "Stats must stay public; no bought views or engagement",
    "Banned niches: South-Asian-focused content, cricket, mobile games, NSFW",
    "Do not combine two campaigns in one post",
]

CLIPSTER_BANNED = ["cricket", "ipl", "bgmi", "free fire", "pubg mobile", "nsfw", "onlyfans"]


def _generic(**kw) -> Brief:
    return Brief(**kw)


PRESETS: dict[str, dict] = {
    "generic": {
        "label": "Generic: clipping",
        "description": "Clip long videos into shorts. No logo or sound requirements.",
        "brief": _generic(preset="generic", type="clipping", checklist=COMMON_CHECKLIST),
    },
    "generic_logo": {
        "label": "Generic: logo",
        "description": "Brand logo on every clip, placed in an allowed zone.",
        "brief": _generic(
            preset="generic_logo",
            type="logo",
            logo={"mode": "free", "zone": "top_half", "x": 0.5, "y": 0.22, "width": 0.4},
            checklist=COMMON_CHECKLIST + ["Logo visible for the whole clip"],
        ),
    },
    "generic_music": {
        "label": "Generic: music / sound",
        "description": "Promote a song: official sound on every post, no extra music.",
        "brief": _generic(
            preset="generic_music",
            type="music",
            sound_rule={"mode": "in_app", "min_volume": 0.75},
            allow_music=False,
            allow_sfx=False,
            checklist=COMMON_CHECKLIST + ["Use the official sound from the in-app library", "Keep the sound audible for the whole clip"],
        ),
    },
    "clipster_logo": {
        "label": "Clipster: logo campaign",
        "description": "Clipster's pre-placed logo template (1080x1920, looped, not moved or resized).",
        "brief": _generic(
            preset="clipster_logo",
            source="Clipster",
            type="logo",
            output_height=1080,
            logo={"mode": "template", "zone": "any", "max_ui_cover": 0.5, "max_nogo_overlap": 0.25},
            keep_live_days=30,
            banned_keywords=CLIPSTER_BANNED,
            rules=CLIPSTER_GENERAL_RULES
            + [
                "Use the LOGO_PRE_PLACED template file as supplied: don't move, resize or crop it",
                "Logo must loop for the full length of the clip",
                "At most 50% of the logo may be covered by platform UI",
                "At most 25% of the logo may overlap the safe-zone no-go area",
            ],
            checklist=COMMON_CHECKLIST + ["Logo template visible and looping for the whole clip", "Keep the post live for 30 days"],
        ),
    },
    "clipster_music": {
        "label": "Clipster: music campaign",
        "description": "Official sound at ≥75% volume for the whole clip, >10 s, English-only text.",
        "brief": _generic(
            preset="clipster_music",
            source="Clipster",
            type="music",
            min_duration=10.5,
            min_engagement=3.0,
            keep_live_days=30,
            sound_rule={"mode": "in_app", "min_volume": 0.75},
            allow_music=False,
            allow_sfx=False,
            english_only=True,
            banned_keywords=CLIPSTER_BANNED,
            rules=CLIPSTER_GENERAL_RULES
            + [
                "Sound must play at 75% volume or more for the whole video",
                "Video must be longer than 10 seconds",
                "Engagement rate of at least 3%",
                "All on-screen text and captions in English",
                "Not combined with any other campaign",
            ],
            checklist=COMMON_CHECKLIST
            + [
                "Add the official sound from the in-app library and set it to 75% or more",
                "Comment the song name on your post and like your own comment (if the brief asks)",
                "Keep the post live for 30 days",
            ],
        ),
    },
    "clipster_clipping": {
        "label": "Clipster: clipping campaign",
        "description": "Clip a creator's content under Clipster's general rules.",
        "brief": _generic(
            preset="clipster_clipping",
            source="Clipster",
            type="clipping",
            keep_live_days=30,
            banned_keywords=CLIPSTER_BANNED,
            rules=CLIPSTER_GENERAL_RULES,
            checklist=COMMON_CHECKLIST + ["Keep the post live for 30 days"],
        ),
    },
    "clipster_ugc": {
        "label": "Clipster: UGC campaign",
        "description": "Your own on-camera content following the brief's trend.",
        "brief": _generic(
            preset="clipster_ugc",
            source="Clipster",
            type="ugc",
            english_only=True,
            keep_live_days=30,
            banned_keywords=CLIPSTER_BANNED,
            rules=CLIPSTER_GENERAL_RULES + ["Real (IRL) creator footage only", "On-screen text in English"],
            checklist=COMMON_CHECKLIST + ["Follow the trend described in the brief", "Keep the post live for 30 days"],
        ),
    },
}


def preset_brief(key: str) -> Brief:
    if key not in PRESETS:
        key = "generic"
    return PRESETS[key]["brief"].model_copy(deep=True)


def list_presets() -> list[dict]:
    return [{"key": k, "label": v["label"], "description": v["description"], "brief": v["brief"].model_dump()} for k, v in PRESETS.items()]


# ------------------------------------------------------------------------------ R1: type-first creation
# A campaign starts from its type; site rules (Clipster) are added only when the pasted brief is from that
# site. The old preset list above stays for API compatibility.
TYPE_DEFAULTS: dict[str, dict] = {
    "clipping": {"type": "clipping", "checklist": COMMON_CHECKLIST},
    "logo": {"type": "logo", "logo": {"mode": "free", "zone": "any", "x": 0.5, "y": 0.2, "width": 0.4}, "checklist": COMMON_CHECKLIST},
    "music": {
        "type": "music",
        "sound_rule": {"mode": "in_app", "min_volume": 0.75},
        "allow_music": False,
        "allow_sfx": False,
        "hook_overlay": {"enabled": False},
        "checklist": COMMON_CHECKLIST + ["Use the official sound from the in-app library", "Keep the sound audible for the whole clip"],
    },
    "ugc": {"type": "ugc", "checklist": COMMON_CHECKLIST},
}

CLIPSTER_BY_TYPE: dict[str, dict] = {
    "logo": {"rules": ["At most 50% of the logo may be covered by platform UI", "At most 25% of the logo may overlap the safe-zone no-go area"]},
    "music": {
        "min_duration": 10.5,
        "min_engagement": 3.0,
        "english_only": True,
        "rules": ["Sound must play at 75% volume or more for the whole video", "Not combined with any other campaign"],
        "checklist": ["Comment the song name on your post and like your own comment (if the brief asks)"],
    },
    "ugc": {"english_only": True, "rules": ["Real (IRL) creator footage only"]},
}


def base_brief(kind: str) -> Brief:
    data = TYPE_DEFAULTS.get(kind) or TYPE_DEFAULTS["clipping"]
    return Brief.model_validate({"preset": kind if kind in TYPE_DEFAULTS else "clipping", **data})


def apply_site_rules(brief: Brief) -> Brief:
    """Add a campaign site's general rules on top of the brief (only Clipster is known). Values the brief
    already sets win; lists are merged."""
    if (brief.source or "").lower() != "clipster":
        return brief
    data = brief.model_dump()
    extra = {"keep_live_days": 30, "banned_keywords": CLIPSTER_BANNED, "rules": CLIPSTER_GENERAL_RULES, "checklist": ["Keep the post live for 30 days"]}
    t = CLIPSTER_BY_TYPE.get(brief.type, {})
    for src in (extra, t):
        for k, v in src.items():
            if isinstance(v, list):
                data[k] = list(dict.fromkeys([*(data.get(k) or []), *v]))
            elif data.get(k) in (None, False, 0, ""):
                data[k] = v
    return Brief.model_validate(data)
