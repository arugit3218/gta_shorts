"""Compliance checker (A8): does a clip meet its campaign's rules before you post it?

Each check returns pass / warn / fail, or "manual" for rules only you can confirm (e.g. "comment the
song name"). Checks are pure functions over a ``ClipFacts`` snapshot, so they are easy to test and
explain. Nothing here predicts approval; it catches the mistakes that get submissions rejected.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

import numpy as np

from ..analysis.lexicon import STOPWORDS
from .models import Brief
from .safezones import GRID_H, GRID_W, LABELS, overlap_fraction, rect_mask

PLATFORM_MAX = 180.0  # Reels and Shorts both allow up to 3 minutes


@dataclass
class Check:
    id: str
    label: str
    status: str  # pass | warn | fail | manual | info
    detail: str = ""

    def as_dict(self) -> dict:
        return {"id": self.id, "label": self.label, "status": self.status, "detail": self.detail}


@dataclass
class ClipFacts:
    duration: float
    rendered: bool
    stale: bool
    width: int | None = None
    height: int | None = None
    has_audio: bool = True
    caption_text: str = ""
    hook_text: str = ""
    end_text: str = ""  # text over the last seconds, when on
    end_required: bool = False  # the campaign asks for end text on every clip
    caption_logo_overlap: float = 0.0  # share of the caption block the logo sits on
    caption_set_by_clip: bool = False
    disclosure_on_screen: str = ""
    transcript: str = ""
    title: str = ""
    posts: dict[str, str] = field(default_factory=dict)  # platform -> caption
    logo_enabled: bool = False
    logo_mask: np.ndarray | None = None  # visible logo pixels on the safe-zone grid
    music: bool = False
    sfx: bool = False
    sound_burned: bool = False
    sound_volume: float = 0.0
    burned_text_detected: bool = False
    burned_text_blurred: bool = False
    duplicates_other: list[str] = field(default_factory=list)  # names of other campaigns with overlapping footage
    duplicates_same: int = 0


NON_LATIN = re.compile(r"[^\x00-ɏ -⁯₠-⃏←-⇿☀-➿\U0001F000-\U0001FAFF️]")


def english_issues(text: str) -> tuple[str, str] | None:
    """(status, detail) when text doesn't look English, else None. Emoji are fine."""
    text = (text or "").strip()
    if not text:
        return None
    odd = NON_LATIN.findall(text)
    if len(odd) >= 2 and len(odd) / max(1, len(text)) > 0.03:
        return "fail", f"Contains non-English script: “{''.join(dict.fromkeys(odd))[:12]}”"
    words = re.findall(r"[A-Za-z']+", text.lower())
    if len(words) >= 8:
        ratio = sum(1 for w in words if w in STOPWORDS) / len(words)
        if ratio < 0.12:
            return "warn", "Text may not be English (few common English words)"
    return None


def _keyword_hits(keywords: list[str], text: str) -> list[str]:
    low = text.lower()
    hits = []
    for k in keywords:
        k2 = k.strip().lower()
        if k2 and re.search(r"(?<!\w)" + re.escape(k2) + r"(?!\w)", low):
            hits.append(k)
    return hits


def check_clip(brief: Brief | None, f: ClipFacts) -> list[Check]:
    out: list[Check] = []
    b = brief or Brief()

    # Render state ------------------------------------------------------------------
    if not f.rendered:
        out.append(Check("render", "Rendered", "fail", "Render the clip first."))
    elif f.stale:
        out.append(Check("render", "Rendered", "fail", "Edits were made after the last render. Re-render before exporting."))
    else:
        out.append(Check("render", "Rendered", "pass"))

    # Length ------------------------------------------------------------------------
    lo, hi = b.min_duration, b.max_duration
    d = f.duration
    if lo and d < lo:
        out.append(Check("length", "Length", "fail", f"{d:.1f}s is shorter than the campaign minimum of {lo:g}s."))
    elif hi and d > hi:
        out.append(Check("length", "Length", "fail", f"{d:.1f}s is longer than the campaign maximum of {hi:g}s."))
    elif d > PLATFORM_MAX:
        out.append(Check("length", "Length", "fail", f"{d:.0f}s is over the 3-minute limit for Reels and Shorts."))
    else:
        rng = f"{lo:g}s minimum" if lo else ""
        rng += (", " if rng and hi else "") + (f"{hi:g}s maximum" if hi else "")
        out.append(Check("length", "Length", "pass", f"{d:.1f}s" + (f" ({rng})" if rng else "")))

    # Format ------------------------------------------------------------------------
    if f.width and f.height:
        ratio = f.width / f.height
        if abs(ratio - 9 / 16) > 0.01:
            out.append(Check("format", "Vertical 9:16", "fail", f"Video is {f.width}x{f.height}; Reels and Shorts need 9:16."))
        elif b.logo.mode == "template" and (f.width, f.height) != (1080, 1920):
            out.append(Check("format", "Vertical 9:16", "fail", f"Logo template campaigns need 1080x1920 output; this is {f.width}x{f.height}."))
        elif f.height < 1920 and b.output_height == 1080:
            out.append(Check("format", "Vertical 9:16", "warn", f"{f.width}x{f.height}. The campaign prefers 1080x1920."))
        else:
            out.append(Check("format", "Vertical 9:16", "pass", f"{f.width}x{f.height}"))
    if not f.has_audio:
        out.append(Check("audio", "Has audio", "warn", "The video has no audio track."))

    # Logo --------------------------------------------------------------------------
    needs_logo = b.type == "logo" or b.logo.mode != "none"
    if needs_logo:
        if b.logo.mode == "none" or not b.logo.file:
            out.append(Check("logo", "Logo", "fail", "Upload the campaign logo in the campaign settings."))
        elif not f.logo_enabled:
            out.append(Check("logo", "Logo", "fail", "The logo is turned off for this clip."))
        else:
            out.append(Check("logo", "Logo", "pass", "Pre-placed template, looped for the whole clip (not moved or resized)" if b.logo.mode == "template" else "Logo on screen for the whole clip"))
            if f.logo_mask is not None and f.logo_mask.any():
                plats = list(b.platforms) or ["reels", "shorts"]
                if b.logo.mode == "free" and b.logo.zone != "any":
                    from ..media.logo import ZONE_RECTS

                    inside = float((f.logo_mask & rect_mask(ZONE_RECTS[b.logo.zone])).sum()) / float(f.logo_mask.sum())
                    st = "pass" if inside >= 0.97 else "fail"
                    out.append(Check("logo_zone", f"Logo in {b.logo.zone.replace('_', ' ')}", st, f"{inside * 100:.0f}% of the logo is inside the required zone."))
                for p in plats:
                    ui = overlap_fraction(f.logo_mask, [p], "ui")
                    ng = overlap_fraction(f.logo_mask, [p], "nogo")
                    st = "pass"
                    msgs = [f"{ui * 100:.0f}% under {LABELS[p]} UI (max {b.logo.max_ui_cover * 100:.0f}%)", f"{ng * 100:.0f}% in the no-go zone (max {b.logo.max_nogo_overlap * 100:.0f}%)"]
                    if ui > b.logo.max_ui_cover or ng > b.logo.max_nogo_overlap:
                        st = "fail"
                    elif ui > b.logo.max_ui_cover * 0.8 or ng > b.logo.max_nogo_overlap * 0.8:
                        st = "warn"
                    out.append(Check(f"logo_safe_{p}", f"Logo safe zone · {LABELS[p]}", st, "; ".join(msgs)))

    # Text vs logo ------------------------------------------------------------------
    if f.logo_enabled and f.caption_text and f.logo_mask is not None and f.logo_mask.any():
        ov = f.caption_logo_overlap
        where = "Move them in the Captions tab (Caption style → Vertical position) or move the logo." if f.caption_set_by_clip else "Set the caption position on the campaign or move the logo."
        if ov > 0.25:
            out.append(Check("caption_logo", "Captions clear of the logo", "fail", f"The logo covers about {ov * 100:.0f}% of the captions. {where}"))
        elif ov > 0:
            out.append(Check("caption_logo", "Captions clear of the logo", "warn", f"The logo touches the captions (about {ov * 100:.0f}%). {where}"))
        else:
            out.append(Check("caption_logo", "Captions clear of the logo", "pass", "Captions and logo don't overlap"))

    # End text ------------------------------------------------------------------------
    if f.end_required:
        out.append(Check("end_text", "End text", "pass" if f.end_text else "fail", f"“{f.end_text}” at the end" if f.end_text else "The campaign asks for end text; it's turned off for this clip (Captions tab → End text)."))

    # Disclosure --------------------------------------------------------------------
    disc = b.disclosure
    if disc.text and disc.on_screen:
        out.append(Check("disclosure", "On-screen disclosure", "pass" if f.disclosure_on_screen else "fail", f"“{disc.text}” shown on screen" if f.disclosure_on_screen else f"Show “{disc.text}” on screen (turned off for this clip)."))

    # Sound -------------------------------------------------------------------------
    rule = b.sound_rule
    if rule.mode == "in_app":
        out.append(Check("sound_in_app", "Official sound", "manual", f"Add the campaign sound in the app when posting and set it to at least {rule.min_volume * 100:.0f}% volume for the whole clip."))
    elif rule.mode == "burn":
        if not f.sound_burned:
            out.append(Check("sound", "Campaign sound", "fail", "Pick one of the campaign's sounds for this clip."))
        elif f.sound_volume + 1e-6 < rule.min_volume:
            out.append(Check("sound", "Campaign sound", "fail", f"Sound volume is {f.sound_volume * 100:.0f}%; the campaign requires at least {rule.min_volume * 100:.0f}%."))
        else:
            out.append(Check("sound", "Campaign sound", "pass", f"Mixed in at {f.sound_volume * 100:.0f}% for the whole clip"))
    if not b.allow_music and f.music:
        out.append(Check("music", "No extra music", "fail", "This campaign doesn't allow background music. Turn music off for this clip."))
    if not b.allow_sfx and f.sfx:
        out.append(Check("sfx", "No sound effects", "fail", "This campaign doesn't allow sound effects. Turn them off for this clip."))

    # Text: English / banned words / required tags -----------------------------------
    on_screen = " ".join(x for x in (f.hook_text, f.caption_text, f.end_text, f.disclosure_on_screen) if x)
    if b.english_only:
        problems = []
        worst = "pass"
        for name, txt in [("On-screen text", on_screen), *[(f"{LABELS.get(p, p)} caption", c) for p, c in f.posts.items()]]:
            r = english_issues(txt)
            if r:
                problems.append(f"{name}: {r[1]}")
                worst = "fail" if r[0] == "fail" or worst == "fail" else "warn"
        out.append(Check("english", "English-only text", worst, "; ".join(problems) or "All text looks English"))
    if b.banned_keywords:
        hay = " ".join([f.transcript, f.title, on_screen, *f.posts.values()])
        hits = _keyword_hits(b.banned_keywords, hay)
        out.append(Check("banned", "Banned topics", "fail" if hits else "pass", ("Mentions: " + ", ".join(hits[:8])) if hits else f"None of {len(b.banned_keywords)} banned terms found"))
    req = (b.required_caption_text or "").strip()
    if req:
        bad = [LABELS.get(p, p) for p, cap in f.posts.items() if req not in cap]
        out.append(Check("required_text", "Required caption text", "fail" if bad else "pass", ("Missing or changed in: " + ", ".join(bad)) if bad else "Included exactly as written"))
    for p, cap in f.posts.items():
        missing = [t for t in b.required_hashtags if t.lower() not in cap.lower()] + [m for m in b.required_mentions if m.lower() not in cap.lower()]
        if b.required_hashtags or b.required_mentions:
            out.append(Check(f"tags_{p}", f"Required tags · {LABELS.get(p, p)}", "fail" if missing else "pass", ("Missing: " + " ".join(missing)) if missing else "All required hashtags and mentions included"))

    # Originality -------------------------------------------------------------------
    if f.duplicates_other:
        out.append(Check("duplicate", "Not used in another campaign", "fail", "This footage was already exported for: " + ", ".join(f.duplicates_other[:5]) + ". Campaigns don't allow combining or reusing clips."))
    elif f.duplicates_same:
        out.append(Check("duplicate", "Unique clip", "warn", f"Overlaps {f.duplicates_same} clip(s) already exported for this campaign (duplicate content is usually rejected)."))
    else:
        out.append(Check("duplicate", "Unique clip", "pass"))

    if f.burned_text_detected and not f.burned_text_blurred:
        out.append(Check("burned", "Source captions", "warn", "The source has burned-in captions that may clash with yours. Turn on ‘Blur burned-in captions’."))

    for item in b.checklist:
        out.append(Check("manual", item, "manual"))
    return out


def summarise(checks: list[Check]) -> dict:
    st = [c.status for c in checks]
    verdict = "fail" if "fail" in st else "warn" if "warn" in st else "pass"
    return {
        "verdict": verdict,
        "fails": st.count("fail"),
        "warnings": st.count("warn"),
        "manual": st.count("manual"),
        "checks": [c.as_dict() for c in checks],
    }


def empty_mask() -> np.ndarray:
    return np.zeros((GRID_H, GRID_W), dtype=bool)
