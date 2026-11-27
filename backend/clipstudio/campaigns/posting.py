"""Where a clip has been posted ("Mark as posted"): one row per clip and platform, with an optional link.

A clip counts as *posted* once it is marked on every platform its campaign posts to (both Reels and
Shorts without a campaign). Until then it stays in the campaign's post queue and on Home's to-do list.
"""

from __future__ import annotations

import time
from typing import Iterable

from ..db import Database
from .models import Brief

ALL_PLATFORMS = ("reels", "shorts")


def platforms_of(brief: Brief | dict | None) -> list[str]:
    """Platforms a campaign posts to, in a fixed order (both when none or no campaign)."""
    if isinstance(brief, dict):
        brief = Brief.model_validate(brief)
    chosen = set(brief.platforms) if brief and brief.platforms else set(ALL_PLATFORMS)
    return [p for p in ALL_PLATFORMS if p in chosen] or list(ALL_PLATFORMS)


def posted_map(db: Database, clip_ids: Iterable[str]) -> dict[str, dict[str, dict]]:
    """clip id → {platform: {url, at}} for the given clips (clips never posted are absent)."""
    ids = list(dict.fromkeys(clip_ids))
    out: dict[str, dict[str, dict]] = {}
    for i in range(0, len(ids), 500):  # stay under SQLite's bound-parameter limit
        chunk = ids[i : i + 500]
        qs = ",".join("?" for _ in chunk)
        for r in db.query(f"SELECT * FROM posts WHERE clip_id IN ({qs})", tuple(chunk)):
            entry = {"url": r["url"], "at": r["posted_at"]}
            if r.get("stats_at"):
                entry |= {"views": r.get("views"), "likes": r.get("likes"), "comments": r.get("comments"), "stats_at": r["stats_at"], "stats_error": r.get("stats_error")}
            out.setdefault(r["clip_id"], {})[r["platform"]] = entry
    return out


def is_done(posted: dict[str, dict] | None, platforms: list[str]) -> bool:
    return bool(posted) and all(p in posted for p in platforms)


def posted_ids(db: Database) -> set[str]:
    """Every clip that has been posted somewhere (protected from re-processing)."""
    return {r["clip_id"] for r in db.query("SELECT DISTINCT clip_id FROM posts")}


def done_ids(db: Database) -> set[str]:
    """Clips posted on every platform their campaign asks for."""
    rows = db.query(
        "SELECT po.clip_id, po.platform, cp.brief FROM posts po JOIN clips cl ON cl.id = po.clip_id "
        "JOIN projects p ON p.id = cl.project_id LEFT JOIN campaigns cp ON cp.id = p.campaign_id"
    )
    plats: dict[str, set[str]] = {}
    need: dict[str, list[str]] = {}
    for r in rows:
        plats.setdefault(r["clip_id"], set()).add(r["platform"])
        if r["clip_id"] not in need:
            try:
                need[r["clip_id"]] = platforms_of(r["brief"] or None)
            except Exception:
                need[r["clip_id"]] = list(ALL_PLATFORMS)
    return {cid for cid, have in plats.items() if all(p in have for p in need[cid])}


def mark(db: Database, clip_id: str, platform: str, url: str | None) -> None:
    # a new link means the old numbers belong to another post: clear them
    db.execute(
        "INSERT INTO posts (clip_id, platform, url, posted_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(clip_id, platform) DO UPDATE SET url = excluded.url, "
        "views = CASE WHEN posts.url IS excluded.url THEN posts.views END, likes = CASE WHEN posts.url IS excluded.url THEN posts.likes END, "
        "comments = CASE WHEN posts.url IS excluded.url THEN posts.comments END, stats_at = CASE WHEN posts.url IS excluded.url THEN posts.stats_at END, "
        "stats_error = CASE WHEN posts.url IS excluded.url THEN posts.stats_error END",
        (clip_id, platform, url or None, time.time()),
    )


def refresh_stats(db: Database, rows: list[dict], fetch=None) -> dict:
    """Fetch public numbers for posts with a link. ``rows``: dicts with clip_id, platform, url.
    Keeps the last good numbers when a fetch fails (and records why). Returns counts."""
    from ..media.download import post_stats

    fetch = fetch or post_stats
    ok = failed = 0
    for r in rows:
        if not r.get("url"):
            continue
        now = time.time()
        try:
            st = fetch(r["url"])
            db.execute(
                "UPDATE posts SET views = ?, likes = ?, comments = ?, stats_at = ?, stats_error = NULL WHERE clip_id = ? AND platform = ?",
                (st.get("views"), st.get("likes"), st.get("comments"), now, r["clip_id"], r["platform"]),
            )
            ok += 1
        except Exception as e:  # one bad link never stops the rest
            db.execute("UPDATE posts SET stats_at = ?, stats_error = ? WHERE clip_id = ? AND platform = ?", (now, str(e)[:300], r["clip_id"], r["platform"]))
            failed += 1
    return {"updated": ok, "failed": failed}


def unmark(db: Database, clip_id: str, platform: str) -> None:
    db.execute("DELETE FROM posts WHERE clip_id = ? AND platform = ?", (clip_id, platform))
