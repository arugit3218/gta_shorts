"""Parakeet-MLX transcription, run as a separate process.

Running the model in a child process guarantees that all model memory (weights + Metal buffers)
is returned to the OS when transcription ends, which matters on an 8 GB machine.

Protocol: prints JSON lines to stdout: {"progress": 0..1} ... then {"done": true}. Writes result to OUT.
Usage: python -m clipstudio.transcription.parakeet_worker MODEL WAV OUT CHUNK_SECONDS
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def _emit(obj: dict) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def merge_tokens(tokens) -> list[dict]:
    """Parakeet emits sub-word tokens; a leading space starts a new word."""
    words: list[dict] = []
    for t in tokens:
        text = t.text
        if not text:
            continue
        piece = text.strip()
        if not piece:
            continue
        starts_word = text.startswith(" ") or not words
        # Parakeet often emits numbers/currency without a leading space ("was" + "22").
        if not starts_word and piece[0] in "$£€0123456789" and words[-1]["w"][-1:].isalpha():
            starts_word = True
        if starts_word:
            words.append({"w": piece, "s": float(t.start), "e": float(t.end), "c": [float(t.confidence)]})
        else:
            words[-1]["w"] += piece
            words[-1]["e"] = float(t.end)
            words[-1]["c"].append(float(t.confidence))
    for w in words:
        w["c"] = round(min(w["c"]), 3)
        w["s"], w["e"] = round(w["s"], 3), round(max(w["e"], w["s"] + 0.02), 3)
    return words


def main() -> None:
    model_id, wav, out, chunk = sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]), float(sys.argv[4])
    from parakeet_mlx import from_pretrained

    _emit({"stage": "loading_model"})
    model = from_pretrained(model_id)
    _emit({"stage": "transcribing"})

    def cb(done: int, total: int) -> None:
        _emit({"progress": round(done / max(total, 1), 4)})

    result = model.transcribe(wav, chunk_duration=chunk, overlap_duration=15.0, chunk_callback=cb)

    words: list[dict] = []
    sentences: list[dict] = []
    for s in result.sentences:
        ws = merge_tokens(s.tokens)
        if not ws:
            continue
        i0 = len(words)
        words.extend(ws)
        sentences.append({"start": ws[0]["s"], "end": ws[-1]["e"], "text": " ".join(w["w"] for w in ws), "i0": i0, "i1": len(words)})

    tmp = out.with_suffix(".part")
    tmp.write_text(json.dumps({"model": model_id, "language": "en", "words": words, "sentences": sentences}))
    os.replace(tmp, out)
    _emit({"done": True, "words": len(words)})


if __name__ == "__main__":
    main()
