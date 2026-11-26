"""Style templates on the server (mirror of frontend/src/components/styles.tsx).

Used when a video in a campaign is processed without explicit options (e.g. "Start with default settings",
or a video moved into a campaign and re-processed): the campaign's style then still applies.
"""

from __future__ import annotations

STYLES: dict[str, dict] = {
    "talking": {"caption_preset": "hormozi", "hook_overlay": True, "sfx": "off", "intensity": "balanced", "emoji": False},
    "gaming": {"caption_preset": "bold", "hook_overlay": True, "sfx": "auto", "intensity": "punchy", "emoji": False},
    "hype": {"caption_preset": "beast", "hook_overlay": True, "sfx": "auto", "intensity": "punchy", "emoji": True},
    "clean": {"caption_preset": "minimal", "hook_overlay": False, "sfx": "off", "intensity": "clean", "emoji": False},
}

INTENSITY: dict[str, dict] = {
    "clean": {"jumpcut": "light", "fillers": False, "zoom": "off", "emphasis": False, "progress": "off"},
    "balanced": {"jumpcut": "normal", "fillers": True, "zoom": "subtle", "emphasis": True, "progress": "off"},
    "punchy": {"jumpcut": "tight", "fillers": True, "zoom": "punchy", "emphasis": True, "progress": "bottom"},
}


def campaign_defaults(brief) -> dict:
    """Project options a campaign implies (style, intensity, framing, caption look, clips per video)."""
    st = STYLES.get(brief.style or "gaming", STYLES["gaming"])
    out: dict = {"caption_preset": st["caption_preset"], "hook_overlay": st["hook_overlay"], "sfx": st["sfx"]}
    out["polish"] = INTENSITY.get(brief.intensity or st["intensity"], INTENSITY["balanced"]) | {"emoji": st["emoji"]}
    if brief.layout:
        out["layout"] = brief.layout
    if brief.caption_preset:
        out["caption_preset"] = brief.caption_preset
    if brief.max_clips:
        out["max_clips"] = brief.max_clips
    return out
