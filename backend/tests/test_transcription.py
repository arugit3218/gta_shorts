import shutil
import subprocess

import pytest

from clipstudio.transcription.parakeet_worker import merge_tokens
from clipstudio.transcription.service import TranscriptionService, model_cached

MODEL = "mlx-community/parakeet-tdt-0.6b-v2"


class Tok:
    def __init__(self, text, s, e, c=1.0):
        self.text, self.start, self.end, self.confidence = text, s, e, c


def test_merge_tokens_words_and_numbers():
    toks = [Tok(" H", 0, 0.1), Tok("ere", 0.1, 0.2), Tok(" I", 0.3, 0.4), Tok(" was", 0.4, 0.5), Tok("2", 0.5, 0.6), Tok("2", 0.6, 0.7), Tok(" $", 0.8, 0.9), Tok("3", 0.9, 1.0), Tok(",", 1.0, 1.0), Tok("000", 1.0, 1.2)]
    words = merge_tokens(toks)
    assert [w["w"] for w in words] == ["Here", "I", "was", "22", "$3,000"]
    assert words[0]["s"] == 0 and words[0]["e"] == pytest.approx(0.2)


@pytest.mark.slow
@pytest.mark.skipif(not model_cached(MODEL) or not shutil.which("say"), reason="needs cached Parakeet model and macOS say")
def test_real_transcription_word_timestamps(tmp_path):
    aiff, wav = tmp_path / "a.aiff", tmp_path / "a.wav"
    subprocess.run(["say", "-o", str(aiff), "Stop buying coffee every day. It adds up to two thousand dollars a year."], check=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(aiff), "-ar", "16000", "-ac", "1", str(wav)], check=True)
    svc = TranscriptionService(tmp_path)
    t = svc.transcribe(wav, fingerprint="x", model_id=MODEL, duration=6.0)
    text = " ".join(w.w for w in t.words).lower()
    assert "coffee" in text and "year" in text
    assert all(w.e > w.s for w in t.words)
    assert all(a.s <= b.s for a, b in zip(t.words, t.words[1:]))
    # second call is served from cache (no subprocess)
    assert svc.cached("x", MODEL) is not None
