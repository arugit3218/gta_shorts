"""Plain-language summary of a brief: what changes the videos vs what is for reference only (R1).

Used by the "Here's what we understood" step when creating a campaign. ``sources`` maps a topic to the
brief line it was read from (see ``parser.parse_brief``) so each item can quote the brief.
"""

from __future__ import annotations

from .models import Brief

PLATFORM = {"reels": "Instagram Reels", "shorts": "YouTube Shorts"}


def _secs(v: float) -> str:
    return f"{v:g}"


def summarise_brief(b: Brief, sources: dict[str, str] | None = None) -> dict:
    src = sources or {}
    video: list[dict] = []

    def add(key: str, label: str, detail: str, *, todo: str | None = None, quote_key: str | None = None) -> None:
        video.append({"key": key, "label": label, "detail": detail, "quote": src.get(quote_key or key), "todo": todo})

    lo, hi = b.min_duration, b.max_duration
    if lo and hi:
        add("length", f"Clips are {_secs(lo)}–{_secs(hi)} seconds", "Clips are cut to fit this length.")
    elif lo:
        add("length", f"Clips are at least {_secs(lo)} seconds", "Shorter moments are skipped.")
    elif hi:
        add("length", f"Clips are at most {_secs(hi)} seconds", "Longer moments are trimmed.")

    if b.type == "logo" or b.logo.mode != "none":
        tmpl = b.logo.mode == "template"
        add(
            "logo",
            "Brand logo on screen the whole time",
            "The brand's full-frame template file, laid over the clip exactly as supplied." if tmpl else "You place it on the frame; it stays visible for the whole clip.",
            todo=None if b.logo.file else ("Upload the brand's template file" if tmpl else "Upload the logo file and place it"),
        )

    d = b.disclosure
    if d.text and (d.in_caption or d.on_screen):
        where = " and ".join(x for x in ("in every caption" if d.in_caption else "", "on screen" if d.on_screen else "") if x)
        add("disclosure", f"{d.text} {where}", "Marks the post as paid, as the brand asks.")
    if b.required_caption_text.strip():
        t = b.required_caption_text.strip()
        add("required_text", "Required caption text, exactly as written", f"“{t[:90]}{'…' if len(t) > 90 else ''}” Every caption is checked for it.")

    r = b.sound_rule
    if r.mode == "in_app":
        add("sound", "Add the brand's sound when posting", f"Pick it in the Instagram/YouTube app, at least {r.min_volume * 100:.0f}% volume. The post checklist reminds you.")
    elif r.mode == "burn":
        add("sound", "The brand's sound is mixed into every clip", f"At least {r.min_volume * 100:.0f}% volume.", todo=None if any(s.file for s in b.sounds) else "Upload the sound file")

    if not b.allow_music and not b.allow_sfx:
        add("extras", "No background music or sound effects", "Both stay off for this campaign's clips.", quote_key="music")
    elif not b.allow_music:
        add("extras", "No background music", "Background music stays off for this campaign's clips.", quote_key="music")
    elif not b.allow_sfx:
        add("extras", "No sound effects", "Sound effects stay off for this campaign's clips.", quote_key="sfx")

    if b.english_only:
        add("english", "All text in English", "Captions, titles and on-screen text are checked.")
    tags = [*b.required_hashtags, *b.required_mentions]
    if tags:
        add("tags", "Caption includes " + " ".join(tags[:6]), "Added to every caption automatically.")
    if b.banned_keywords:
        add("banned", f"Avoids {len(b.banned_keywords)} banned topics", ", ".join(b.banned_keywords[:6]) + ("…" if len(b.banned_keywords) > 6 else ""))
    if b.platforms:
        add("platforms", " + ".join(PLATFORM[p] for p in b.platforms), "Keeps the logo and captions clear of each app's buttons; hashtags stay within each app's limit.")

    ref: list[dict] = []

    def ref_add(key: str, label: str, value: str) -> None:
        ref.append({"key": key, "label": label, "value": value, "quote": src.get(key)})

    if b.cpm is not None:
        ref_add("cpm", "Pay", f"${b.cpm:g} per 1,000 views")
    if b.max_payout is not None:
        ref_add("max_payout", "Max payout", f"${b.max_payout:,.0f}")
    if b.min_views is not None:
        ref_add("min_views", "Min views", f"{b.min_views:,}")
    if b.min_engagement is not None:
        ref_add("min_engagement", "Min engagement", f"{b.min_engagement:g}%")
    if b.keep_live_days is not None:
        ref_add("keep_live", "Keep posts live", f"{b.keep_live_days} days")
    if b.source:
        ref_add("source", "From", b.source)
    return {"video": video, "reference": ref, "checklist": list(b.checklist), "rules": list(b.rules)}
