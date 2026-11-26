"""Paste-to-fill: turn a pasted campaign brief (free text from any site) into brief fields.

Purely rule-based and conservative: it only fills a field when the text says so clearly, and it
reports what it found so the UI can show "filled from the brief" and let the user correct it.
"""

from __future__ import annotations

import re

NUM = r"(\d+(?:[.,]\d+)?)"


def _num(s: str) -> float:
    return float(s.replace(",", ""))


def _views(s: str) -> int:
    s = s.lower().replace(",", "").strip()
    mult = 1
    if s.endswith("k"):
        mult, s = 1000, s[:-1]
    elif s.endswith("m"):
        mult, s = 1_000_000, s[:-1]
    return int(float(s) * mult)


def _seconds(val: str, unit: str) -> float:
    v = _num(val)
    return v * 60 if unit.lower().startswith("m") else v


TYPE_HINTS = [
    ("music", r"\b(music|song|sound|audio campaign|track|artist|drop)\b"),
    ("logo", r"\blogo\b"),
    ("ugc", r"\bugc\b|\buser[- ]generated\b|\birl creators?\b|\bon[- ]camera\b"),
]

ZONES = [
    ("top_half", r"top half|upper half"),
    ("bottom_half", r"bottom half|lower half"),
    ("top_left", r"top[- ]left"),
    ("top_right", r"top[- ]right"),
    ("bottom_left", r"bottom[- ]left"),
    ("bottom_right", r"bottom[- ]right"),
    ("top", r"\bat the top\b|\btop of the (video|screen|frame)\b"),
    ("bottom", r"\bat the bottom\b|\bbottom of the (video|screen|frame)\b"),
    ("middle", r"\b(centre|center|middle) of the (video|screen|frame)\b"),
]

RULE_START = re.compile(r"^\s*(?:[-•*✅❌⛔🚫]\s*)?(do not|don't|dont|no |never|avoid|banned|not allowed|prohibited|must not|must|only|required|requirement|make sure|ensure)\b", re.I)


REQUIRED_TEXT = re.compile(
    r"(?:exactly as written|word for word|verbatim|paste (?:this|the following)[^\n]{0,40}caption|(?:must|should) (?:include|contain|add)[^\n]{0,40}(?:text|line|disclaimer)[^\n]{0,30}caption|caption must (?:include|contain|end with))",
    re.I,
)
QUOTED = re.compile(r"[\"“”']([^\"“”]{8,1400})[\"“”']")


def _required_text(t: str) -> tuple[str, int] | None:
    """Text the brief says to put in every caption exactly as written (usually quoted on the same or next line)."""
    m = REQUIRED_TEXT.search(t)
    if not m:
        return None
    after = t[m.end() : m.end() + 1600]
    q = QUOTED.search(after)
    if q and after[: q.start()].count("\n") <= 2:
        return q.group(1).strip(), m.start()
    # unquoted: the next non-empty line
    for line in after.splitlines()[1:4]:
        line = line.strip(" \t↓:-•")
        if len(line) >= 8:
            return line, m.start()
    return None


def parse_brief(text: str) -> tuple[dict, list[str]]:
    """Returns (patch for Brief, list of human-readable 'found' notes).

    ``patch["_sources"]`` maps a topic (length, logo, disclosure, …) to the brief line it came from, so the
    UI can show where each requirement was read. Callers pop it before validating the patch."""
    t = text or ""
    low = t.lower()
    patch: dict = {"raw_brief": t[:20000]}
    found: list[str] = []
    src: dict[str, str] = {}
    patch["_sources"] = src
    same = len(low) == len(t)

    def note(key: str, m: re.Match | None) -> None:
        if m is None or not same or key in src:
            return
        a = t.rfind("\n", 0, m.start()) + 1
        b = t.find("\n", m.start())
        src[key] = t[a : b if b >= 0 else len(t)].strip(" \t-•*")[:200]

    # name: first non-empty short line
    for line in t.splitlines():
        line = line.strip(" \t#*-•")
        if not line:
            continue
        # "Logo Campaign — $1.50 per 1,000 views": keep the part before the dash / colon / first sentence
        line = re.split(r"(?<=[.!?])\s|\s[—–-]\s|:\s|\s\|\s", line, maxsplit=1)[0].strip(" .")[:90]
        if 3 <= len(line) <= 90:
            patch["name"] = line
            found.append(f"Name: {line}")
        break

    typ_text = re.sub(r"\b(?:no|without|not allowed:?) (?:background |extra |added |other )?(?:music|sounds?|sound effects)\b", " ", low)
    for typ, rx in TYPE_HINTS:
        if re.search(rx, typ_text):
            patch["type"] = typ
            found.append(f"Type: {typ}")
            break

    if "clipster" in low:
        patch["source"] = "Clipster"
        found.append("From Clipster: its general rules are added")

    # pay: "$1 per 1,000 views", "$1.50/1k", "CPM $2", "$2 CPM"
    m = re.search(r"\$\s?" + NUM + r"\s*(?:per|/|for every|each)\s*(?:1[,.]?000|1k|thousand|k)\b", low) or re.search(r"cpm[^\d$]{0,10}\$?\s?" + NUM, low) or re.search(r"\$\s?" + NUM + r"\s*cpm", low)
    if m:
        patch["cpm"] = _num(m.group(1))
        note("cpm", m)
        found.append(f"Pay: ${patch['cpm']:g} per 1,000 views")
    m = re.search(r"(?:max(?:imum)?|up to|cap(?:ped)?(?: at)?)\s*(?:payout|earnings|of)?\s*\$\s?" + NUM, low)
    if m:
        patch["max_payout"] = _num(m.group(1))
        note("max_payout", m)
        found.append(f"Max payout: ${patch['max_payout']:g}")

    # views minimum
    m = re.search(r"(?:min(?:imum)?|at least)\s*(?:of\s*)?(\d[\d,.]*\s*[km]?)\s*views", low) or re.search(r"(\d[\d,.]*\s*[km]?)\s*views?\s*(?:min(?:imum)?|required)", low)
    if m:
        try:
            patch["min_views"] = _views(m.group(1))
            note("min_views", m)
            found.append(f"Minimum views: {patch['min_views']:,}")
        except ValueError:
            pass

    # engagement
    m = re.search(NUM + r"\s*%\s*(?:\+\s*)?(?:engagement|eng\.? rate|er\b)", low) or re.search(r"engagement[^\d]{0,25}" + NUM + r"\s*%", low)
    if m:
        patch["min_engagement"] = _num(m.group(1))
        note("min_engagement", m)
        found.append(f"Minimum engagement: {patch['min_engagement']:g}%")

    # durations
    unit = r"(s|sec|secs|seconds|m|min|mins|minutes)\b"
    rng = re.search(NUM + r"\s*(?:s|sec|secs|seconds)?\s*(?:-|–|—|to)\s*" + NUM + r"\s*" + unit, low)
    if rng and re.search(r"(video|clip|length|long|duration|seconds|sec)", low[max(0, rng.start() - 40) : rng.end() + 20]):
        a, b2 = _seconds(rng.group(1), rng.group(3)), _seconds(rng.group(2), rng.group(3))
        if 1 <= a < b2 <= 600:
            patch["min_duration"], patch["max_duration"] = a, b2
            found.append(f"Length: {a:g}–{b2:g}s")
            note("length", rng)
    m = (
        re.search(r"(?:min(?:imum)?|at least|longer than|more than|over)\s*(?:length|duration)?\s*(?:of|:)?\s*" + NUM + r"\s*" + unit, low)
        or re.search(NUM + r"\s*" + unit + r"\s*(?:min(?:imum)?|or (?:longer|more))", low)
    )
    if m and "min_duration" not in patch:
        v = _seconds(m.group(1), m.group(2))
        if "longer than" in m.group(0) or "more than" in m.group(0) or "over" in m.group(0):
            v += 0.5
        patch["min_duration"] = v
        found.append(f"Minimum length: {v:g}s")
        note("length", m)
    m = re.search(r"(?:max(?:imum)?|at most|shorter than|under|no longer than|up to)\s*(?:length|duration)?\s*(?:of|:)?\s*" + NUM + r"\s*" + unit, low)
    if m and "max_duration" not in patch:
        v = _seconds(m.group(1), m.group(2))
        if v >= 3:
            patch["max_duration"] = v
            found.append(f"Maximum length: {v:g}s")
            note("length", m)

    # platforms
    plats = []
    if re.search(r"instagram|reels?\b|\big\b", low):
        plats.append("reels")
    if re.search(r"youtube|shorts?\b|\byt\b", low):
        plats.append("shorts")
    if plats:
        note("platforms", re.search(r"instagram|reels?\b|youtube|shorts?\b", low))
        patch["platforms"] = plats
        found.append("Platforms: " + ", ".join("Instagram Reels" if p == "reels" else "YouTube Shorts" for p in plats))
    if "tiktok" in low and not plats:
        found.append("Mentions TikTok (not supported for export presets; Reels/Shorts settings still apply)")

    # sound / volume
    m = re.search(NUM + r"\s*%\s*(?:volume|vol\b|sound|audio)", low) or re.search(r"(?:volume|vol\.?)[^\d]{0,20}" + NUM + r"\s*%", low)
    if m:
        v = _num(m.group(1)) / 100
        patch.setdefault("sound_rule", {})["min_volume"] = min(1.0, v)
        note("sound", m)
        found.append(f"Sound volume ≥ {v * 100:.0f}%")
    if re.search(r"official sound|use (?:the|this|our) (?:official )?(?:sound|song|audio)|(?:sound|song) (?:from|in) the (?:in-app|app)", low) or (patch.get("type") == "music" and re.search(r"\b(song|track|sound)\b", typ_text)):
        patch.setdefault("sound_rule", {})["mode"] = "in_app"
        patch["allow_music"] = False
        note("sound", re.search(r"official sound|use (?:the|this) (?:sound|song|audio)|\b(music|song|sound)\b", low))
        found.append("Use the official sound (added in the app when posting)")
    songs = re.findall(r"(?:song|sound|track|audio)\s*(?:name)?\s*[:\-–]\s*([^\n]{2,80})", t, re.I)
    if songs:
        patch["sounds_named"] = [s.strip() for s in songs[:4]]
        found.append("Sound: " + "; ".join(patch["sounds_named"]))

    # extra music / sound effects not allowed
    m = re.search(r"\bno (?:background |extra |added |other )?music\b|without (?:background )?music|music (?:is )?not allowed|don'?t add (?:any )?music", low)
    if m:
        patch["allow_music"] = False
        note("music", m)
        found.append("No background music")
    m = re.search(r"\bno (?:sound effects|sfx)\b|sound effects (?:are )?not allowed|without sound effects", low)
    if m:
        patch["allow_sfx"] = False
        note("sfx", m)
        found.append("No sound effects")

    # logo placement
    if patch.get("type") == "logo" or "logo" in low:
        logo: dict = {}
        note("logo", re.search(r"(?:place|put|add|use|overlay)[^.\n]{0,40}\blogo|\blogo\b[^.\n]{0,40}(?:template|place|top|bottom|corner|zone|visible)", low) or re.search(r"\blogo\b", low))
        if re.search(r"pre[_ -]?placed|template|1080\s*[x×]\s*1920|don'?t (?:move|resize)|do not (?:move|resize)", low):
            logo["mode"] = "template"
            found.append("Logo: pre-placed template (don't move or resize)")
        else:
            logo["mode"] = "free"
        for zone, rx in ZONES:
            if re.search(rx, low):
                logo["zone"] = zone
                logo["y"] = 0.22 if "top" in zone else 0.7 if "bottom" in zone else 0.5
                found.append(f"Logo zone: {zone.replace('_', ' ')}")
                break
        m = re.search(NUM + r"\s*%[^.\n]{0,40}(?:ui|interface|covered)", low)
        if m:
            logo["max_ui_cover"] = _num(m.group(1)) / 100
        m = re.search(NUM + r"\s*%[^.\n]{0,40}(?:no[- ]go|safe[- ]zone|overlap)", low)
        if m:
            logo["max_nogo_overlap"] = _num(m.group(1)) / 100
        patch["logo"] = logo

    # disclosure
    m = re.search(r"(#ad\b|#sponsored\b|#partner\b|paid partnership)", low)
    if m:
        patch["disclosure"] = {"text": m.group(1) if m.group(1).startswith("#") else "Paid partnership", "in_caption": True}
        note("disclosure", m)
        found.append(f"Disclosure: {patch['disclosure']['text']}")

    if re.search(r"english[- ]only|only english|in english|english (?:text|captions|on[- ]screen)", low):
        patch["english_only"] = True
        note("english", re.search(r"english", low))
        found.append("English-only text")

    m = re.search(r"(?:live|up) for (?:at least )?(\d{1,3})\s*days", low) or re.search(r"(\d{1,3})\s*days? (?:live|minimum)", low)
    if m:
        patch["keep_live_days"] = int(m.group(1))
        note("keep_live", m)
        found.append(f"Keep posts live {m.group(1)} days")

    req = _required_text(t)
    if req:
        patch["required_caption_text"] = req[0]
        if same:
            a = t.rfind("\n", 0, req[1]) + 1
            b = t.find("\n", req[1])
            src["required_text"] = t[a : b if b >= 0 else len(t)].strip(" \t-•*")[:200]
        found.append("Required caption text: " + (req[0][:60] + ("…" if len(req[0]) > 60 else "")))
    req_low = req[0].lower() if req else ""

    tags = list(dict.fromkeys(re.findall(r"(?<![\w&])#([A-Za-z]\w{1,59})", t)))
    tags = [x for x in tags if x.lower() not in ("ad", "sponsored", "partner") and f"#{x.lower()}" not in req_low]
    if tags:
        patch["required_hashtags"] = [f"#{x}" for x in tags[:15]]
        found.append("Hashtags: " + " ".join(patch["required_hashtags"]))
    mentions = list(dict.fromkeys(re.findall(r"(?<![\w.])@([A-Za-z0-9_.]{2,30})", t)))
    if mentions:
        patch["required_mentions"] = [f"@{x.rstrip('.')}" for x in mentions[:10]]
        found.append("Mentions: " + " ".join(patch["required_mentions"]))

    rules: list[str] = []
    checklist: list[str] = []
    banned: list[str] = []
    for raw in t.splitlines():
        line = raw.strip()
        if not line or len(line) > 300:
            continue
        if RULE_START.search(line):
            rules.append(line.lstrip("-•*✅❌⛔🚫 ").strip())
            mm = re.match(r"^\s*(?:[-•*❌⛔🚫]\s*)?(?:no|do not post|don't post|banned:?)\s+([a-z0-9 /&'-]{3,40})$", line, re.I)
            if mm:
                banned.extend(x.strip() for x in re.split(r"[,/]| or | and ", mm.group(1)) if len(x.strip()) >= 3)
        if re.search(r"\bcomment\b.*\b(song|sound|name)\b|\blike (?:your|the) (?:own )?comment\b", line, re.I):
            checklist.append(line.lstrip("-•*✅ ").strip())
    if rules:
        patch["rules"] = rules[:40]
        found.append(f"{len(rules)} rule line(s)")
    if checklist:
        patch["checklist"] = checklist
        found.append("Post actions: " + "; ".join(checklist)[:120])
    if banned:
        patch["banned_keywords"] = list(dict.fromkeys(b.lower() for b in banned))[:30]

    return patch, found
