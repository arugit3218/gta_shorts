"""Unit tests for Phase 1 campaign features (no network, synthetic data only)."""

import numpy as np

from clipstudio.analysis.ai_picks import build_prompt, parse_picks, parse_time
from clipstudio.analysis.highlights import HeatSignal, score_range, snap_to_words
from clipstudio.analysis.audio_features import AudioProfile
from clipstudio.campaigns.compliance import ClipFacts, check_clip, english_issues, summarise
from clipstudio.campaigns.models import Brief, merge_brief
from clipstudio.campaigns.parser import parse_brief
from clipstudio.campaigns.post import build_post
from clipstudio.campaigns.presets import PRESETS, preset_brief
from clipstudio.campaigns.safezones import GRID_H, GRID_W, overlap_fraction, rect_mask, safe_box
from clipstudio.media.sfx import Cue, add_cut_whooshes, plan_cues, synth
from clipstudio.media.captions import CapWord

MUSIC_BRIEF = """STRESSATA – Pucho (Music campaign)
$1.20 per 1,000 views, max payout $600
Use the official sound at 75% volume or higher for the entire video.
Videos must be longer than 10 seconds. Minimum 3% engagement.
Post on Instagram Reels and YouTube Shorts. English only on-screen text.
Song: STRESSATA - Pucho
- Comment the song name on your post and like your own comment
- Do not post low quality or spam content
- No cricket
Tag @stressata #stressata #pucho
Keep posts live for 30 days.
"""

LOGO_BRIEF = """CS2 Halloween Logo Campaign
Place the LOGO_PRE_PLACED template (1080x1920) on your clip. Do not move or resize it.
Logo must be in the top half. Minimum 14 seconds. 0.3% engagement.
At most 50% of the logo can be covered by UI and no more than 25% overlap with the no-go safe zone.
"""


def test_parse_music_brief():
    patch, found = parse_brief(MUSIC_BRIEF)
    assert patch["type"] == "music"
    assert patch["cpm"] == 1.2 and patch["max_payout"] == 600
    assert patch["sound_rule"] == {"min_volume": 0.75, "mode": "in_app"}
    assert patch["min_duration"] == 10.5  # "longer than 10 seconds"
    assert patch["min_engagement"] == 3
    assert patch["english_only"] is True
    assert set(patch["platforms"]) == {"reels", "shorts"}
    assert "#stressata" in patch["required_hashtags"] and "@stressata" in patch["required_mentions"]
    assert patch["keep_live_days"] == 30
    assert any("comment the song name" in c.lower() for c in patch["checklist"])
    assert "cricket" in patch["banned_keywords"]
    assert patch["sounds_named"][0].startswith("STRESSATA")
    assert found


def test_parse_logo_brief():
    patch, _ = parse_brief(LOGO_BRIEF)
    assert patch["type"] == "logo"
    assert patch["logo"]["mode"] == "template"
    assert patch["logo"]["zone"] == "top_half"
    assert patch["logo"]["max_ui_cover"] == 0.5 and patch["logo"]["max_nogo_overlap"] == 0.25
    assert patch["min_duration"] == 14


def test_presets_are_valid_and_platform_neutral():
    assert "generic" in PRESETS and "clipster_music" in PRESETS
    b = preset_brief("clipster_music")
    assert b.sound_rule.min_volume == 0.75 and not b.allow_music and b.english_only
    assert preset_brief("nope").preset == "generic"


def test_merge_brief_protects_files():
    b = Brief(logo={"mode": "free", "file": "/secret/logo.png"}, sounds=[{"id": "a", "name": "x", "file": "/s.mp3"}])
    m = merge_brief(b, {"logo": {"file": "/etc/passwd", "zone": "top_half"}, "sounds": [{"id": "a", "name": "renamed", "file": "/etc/passwd"}, {"id": "zz", "file": "/x"}]})
    assert m.logo.file == "/secret/logo.png" and m.logo.zone == "top_half"
    assert len(m.sounds) == 1 and m.sounds[0].file == "/s.mp3" and m.sounds[0].name == "renamed"


def test_post_builder_limits_and_tokens():
    brief = Brief(required_hashtags=["camp", "#two"], required_mentions=["brand"], disclosure={"text": "#ad", "in_caption": True}).model_dump()
    clip = {"title": "T" * 120, "hook": "Big hook", "hashtags": ["#a", "#b", "#c", "#d", "#e"]}
    reels = build_post(brief, clip, "reels")
    assert reels["caption"].startswith("Big hook")
    assert "#camp" in reels["caption"] and "@brand" in reels["caption"] and "#ad" in reels["caption"]
    assert len(reels["hashtags"]) <= 5
    shorts = build_post(brief, clip, "shorts")
    assert len(shorts["title"]) <= 100 and shorts["warnings"]


def test_safe_zones():
    top = rect_mask((0.3, 0.0, 0.7, 0.05))
    assert overlap_fraction(top, ["reels"], "ui") == 1.0
    mid = rect_mask((0.3, 0.4, 0.6, 0.5))
    assert overlap_fraction(mid, ["reels", "shorts"], "nogo") == 0.0
    x0, y0, x1, y1 = safe_box(["reels", "shorts"])
    assert 0.09 <= y0 < 0.2 and 0.7 < y1 <= 0.8 and x1 < 0.9


def _facts(**kw):
    base = dict(duration=20.0, rendered=True, stale=False, width=1080, height=1920, posts={"reels": "hello #camp", "shorts": "hello #camp"})
    return ClipFacts(**(base | kw))


def test_compliance_template_logo_and_sound_rules():
    b = preset_brief("clipster_logo")
    b = b.model_copy(update={"logo": b.logo.model_copy(update={"file": "/tmp/x.gif"}), "platforms": ["reels"]})
    bad_mask = np.zeros((GRID_H, GRID_W), bool)
    bad_mask[180:192, 10:90] = True  # sits on the caption/username area
    r = summarise(check_clip(b, _facts(width=720, height=1280, logo_enabled=True, logo_mask=bad_mask)))
    ids = {c["id"]: c["status"] for c in r["checks"]}
    assert ids["format"] == "fail"  # template campaigns need 1080x1920
    assert ids["logo_safe_reels"] == "fail"
    good = np.zeros((GRID_H, GRID_W), bool)
    good[40:60, 20:80] = True
    r2 = summarise(check_clip(b, _facts(logo_enabled=True, logo_mask=good)))
    assert {c["id"]: c["status"] for c in r2["checks"]}["logo_safe_reels"] == "pass"

    m = preset_brief("clipster_music")
    r3 = summarise(check_clip(m, _facts(duration=9.0, music=True, sfx=True)))
    s3 = {c["id"]: c["status"] for c in r3["checks"]}
    assert s3["length"] == "fail" and s3["music"] == "fail" and s3["sfx"] == "fail" and s3["sound_in_app"] == "manual"
    assert r3["verdict"] == "fail"


def test_compliance_text_rules_and_duplicates():
    b = Brief(english_only=True, banned_keywords=["cricket"], required_hashtags=["#camp"])
    r = summarise(check_clip(b, _facts(transcript="we played cricket", caption_text="यह हिंदी है", posts={"reels": "no tags"}, duplicates_other=["Other campaign"])))
    s = {c["id"]: c["status"] for c in r["checks"]}
    assert s["english"] == "fail" and s["banned"] == "fail" and s["tags_reels"] == "fail" and s["duplicate"] == "fail"
    assert english_issues("This is a perfectly normal English sentence for the clip 🔥") is None


def test_render_state_checks():
    s = {c.id: c.status for c in check_clip(None, _facts(rendered=False))}
    assert s["render"] == "fail"
    s = {c.id: c.status for c in check_clip(None, _facts(stale=True))}
    assert s["render"] == "fail"


def test_sfx_synth_and_plan():
    for k in ("pop", "whoosh", "hit", "riser", "ding"):
        x = synth(k)
        assert x.dtype == np.float32 and 0.5 < np.abs(x).max() <= 0.9
    words = [CapWord(w, i * 0.5, i * 0.5 + 0.4) for i, w in enumerate("so here is the thing I made 500 dollars! but actually it was luck and then the real lesson came".split())]
    cues = plan_cues(words, 20.0, hook_overlay=True, sentence_starts=[0.0, 6.0, 15.0])
    types = [c.type for c in cues]
    assert types[0] == "pop" and "ding" in types and "riser" in types
    assert len(cues) <= 4
    anchors = sorted(c.t + (1.5 if c.type == "riser" else 0) for c in cues)
    assert all(b - a >= 2.5 - 1e-6 for a, b in zip(anchors, anchors[1:]))
    more = add_cut_whooshes([Cue("pop", 0.05)], [5.0, 5.5, 12.0], 20.0)
    assert [c.type for c in more].count("whoosh") == 2


def test_ai_picks_parsing(transcript_factory):
    assert parse_time("1:28.5") == 88.5 and parse_time("01:02:03") == 3723 and parse_time(12) == 12
    answer = 'Sure! Here you go:\n```json\n{"clips": [{"start": "0:01.0", "end": "0:20", "title": "A", "score": 90, "hashtags": ["#x"]}, {"start": "5:00", "end": "5:30"}, {"start": "0:30", "end": "0:31"}]}\n```'
    picks, problems = parse_picks(answer, 60.0)
    assert len(picks) == 1 and picks[0]["start"] == 1.0 and picks[0]["score"] == 90
    assert len(problems) == 2
    t = transcript_factory(["Here is a hook.", "And here is the payoff of the story."])
    prompt = build_prompt(t, min_dur=15, max_dur=30, max_clips=3, campaign_notes="Brand wants energy")
    assert "[0:00.5] Here is a hook." in prompt and "Brand wants energy" in prompt and '"clips"' in prompt


def test_heat_signal_and_score_range(transcript_factory):
    heat = [{"start": i * 10.0, "end": (i + 1) * 10.0, "value": 0.9 if i == 5 else 0.2} for i in range(20)]
    h = HeatSignal(heat)
    assert h.bonus(50, 60) == 10.0 and h.bonus(0, 10) <= 0
    t = transcript_factory(["Here's the secret nobody tells you.", "Most people get this wrong.", "But the truth is simple and it works."] * 3)
    c = score_range(t, AudioProfile.empty(), 0.0, 12.0, source_height=720, weights={"hook": 0.25, "context": 0.2, "emotion": 0.15, "value": 0.15, "story": 0.1, "visual": 0.1, "audio": 0.05})
    assert c.i1 > c.i0 and c.score.total > 0 and c.text
    s, e = snap_to_words(t.words, 0.9, 5.05, t.duration)
    assert any(abs(w.s - 0.15 - s) < 1e-6 or abs(w.s - s) < 0.2 for w in t.words)
    assert e > s
