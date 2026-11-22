"""Disk-space checks, storage estimates, usage reporting and cleanup."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import InsufficientStorage

GB = 1024**3
MB = 1024**2


def free_bytes(path: Path) -> int:
    p = path
    while not p.exists():
        p = p.parent
    return shutil.disk_usage(p).free


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for f in path.rglob("*"):
        try:
            if f.is_file() and not f.is_symlink():
                total += f.stat().st_size
        except OSError:
            pass
    return total


@dataclass
class Estimate:
    source_bytes: int  # new bytes needed to store the source (0 if referenced in place)
    temp_bytes: int  # peak temporary bytes (audio wav, partial renders)
    output_bytes: int  # rendered clips + thumbnails

    @property
    def total(self) -> int:
        return self.source_bytes + self.temp_bytes + self.output_bytes

    def as_dict(self) -> dict:
        return {
            "source_mb": round(self.source_bytes / MB),
            "temp_mb": round(self.temp_bytes / MB),
            "output_mb": round(self.output_bytes / MB),
            "total_mb": round(self.total / MB),
        }


def estimate_processing(duration_s: float, *, source_bytes: int = 0, clips: int = 12, clip_seconds: float = 45, output_height: int = 720) -> Estimate:
    wav = int(duration_s * 16000 * 2) + MB  # 16 kHz mono s16
    bitrate = 3.5e6 if output_height <= 720 else 7e6  # bits/s incl. audio
    out = int(clips * clip_seconds * bitrate / 8)
    return Estimate(source_bytes=source_bytes, temp_bytes=wav + int(clip_seconds * bitrate / 8), output_bytes=out)


def ensure_space(path: Path, needed: int, min_free: int, what: str) -> None:
    free = free_bytes(path)
    if free - needed < min_free:
        raise InsufficientStorage(
            f"Not enough disk space for {what}: needs ~{needed / MB:,.0f} MB, "
            f"{free / MB:,.0f} MB free, and {min_free / GB:.1f} GB must stay free. "
            "Free some space, run Storage → Clean up, or choose a working directory on an external drive."
        )


def fingerprint(path: Path) -> str:
    """Cheap, stable content fingerprint: size + hash of first/last 4 MB."""
    st = path.stat()
    h = hashlib.sha1(str(st.st_size).encode())
    with path.open("rb") as f:
        h.update(f.read(4 * MB))
        if st.st_size > 8 * MB:
            f.seek(-4 * MB, 2)
            h.update(f.read(4 * MB))
    return h.hexdigest()[:20]
