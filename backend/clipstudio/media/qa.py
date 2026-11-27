"""Quality check of a finished clip, before it's posted: one FFmpeg pass over the rendered file, no models.

Adapted from nero-harsh/clipstudio (``media/qa.py``). It measures what platforms and viewers notice:
- sound: integrated loudness near -14 LUFS (what Reels / Shorts normalise to) and true peak at or below
  -1 dBTP after encoding (AAC can push peaks past the limiter, so the encoded file is measured);
- frozen picture: 2 s or more where nothing changes;
- black picture: half a second or more;
- flashing: three or more full-frame light/dark flashes inside one second (a photosensitivity risk).

Each finding is ``fail`` (fix before posting) or ``warn`` (worth a look).
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import time
from pathlib import Path

from . import ffmpeg as ff

TARGET_LUFS = -14.0
LUFS_SLACK = 2.0  # quiet/loud enough to matter on a phone
MAX_TRUE_PEAK = -0.5  # above this, AAC peaks can clip on phone speakers
FLASH_DELTA = 26.0  # mean luma (0–255) change between frames that counts as one light/dark jump
FLASHES_PER_S = 3


def measure(path: Path, timeout: float = 300) -> dict:
    """Loudness, true peak, frozen and black stretches, and the mean brightness of every frame."""
    with tempfile.TemporaryDirectory(prefix="qa_") as d:
        vf = "scale=160:-2,signalstats,metadata=print:key=lavfi.signalstats.YAVG:file=yavg.txt,freezedetect=n=0.003:d=2,blackdetect=d=0.5:pix_th=0.08"
        cmd = [ff.FFMPEG, "-hide_banner", "-nostdin", "-nostats", "-i", str(Path(path).resolve()), "-vf", vf, "-af", "ebur128=peak=true", "-f", "null", "-"]
        err = subprocess.run(cmd, capture_output=True, text=True, cwd=d, timeout=timeout).stderr
        yfile = Path(d) / "yavg.txt"
        ytxt = yfile.read_text() if yfile.exists() else ""
    summary = err[err.rfind("Summary:") :] if "Summary:" in err else ""
    lufs = re.search(r"I:\s+(-?[\d.]+|-inf) LUFS", summary)
    tp = re.search(r"True peak:\s+Peak:\s+(-?[\d.]+|-inf) dBFS", summary)
    times = [float(x) for x in re.findall(r"pts_time:([\d.]+)", ytxt)]
    yavg = [float(x) for x in re.findall(r"YAVG=([\d.]+)", ytxt)]
    freezes = [(float(a), float(b)) for a, b in zip(re.findall(r"freeze_start: ([\d.]+)", err), re.findall(r"freeze_duration: ([\d.]+)", err))]
    blacks = [(float(a), float(b)) for a, b in re.findall(r"black_start:([\d.]+) black_end:[\d.]+ black_duration:([\d.]+)", err)]

    def num(m: re.Match | None) -> float | None:
        return None if not m or m.group(1) == "-inf" else float(m.group(1))

    return {"lufs": num(lufs), "true_peak": num(tp), "freezes": freezes, "blacks": blacks, "luma": list(zip(times, yavg))}


def flash_times(luma: list[tuple[float, float]]) -> list[float]:
    """Start times of seconds holding FLASHES_PER_S or more flashes (a flash = a jump and a jump back)."""
    jumps = [(t, y - yp) for (_, yp), (t, y) in zip(luma, luma[1:]) if abs(y - yp) >= FLASH_DELTA]
    out: list[float] = []
    for i, (t, _) in enumerate(jumps):
        window = [j for j in jumps[i:] if j[0] - t < 1.0]
        reversals = sum(1 for a, b in zip(window, window[1:]) if (a[1] > 0) != (b[1] > 0))
        if (reversals + 1) // 2 >= FLASHES_PER_S and (not out or t - out[-1] >= 1.0):
            out.append(t)
    return out


def _at(t: float) -> str:
    return f"{int(t // 60)}:{t % 60:04.1f}"


def check(path: Path, *, has_audio: bool = True, song: bool = False) -> dict:
    """Findings grouped as sound / picture / flashing, plus the numbers behind them.
    Stored on the clip as ``analysis.qa`` and shown with the clip's checks."""
    m = measure(path)
    issues: list[dict] = []

    def add(group: str, level: str, message: str, t: float | None = None) -> None:
        issues.append({"group": group, "level": level, "message": message, **({"t": round(t, 2)} if t is not None else {})})

    if has_audio:
        if m["lufs"] is None or m["lufs"] <= -60:  # no audio, or digital silence (ebur128 reports about -70)
            add("sound", "fail", "The clip is silent.")
        elif song:
            pass  # a mastered song is loud on purpose; the platform evens it out
        elif m["lufs"] < TARGET_LUFS - LUFS_SLACK:
            add("sound", "warn", f"Quiet: {m['lufs']:.1f} LUFS (platforms play about {TARGET_LUFS:.0f}). Viewers may scroll past.")
        elif m["lufs"] > TARGET_LUFS + LUFS_SLACK:
            add("sound", "warn", f"Loud: {m['lufs']:.1f} LUFS (platforms play about {TARGET_LUFS:.0f}); Instagram / YouTube will turn it down.")
        if not song and m["true_peak"] is not None and m["true_peak"] > MAX_TRUE_PEAK:
            add("sound", "warn", f"Peaks reach {m['true_peak']:.1f} dBTP; above {MAX_TRUE_PEAK:.0f} can crackle on phone speakers.")
    for t, d in m["freezes"]:
        add("picture", "warn", f"The picture doesn't change for {d:.1f}s at {_at(t)}.", t)
    for t, d in m["blacks"]:
        add("picture", "fail" if t < 0.1 else "warn", f"{d:.1f}s of black picture at {_at(t)}{' (the very start: the first frame is black)' if t < 0.1 else ''}.", t)
    for t in flash_times(m["luma"]):
        add("flashing", "warn", f"Rapid bright flashes at {_at(t)} (3 or more in a second). This can trigger photosensitive viewers.", t)
    return {
        "ok": not any(i["level"] == "fail" for i in issues),
        "audio_checked": has_audio,
        "issues": issues,
        "lufs": m["lufs"],
        "true_peak": m["true_peak"],
        "at": time.time(),
    }
