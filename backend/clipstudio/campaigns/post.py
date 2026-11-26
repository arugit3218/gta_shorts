"""Post caption builder (A7): per-platform caption text from a template + clip metadata.

Template tokens: {title} {hook} {description} {hashtags} {campaign_hashtags} {mentions}
{disclosure} {required_text} {cta}. Empty tokens collapse cleanly (no dangling blank lines). The
campaign's required caption text is always included, even if a custom template leaves it out.
"""

from __future__ import annotations

import re

LIMITS = {
    # Instagram: 2,200-character captions; hashtags are capped at 5 per post (Dec 2025 change).
    "reels": {"caption": 2200, "hashtags": 5, "title": None},
    # YouTube Shorts: title ≤ 100 chars, description ≤ 5,000; more than 60 hashtags → all ignored.
    "shorts": {"caption": 5000, "hashtags": 15, "title": 100},
}


def _tags(items: list[str]) -> list[str]:
    out: list[str] = []
    for t in items or []:
        t = "#" + re.sub(r"[^\w]", "", str(t).lstrip("#"))
        if len(t) > 1 and t.lower() not in (x.lower() for x in out):
            out.append(t)
    return out


def build_post(brief: dict | None, clip: dict, platform: str) -> dict:
    """Returns {title, caption, hashtags, warnings} for one platform."""
    brief = brief or {}
    lim = LIMITS.get(platform, LIMITS["reels"])
    campaign_tags = _tags(brief.get("required_hashtags", []))
    clip_tags = [t for t in _tags(clip.get("hashtags", [])) if t.lower() not in (c.lower() for c in campaign_tags)]
    max_tags = lim["hashtags"]
    room = max(0, max_tags - len(campaign_tags))
    used_clip_tags = clip_tags[:room]
    disclosure = (brief.get("disclosure") or {})
    disc_text = disclosure.get("text", "") if disclosure.get("in_caption", True) else ""
    tokens = {
        "title": clip.get("title", ""),
        "hook": clip.get("hook", "") or clip.get("title", ""),
        "description": clip.get("description", ""),
        "hashtags": " ".join(used_clip_tags),
        "campaign_hashtags": " ".join(campaign_tags),
        "mentions": " ".join(brief.get("required_mentions", [])),
        "disclosure": disc_text,
        "required_text": (brief.get("required_caption_text") or "").strip(),
        "cta": "",
    }
    template = brief.get("caption_template") or "{hook}\n\n{campaign_hashtags} {hashtags}\n{mentions}\n{disclosure}"
    text = template
    for k, v in tokens.items():
        text = text.replace("{" + k + "}", v or "")
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    out: list[str] = []
    for ln in lines:
        if ln or (out and out[-1]):
            out.append(ln)
    caption = "\n".join(out).strip()
    req = tokens["required_text"]
    if req and req not in caption:
        caption = f"{caption}\n{req}".strip()
    warnings: list[str] = []
    if len(caption) > lim["caption"]:
        warnings.append(f"Caption is {len(caption)} characters; the limit is {lim['caption']}.")
    n_tags = len(re.findall(r"(?<!\w)#\w+", caption))
    if n_tags > max_tags:
        warnings.append(f"{n_tags} hashtags; {'Instagram allows at most 5' if platform == 'reels' else 'keep it to about 15'}.")
    title = clip.get("title", "")
    if lim["title"] and len(title) > lim["title"]:
        warnings.append(f"Title is {len(title)} characters; YouTube allows {lim['title']}.")
        title = title[: lim["title"] - 1].rstrip() + "…"
    if disclosure.get("text") and disclosure.get("in_caption", True) and disclosure["text"] not in caption:
        warnings.append("The caption template doesn't include {disclosure}, but the campaign requires a disclosure.")
    return {"platform": platform, "title": title, "caption": caption, "hashtags": campaign_tags + used_clip_tags, "warnings": warnings}
