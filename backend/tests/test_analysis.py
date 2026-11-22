import numpy as np

from clipstudio.analysis.audio_features import AudioProfile
from clipstudio.analysis.highlights import find_highlights
from clipstudio.analysis.metadata import build_idf, clean_quote, generate
from clipstudio.analysis.scoring import quality_label, sentence_features, sentence_hook_score
from clipstudio.analysis.segmentation import normalise_sentences, topic_boundaries
from clipstudio.config import ScoringWeights
from clipstudio.transcription.types import Sentence, to_srt

MONEY = [
    "Here's the secret nobody tells you about saving money.",
    "Most people think they need a bigger salary to get rich.",
    "But the truth is your daily habits matter far more than your income.",
    "I remember when I was twenty two and I had nothing in the bank.",
    "I was spending eight dollars on coffee every single day without thinking.",
    "That adds up to almost three thousand dollars a year.",
    "So I decided to track every single purchase for one month.",
    "You should try it because it will shock you how much you waste.",
    "Make sure you write everything down, even the small stuff.",
    "And that's why I tell everyone to start with tracking, not budgeting.",
]
GARDEN = [
    "Okay so now let's switch gears and talk about my garden.",
    "Tomatoes need at least six hours of direct sunlight every day.",
    "The soil should drain well and be rich in compost and organic matter.",
    "Water the tomato plants deeply twice a week rather than a little every day.",
    "Mulch around the stems keeps the soil moist and stops weeds from growing.",
    "Pruning the suckers helps the plant put energy into the fruit.",
    "Basil grows really well next to tomatoes in the same garden bed.",
    "By late summer the garden should be full of ripe red tomatoes.",
    "Pick them in the morning when the fruit is cool and firm.",
    "Store tomatoes on the counter, never in the fridge, for the best flavour.",
]
FILLER = ["um so yeah anyway like I said we were just kind of hanging out you know."] * 6

W = ScoringWeights().normalised()


def run(t, **kw):
    params = dict(source_height=1080, weights=W, min_dur=15, max_dur=60, max_clips=5, min_score=40)
    params.update(kw)
    return find_highlights(t, AudioProfile.empty(), **params)


def test_hook_score_prefers_strong_openers(transcript_factory):
    t = transcript_factory(["Here's the secret nobody tells you about money.", "and um then it was like whatever."])
    strong, weak = (sentence_features(s, t.words) for s in t.sentences)
    assert sentence_hook_score(strong) > sentence_hook_score(weak) + 25


def test_clips_respect_duration_and_sentence_boundaries(transcript_factory):
    t = transcript_factory(MONEY + GARDEN + FILLER)
    clips, stats = run(t)
    assert clips, stats
    starts = {s.start for s in t.sentences}
    for c in clips:
        assert 15 <= c.duration <= 60
        assert any(abs(c.start - (s - 0.15)) < 0.2 for s in starts), "clip must start at a sentence"
        assert t.words[c.w1 - 1].e <= c.end


def test_best_clip_is_the_hook_driven_money_story(transcript_factory):
    t = transcript_factory(FILLER + MONEY + GARDEN)
    clips, _ = run(t)
    best = clips[0]
    assert "secret" in best.text.lower() or "truth" in best.text.lower()
    assert best.score.scores["hook"] >= 60
    assert best.score.reasons


def test_no_overlapping_or_duplicate_clips(transcript_factory):
    t = transcript_factory(MONEY + GARDEN + MONEY)
    clips, _ = run(t, max_clips=10)
    for i, a in enumerate(clips):
        for b in clips[i + 1:]:
            inter = max(0, min(a.end, b.end) - max(a.start, b.start))
            assert inter <= 0.15 * min(a.duration, b.duration) + 1e-6
    texts = [c.text for c in clips]
    assert len(texts) == len(set(texts))


def test_does_not_force_clips_from_filler(transcript_factory):
    t = transcript_factory(FILLER * 4)
    clips, stats = run(t, min_score=55, max_clips=10)
    assert len(clips) <= 1
    assert "reason" in stats or len(clips) == stats["target"]


def test_topic_boundary_detected_between_subjects(transcript_factory):
    t = transcript_factory(MONEY + GARDEN)
    tb = topic_boundaries(normalise_sentences(t), block=4)
    assert any(abs(b - len(MONEY)) <= 2 for b in tb.boundaries), tb.boundaries


def test_long_sentence_is_split(transcript_factory):
    t = transcript_factory([" ".join(["word"] * 80) + "."])
    out = normalise_sentences(t)
    assert len(out) >= 2
    assert out[0].i0 == 0 and out[-1].i1 == 80
    assert all(a.i1 == b.i0 for a, b in zip(out, out[1:]))


def test_metadata_is_extractive(transcript_factory):
    t = transcript_factory(MONEY)
    meta = generate(t.sentences, t.words, "Advice", build_idf(t.sentences))
    assert len(meta["hooks"]) == 3
    source = " ".join(MONEY).lower()
    for h in meta["hooks"]:
        core = h.rstrip("…").rstrip(".").lower()
        assert core[:25] in source, h
    assert meta["title"] and len(meta["title"]) <= 70
    assert all(h.startswith("#") for h in meta["hashtags"])


def test_clean_quote_strips_fillers():
    assert clean_quote("and um so the truth is simple.") == "The truth is simple."


def test_quality_labels():
    assert quality_label(90) == "Excellent candidate"
    assert quality_label(72) == "Strong candidate"
    assert quality_label(60) == "Needs review"
    assert quality_label(10) == "Low priority"


def test_srt_format(transcript_factory):
    srt = to_srt(transcript_factory(["Hello there world."]))
    assert srt.startswith("1\n00:00:00,500 --> ")
    assert "Hello there world." in srt
