"""Second opinion from any AI chat (Claude, ChatGPT, …) without an API key.

1. ``build_prompt`` produces a self-contained prompt: our selection rubric + the timestamped
   transcript + the exact JSON format to answer in. The user pastes it into a chat.
2. ``parse_picks`` reads the chat's answer back (tolerant of code fences, extra prose, mm:ss
   times) and returns validated clip ranges. ClipStudio then snaps them to word boundaries and
   scores them with its own criteria, so AI picks get the same explanations as automatic ones.
Nothing is sent anywhere by ClipStudio itself.
"""

from __future__ import annotations

import json
import re

from ..transcription.types import Transcript


def _ts(t: float) -> str:
    m, s = divmod(max(0.0, t), 60)
    return f"{int(m)}:{s:04.1f}"


def build_prompt(t: Transcript, *, min_dur: float, max_dur: float, max_clips: int, title: str = "", campaign_notes: str = "") -> str:
    lines = []
    for s in t.sentences:
        lines.append(f"[{_ts(s.start)}] {s.text}")
    transcript = "\n".join(lines)
    notes = f"\nCampaign brief / what the brand wants:\n{campaign_notes.strip()}\n" if campaign_notes.strip() else ""
    return f"""You are an expert short-form video editor. Pick the best moments from this video for Instagram Reels and YouTube Shorts.

Video: {title or "(untitled)"}
{notes}
Rules for each clip:
- {min_dur:.0f}–{max_dur:.0f} seconds long.
- Starts on a sentence a brand-new viewer can follow (no "and so", "that's why", "he said" without context).
- Opens with a strong hook in the first 3 seconds (a bold claim, question, surprising number or conflict).
- Ends on the payoff (the answer, punchline or lesson), not mid-thought.
- Stands alone, with no outside context needed. Clips must not overlap.
- Score each 0–100 on hook (25), engagement (25), value (25) and shareability (25).
- Pick at most {max_clips} clips, best first. Fewer is fine if the video doesn't have more great moments.

Answer with ONLY this JSON (times as m:ss.s from the transcript timestamps):
{{"clips": [{{"start": "1:28.0", "end": "2:01.5", "title": "3–9 word hook title", "hook": "the opening line or an on-screen hook", "hashtags": ["#tag1", "#tag2", "#tag3"], "score": 88, "reason": "why this works"}}]}}

Transcript (each line starts at the given time):
{transcript}
"""


def parse_time(v) -> float | None:
    if isinstance(v, (int, float)):
        return float(v)
    if not isinstance(v, str):
        return None
    v = v.strip()
    m = re.fullmatch(r"(?:(\d+):)?(\d{1,2}):(\d{1,2}(?:\.\d+)?)", v)
    if m:
        h = int(m.group(1) or 0)
        return h * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    try:
        return float(v.rstrip("s"))
    except ValueError:
        return None


def parse_picks(text: str, duration: float) -> tuple[list[dict], list[str]]:
    """Returns (picks, problems). Each pick: start, end, title, hook, hashtags, score, reason."""
    problems: list[str] = []
    raw = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", raw, re.S)
    if fence:
        raw = fence.group(1)
    start = raw.find("{")
    if start < 0:
        start = raw.find("[")
    if start < 0:
        return [], ["No JSON found in the pasted answer."]
    data = None
    for end in range(len(raw), start, -1):
        if raw[end - 1] not in "}]":
            continue
        try:
            data = json.loads(raw[start:end])
            break
        except json.JSONDecodeError:
            continue
    if data is None:
        return [], ["Couldn't read the JSON. Paste the whole answer from the chat."]
    items = data.get("clips", []) if isinstance(data, dict) else data
    picks: list[dict] = []
    for k, it in enumerate(items if isinstance(items, list) else []):
        if not isinstance(it, dict):
            continue
        s, e = parse_time(it.get("start")), parse_time(it.get("end"))
        if s is None or e is None:
            problems.append(f"Clip {k + 1}: missing or unreadable start/end time.")
            continue
        if not (0 <= s < e <= duration + 1):
            problems.append(f"Clip {k + 1}: {s:.1f}–{e:.1f}s is outside the video (0–{duration:.0f}s).")
            continue
        if e - s < 3 or e - s > 180:
            problems.append(f"Clip {k + 1}: {e - s:.0f}s long; clips must be 3 s to 3 min.")
            continue
        tags = it.get("hashtags") or []
        picks.append(
            {
                "start": s,
                "end": min(e, duration),
                "title": str(it.get("title") or "")[:200],
                "hook": str(it.get("hook") or "")[:300],
                "hashtags": [str(x) for x in tags][:10] if isinstance(tags, list) else [],
                "score": float(it["score"]) if isinstance(it.get("score"), (int, float)) else None,
                "reason": str(it.get("reason") or "")[:500],
            }
        )
    if not picks and not problems:
        problems.append("The answer didn't contain any clips.")
    return picks[:30], problems
