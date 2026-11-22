"""Optional local LLM via Ollama (Tier 2). Never required; every caller has a deterministic fallback.

- Only localhost endpoints are allowed (validated in settings).
- ``keep_alive: 0`` makes Ollama unload the model right after each request (8 GB RAM friendliness).
- Outputs are validated for grounding: hooks/titles must not introduce numbers absent from the
  transcript, and must stay short. Invalid fields fall back to extractive text.
"""

from __future__ import annotations

import json
import logging
import re
import urllib.error
import urllib.request

log = logging.getLogger(__name__)

PROMPT = """You are helping edit a short-form video clip. Use ONLY the transcript below.
Rules: do not invent facts, names or numbers; do not change the speaker's meaning; keep hooks under 14 words.
Return compact JSON with keys:
"hooks": 3 alternative opening hook lines (may quote or lightly paraphrase the speaker),
"title": a title under 60 characters,
"description": 1-2 sentence description,
"hashtags": 3-5 lowercase hashtags without '#',
"category": one of ["Advice","Story","Opinion","Education","Humour","Emotional","Highlight"],
"rating": integer 1-10 for how engaging and self-contained the clip is,
"rating_reason": one short sentence.

Transcript:
\"\"\"{text}\"\"\"
"""


def available(url: str, model: str, timeout: float = 2.0) -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{url.rstrip('/')}/api/tags", timeout=timeout) as r:
            tags = json.loads(r.read())
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        return False, f"Ollama not reachable at {url} ({e.__class__.__name__})"
    names = {m.get("name", "") for m in tags.get("models", [])}
    if model not in names and f"{model}:latest" not in names:
        return False, f"Model '{model}' not installed in Ollama (run: ollama pull {model})"
    return True, "ok"


def _numbers(s: str) -> set[str]:
    return set(re.findall(r"\d[\d,.]*", s))


def enrich(url: str, model: str, text: str, timeout: float = 90.0) -> dict | None:
    body = json.dumps(
        {
            "model": model,
            "prompt": PROMPT.format(text=text[:6000]),
            "format": "json",
            "stream": False,
            "keep_alive": 0,
            "options": {"temperature": 0.3, "num_ctx": 4096},
        }
    ).encode()
    req = urllib.request.Request(f"{url.rstrip('/')}/api/generate", data=body, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = json.loads(r.read()).get("response", "")
        data = json.loads(raw)
    except Exception as e:  # network, timeout, invalid JSON
        log.warning("LLM enrich failed: %s", e)
        return None
    return validate(data, text)


def validate(data: dict, transcript: str) -> dict:
    allowed_nums = _numbers(transcript)
    out: dict = {}

    def grounded(s: str) -> bool:
        return isinstance(s, str) and 0 < len(s) <= 160 and _numbers(s) <= allowed_nums

    hooks = [h.strip() for h in data.get("hooks", []) if grounded(h) and len(h.split()) <= 16]
    if hooks:
        out["hooks"] = hooks[:3]
    if grounded(data.get("title", "")) and len(data["title"]) <= 70:
        out["title"] = data["title"].strip().strip('"')
    desc = data.get("description", "")
    if isinstance(desc, str) and 0 < len(desc) <= 300 and _numbers(desc) <= allowed_nums:
        out["description"] = desc.strip()
    tags = [re.sub(r"[^a-z0-9]", "", str(t).lower()) for t in data.get("hashtags", [])]
    tags = [t for t in tags if 2 < len(t) < 30]
    if tags:
        out["hashtags"] = ["#" + t for t in tags[:5]]
    if data.get("category") in {"Advice", "Story", "Opinion", "Education", "Humour", "Emotional", "Highlight"}:
        out["category"] = data["category"]
    try:
        rating = int(data.get("rating"))
        if 1 <= rating <= 10:
            out["rating"] = rating
            out["rating_reason"] = str(data.get("rating_reason", ""))[:200]
    except (TypeError, ValueError):
        pass
    return out
