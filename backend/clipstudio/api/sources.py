"""Validation for user-provided local files."""

from __future__ import annotations

import re
from pathlib import Path

from ..errors import InvalidInput

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".m4v", ".webm", ".avi", ".mts", ".m2ts", ".ts", ".flv", ".wmv", ".mpg", ".mpeg", ".3gp"}
AUDIO_EXTS = {".mp3", ".m4a", ".aac", ".wav", ".flac", ".ogg"}


def allowed_roots() -> list[Path]:
    return [Path.home().resolve(), Path("/Volumes").resolve()]


def validate_local_video(raw: str) -> Path:
    """Accept only existing video files under the user's home folder or mounted volumes.

    Prevents using the API to probe arbitrary system files; the file is never served back
    by path (only by project id).
    """
    if not raw or "\x00" in raw:
        raise InvalidInput("Enter a file path")
    p = Path(raw.strip().strip("'\"")).expanduser()
    try:
        p = p.resolve(strict=True)
    except (FileNotFoundError, RuntimeError, OSError) as e:
        raise InvalidInput("File not found") from e
    if not p.is_file():
        raise InvalidInput("Path is not a file")
    if p.suffix.lower() not in VIDEO_EXTS:
        raise InvalidInput(f"Unsupported file type '{p.suffix}'. Supported: {', '.join(sorted(VIDEO_EXTS))}")
    if not any(p.is_relative_to(root) for root in allowed_roots()):
        raise InvalidInput("Only files in your home folder or on external drives (/Volumes) can be imported")
    return p


def safe_filename(name: str, default: str = "video.mp4") -> str:
    base = Path(name or default).name
    stem = re.sub(r"[^A-Za-z0-9._ -]", "_", Path(base).stem)[:80].strip() or "video"
    ext = Path(base).suffix.lower()
    return f"{stem}{ext}"
