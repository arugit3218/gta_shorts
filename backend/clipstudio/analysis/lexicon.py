"""Phrase lexicons for deterministic transcript scoring.

All matching is done on lowercased text with punctuation removed (apostrophes kept).
Lists are deliberately conservative; they are heuristics, not a classifier.
"""

from __future__ import annotations

import re

STOPWORDS = frozenset(
    """a about above after again against all am an and any are aren't as at be because been before being below between
    both but by can can't cannot could couldn't did didn't do does doesn't doing don't down during each few for from further
    had hadn't has hasn't have haven't having he he'd he'll he's her here here's hers herself him himself his how how's i i'd
    i'll i'm i've if in into is isn't it it's its itself let's me more most mustn't my myself no nor not of off on once only or
    other ought our ours ourselves out over own same shan't she she'd she'll she's should shouldn't so some such than that
    that's the their theirs them themselves then there there's these they they'd they'll they're they've this those through
    to too under until up very was wasn't we we'd we'll we're we've were weren't what what's when when's where where's which
    while who who's whom why why's with won't would wouldn't you you'd you'll you're you've your yours yourself yourselves
    just like really yeah okay ok um uh know get got go going thing things lot kind sort actually basically gonna wanna
    right well also even much many way say said mean one two dont im youre thats theres""".split()
)

FILLER_WORDS = frozenset({"um", "uh", "uhm", "erm", "hmm", "mm", "ah"})
FILLER_PHRASES = ("you know", "i mean", "sort of", "kind of", "like i said")

# Openers that signal the clip starts mid-thought.
DANGLING_STARTS = frozenset({"and", "but", "because", "so", "which", "also", "or", "plus", "then", "although", "though", "cause"})
ANAPHORIC_STARTS = frozenset({"that", "this", "it", "he", "she", "they", "those", "these", "him", "her", "them", "there"})
WEAK_ENDINGS = frozenset({"and", "but", "so", "because", "or", "the", "a", "to", "of", "with", "like", "um", "uh"})

HOOK_PHRASES = (
    "the truth is", "the truth about", "nobody tells you", "no one tells you", "nobody talks about", "the secret",
    "here's the thing", "here's why", "here's what", "here's how", "the biggest mistake", "biggest mistake",
    "the real reason", "the reason why", "most people", "everyone thinks", "people don't realize", "you won't believe",
    "i was wrong", "changed my life", "changed everything", "the problem is", "the worst", "the best", "never", "stop",
    "what if", "imagine", "the number one", "the one thing", "the only", "you need to", "this is why", "here is the",
    "let me tell you", "unpopular opinion", "hot take", "honestly", "i'll be honest", "the craziest", "the scariest",
    "nobody believed", "it turns out", "turns out", "what happened", "i quit", "i lost", "i made", "million", "billion",
)
QUESTION_STARTS = ("why ", "what ", "how ", "who ", "when ", "where ", "have you", "did you", "do you", "would you", "can you", "is it", "are you", "should you", "ever wonder")

PAYOFF_PHRASES = (
    "that's why", "so the lesson", "the lesson", "the point is", "in the end", "turns out", "it turned out", "which means",
    "the answer is", "the key is", "and that's", "bottom line", "at the end of the day", "so remember", "the takeaway",
    "and that is why", "that's how", "that's what", "so if you", "and it worked", "that changed", "from then on", "ever since",
)

EMOTION_WORDS = frozenset(
    """love loved hate hated scared afraid terrified fear angry furious mad sad cried crying tears heartbroken happy
    excited thrilled shocked shocking insane crazy amazing incredible unbelievable terrible horrible awful beautiful
    devastating devastated embarrassed embarrassing proud ashamed hurt pain painful brutal wild ridiculous hilarious
    funny laugh laughing laughed died dead dying panic panicked nervous anxious anxiety depressed depression lonely
    obsessed disgusting disgusted stunned grateful wow omg epic legendary nightmare disaster miracle""".split()
)
INTENSIFIERS = frozenset("absolutely literally completely totally extremely seriously insanely incredibly worst best always never everything nothing everyone nobody".split())
LAUGHTER = ("haha", "hahaha", "lol", "[laughter]", "(laughs)", "laughs")

CONTROVERSY_PHRASES = (
    "unpopular opinion", "hot take", "overrated", "underrated", "i disagree", "that's wrong", "you're wrong", "is a scam",
    "is a lie", "the truth is", "nobody wants to hear", "controversial", "i don't care what", "people hate", "everyone is wrong",
    "stop doing", "is dead", "waste of", "myth", "honestly",
)

VALUE_PHRASES = (
    "you should", "you need to", "you have to", "you must", "how to", "the key", "the trick", "tip", "tips", "step",
    "first you", "here's how", "make sure", "if you want", "the best way", "mistake", "mistakes", "avoid", "rule",
    "strategy", "framework", "the secret", "focus on", "start with", "try to", "the goal", "what you do is", "instead of",
    "the easiest way", "the fastest way", "hack", "lesson", "advice", "recommend", "don't ever", "always",
)
IMPERATIVE_VERBS = frozenset(
    "try stop start do don't make use focus remember think build write save invest learn read ask take keep avoid quit "
    "practice imagine look listen go get find spend cut".split()
)
EDUCATION_PHRASES = (
    "because", "which means", "the reason", "for example", "for instance", "in other words", "what happens is",
    "the way it works", "this is called", "it's called", "the difference", "research", "study", "studies", "percent",
    "data", "science", "explains", "means that", "defined as", "basically",
)

STORY_PHRASES = (
    "i remember", "when i was", "one day", "years ago", "back then", "at the time", "my first", "true story",
    "what happened", "it happened", "turned out", "suddenly", "finally", "that's when", "the next day", "a few years",
    "i was", "we were", "i had", "i went", "i got", "i said", "i told", "i thought", "i realized", "i decided",
    "she said", "he said", "they said", "long story short", "fast forward", "the moment",
)

META_PHRASES = (
    "subscribe", "like and subscribe", "smash that", "link in the description", "link below", "sponsored by", "sponsor",
    "welcome back", "welcome to the", "patreon", "before we get started", "let me know in the comments",
    "see you next time", "thanks for watching", "promo code", "use code",
)

CATEGORY_HASHTAGS = {
    "Advice": ["advice", "tips"],
    "Story": ["storytime", "story"],
    "Opinion": ["opinion", "debate"],
    "Education": ["learn", "explained"],
    "Humour": ["funny", "comedy"],
    "Emotional": ["motivation", "real"],
    "Highlight": ["highlights", "viral"],
}

_PUNCT = re.compile(r"[^\w\s'$%]")
_NUM = re.compile(r"\b\d[\d,.]*\b|\$\d|\d+%|\b(?:hundred|thousand|million|billion|percent)\b")


def norm(text: str) -> str:
    return " " + _PUNCT.sub(" ", text.lower()).replace("  ", " ").strip() + " "


def count_phrases(normed: str, phrases) -> int:
    return sum(normed.count(" " + p + " ") for p in phrases)


def has_number(text: str) -> bool:
    return bool(_NUM.search(text.lower()))


def tokens(text: str) -> list[str]:
    return [t for t in _PUNCT.sub(" ", text.lower()).split() if t]


def content_tokens(text: str) -> list[str]:
    return [_stem(t) for t in tokens(text) if t not in STOPWORDS and len(t) > 2 and not t.isdigit()]


def _stem(t: str) -> str:
    for suf in ("ings", "ing", "ies", "ed", "es", "s"):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[: -len(suf)] + ("y" if suf == "ies" else "")
    return t
