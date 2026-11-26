"""URL ingestion via yt-dlp (public, downloadable sources only).

- Only http(s) URLs to public hosts are accepted (no localhost / private network targets).
- No cookies, no credentials, no DRM circumvention: if a site needs login, we report it.
- Downloads are capped to a maximum height to save disk, and de-duplicated by normalised URL.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import re
import shutil
import socket
from pathlib import Path
from typing import Callable
from urllib.parse import urlparse, urlunparse

from ..cancel import NEVER, CancelToken
from ..errors import Cancelled, InvalidInput, MediaError

log = logging.getLogger(__name__)


def validate_url(url: str) -> str:
    url = (url or "").strip()
    if len(url) > 2048:
        raise InvalidInput("URL is too long")
    p = urlparse(url)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise InvalidInput("Enter a full http(s) video URL")
    host = p.hostname
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror as e:
        raise InvalidInput(f"Can't resolve host '{host}'") from e
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise InvalidInput("URLs pointing to local or private network addresses are not allowed")
    return urlunparse(p._replace(fragment=""))


def url_key(url: str) -> str:
    return hashlib.sha1(url.encode()).hexdigest()[:16]


def js_runtimes() -> dict:
    """JavaScript runtimes yt-dlp may use for YouTube's player challenges (needed since late 2025).
    Uses whichever of deno / node / bun is installed; the challenge solver scripts come from the
    bundled yt-dlp-ejs package (nothing is fetched remotely)."""
    found = {}
    for name in ("deno", "node", "bun"):
        path = shutil.which(name)
        if path:
            found[name] = {"path": path}
    return found


def _opts(max_height: int, outtmpl: str | None = None) -> dict:
    fmt = (
        f"bv*[height<={max_height}][ext=mp4]+ba[ext=m4a]/b[height<={max_height}][ext=mp4]/"
        f"bv*[height<={max_height}]+ba/b[height<={max_height}]/b"
    )
    o = {
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "socket_timeout": 30,
        "retries": 3,
        "cachedir": False,
        "restrictfilenames": True,
        "overwrites": False,
        "continuedl": True,
    }
    rt = js_runtimes()
    if rt:
        o["js_runtimes"] = rt
    if outtmpl:
        o["outtmpl"] = outtmpl
    return o


def _friendly(e: Exception) -> MediaError:
    msg = str(e)
    low = msg.lower()
    if "sign in" in low or "login" in low or "private" in low or "members-only" in low:
        return MediaError("This video requires login or is private. Only public videos can be downloaded.")
    if "drm" in low:
        return MediaError("This video is DRM-protected and can't be downloaded.")
    if "unsupported url" in low:
        return MediaError("This site isn't supported for downloading. Download the video yourself (if permitted) and upload the file.")
    if "http error 404" in low:
        return MediaError("Video not found (404).")
    return MediaError("Couldn't access this video: " + msg.replace("ERROR: ", "")[:300])


def inspect(url: str, max_height: int = 1080) -> dict:
    """Fetch metadata without downloading."""
    from yt_dlp import YoutubeDL

    url = validate_url(url)
    try:
        with YoutubeDL(_opts(max_height) | {"skip_download": True}) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # yt-dlp raises DownloadError / ExtractorError
        raise _friendly(e) from e
    if info.get("_type") == "playlist":
        raise InvalidInput("Playlists aren't supported; paste a single video URL.")
    if info.get("is_live"):
        raise InvalidInput("Live streams aren't supported.")
    size = 0
    for f in info.get("requested_formats") or [info]:
        size += int(f.get("filesize") or f.get("filesize_approx") or 0)
    return {
        "url": url,
        "title": info.get("title") or "Untitled",
        "duration": float(info.get("duration") or 0),
        "width": info.get("width"),
        "height": info.get("height"),
        "thumbnail": info.get("thumbnail"),
        "uploader": info.get("uploader"),
        "extractor": info.get("extractor_key"),
        "estimated_bytes": size or None,
        "fps": info.get("fps"),
        "heatmap": [
            {"start": float(h["start_time"]), "end": float(h["end_time"]), "value": float(h["value"])}
            for h in (info.get("heatmap") or [])
            if h.get("start_time") is not None and h.get("end_time") is not None and h.get("value") is not None
        ],
        "chapters": [
            {"start": float(c.get("start_time") or 0), "end": float(c.get("end_time") or 0), "title": str(c.get("title") or "")[:200]}
            for c in (info.get("chapters") or [])
        ],
        "creator_subtitles": sorted(k for k in (info.get("subtitles") or {}) if k.lower().startswith("en") and k != "live_chat"),
    }


def download(
    url: str,
    dest_dir: Path,
    *,
    max_height: int = 1080,
    on_progress: Callable[[float], None] | None = None,
    cancel: CancelToken = NEVER,
) -> Path:
    from yt_dlp import YoutubeDL

    url = validate_url(url)
    key = url_key(url)
    existing = sorted(p for p in dest_dir.glob(f"{key}.*") if p.suffix in (".mp4", ".mkv", ".webm", ".mov") and ".part" not in p.name)
    if existing:
        log.info("Reusing previous download for %s", url)
        return existing[0]

    def hook(d: dict) -> None:
        if cancel.cancelled:
            raise Cancelled()
        if d.get("status") == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(0.99, d.get("downloaded_bytes", 0) / total))

    opts = _opts(max_height, str(dest_dir / f"{key}.%(ext)s")) | {"progress_hooks": [hook]}
    try:
        with YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
            path = Path(ydl.prepare_filename(info))
    except Cancelled:
        for p in dest_dir.glob(f"{key}.*"):
            p.unlink(missing_ok=True)
        raise
    except Exception as e:
        if cancel.cancelled:
            raise Cancelled() from e
        raise _friendly(e) from e
    if path.suffix != ".mp4" and path.with_suffix(".mp4").exists():
        path = path.with_suffix(".mp4")
    if not path.exists():
        cands = [p for p in dest_dir.glob(f"{key}.*") if ".part" not in p.name]
        if not cands:
            raise MediaError("Download finished but the file wasn't found")
        path = cands[0]
    return path


def _progress_hook(cancel: CancelToken, on_progress: Callable[[float], None] | None):
    def hook(d: dict) -> None:
        if cancel.cancelled:
            raise Cancelled()
        if d.get("status") == "downloading" and on_progress:
            total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
            if total:
                on_progress(min(0.99, d.get("downloaded_bytes", 0) / total))

    return hook


def _run_ydl(url: str, opts: dict, key_glob: tuple[Path, str], cancel: CancelToken) -> dict:
    from yt_dlp import YoutubeDL

    try:
        with YoutubeDL(opts) as ydl:
            return ydl.extract_info(url, download=True)
    except Cancelled:
        d, pat = key_glob
        for p in d.glob(pat):
            p.unlink(missing_ok=True)
        raise
    except Exception as e:
        if cancel.cancelled:
            raise Cancelled() from e
        raise _friendly(e) from e


def download_audio(url: str, dest_dir: Path, *, on_progress: Callable[[float], None] | None = None, cancel: CancelToken = NEVER) -> Path:
    """Audio-only download (~1 MB per minute): enough to transcribe and pick clips before
    fetching any video."""
    url = validate_url(url)
    key = url_key(url) + ".audio"
    existing = sorted(p for p in dest_dir.glob(f"{key}.*") if ".part" not in p.name and p.suffix in (".m4a", ".webm", ".mp3", ".opus", ".ogg", ".mp4"))
    if existing:
        return existing[0]
    opts = _opts(1080, str(dest_dir / f"{key}.%(ext)s")) | {"format": "ba[ext=m4a]/ba/b", "progress_hooks": [_progress_hook(cancel, on_progress)]}
    opts.pop("merge_output_format", None)
    _run_ydl(url, opts, (dest_dir, f"{key}.*"), cancel)
    cands = [p for p in dest_dir.glob(f"{key}.*") if ".part" not in p.name]
    if not cands:
        raise MediaError("Audio download finished but the file wasn't found")
    return cands[0]


def download_section(url: str, dest: Path, start: float, end: float, *, max_height: int = 1080, cancel: CancelToken = NEVER) -> Path:
    """Download only [start, end] seconds of the video (yt-dlp hands the range to FFmpeg)."""
    from yt_dlp.utils import download_range_func

    url = validate_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    stem = dest.with_suffix("")
    opts = _opts(max_height, str(stem) + ".%(ext)s") | {
        "download_ranges": download_range_func(None, [(max(0.0, start), end)]),
        "progress_hooks": [_progress_hook(cancel, None)],
    }
    _run_ydl(url, opts, (dest.parent, stem.name + ".*"), cancel)
    cands = [p for p in dest.parent.glob(stem.name + ".*") if ".part" not in p.name and p.suffix in (".mp4", ".mkv", ".webm", ".mov")]
    if not cands:
        raise MediaError("Section download finished but the file wasn't found")
    if cands[0] != dest:
        cands[0].replace(dest)
    return dest


def download_proxy(url: str, dest: Path, *, cancel: CancelToken = NEVER) -> Path:
    """A tiny low-resolution, video-only copy (~20–40 MB per hour) used only to measure motion."""
    url = validate_url(url)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        return dest
    stem = dest.with_suffix("")
    opts = _opts(240, str(stem) + ".%(ext)s") | {"format": "bv*[height<=240][height>=120]/wv*/bv*[height<=360]/w", "progress_hooks": [_progress_hook(cancel, None)]}
    opts.pop("merge_output_format", None)
    _run_ydl(url, opts, (dest.parent, stem.name + ".*"), cancel)
    cands = [p for p in dest.parent.glob(stem.name + ".*") if ".part" not in p.name and p.suffix in (".mp4", ".webm", ".mkv", ".mov")]
    if not cands:
        raise MediaError("Preview download finished but the file wasn't found")
    if cands[0] != dest:
        cands[0].replace(dest)
    return dest


def download_subtitles(url: str, dest_dir: Path, lang: str) -> Path | None:
    """The uploader's own subtitles (not auto-generated) as WebVTT, or None."""
    url = validate_url(url)
    key = url_key(url)
    opts = _opts(1080, str(dest_dir / f"{key}.%(ext)s")) | {
        "skip_download": True,
        "writesubtitles": True,
        "writeautomaticsub": False,
        "subtitleslangs": [lang],
        "subtitlesformat": "vtt",
    }
    _run_ydl(url, opts, (dest_dir, f"{key}.*.vtt"), NEVER)
    cands = sorted(dest_dir.glob(f"{key}*.vtt"))
    return cands[0] if cands else None


def _vtt_time(s: str) -> float:
    parts = s.strip().replace(",", ".").split(":")
    parts = [float(p) for p in parts]
    while len(parts) < 3:
        parts.insert(0, 0.0)
    return parts[0] * 3600 + parts[1] * 60 + parts[2]


def parse_vtt(text: str) -> list[tuple[float, float, str]]:
    """Cues as (start, end, text), tags stripped and rolling duplicates removed."""
    cues: list[tuple[float, float, str]] = []
    blocks = re.split(r"\n\s*\n", text.replace("\r", ""))
    for b in blocks:
        lines = [ln for ln in b.split("\n") if ln.strip()]
        for i, ln in enumerate(lines):
            if "-->" in ln:
                a, _, rest = ln.partition("-->")
                try:
                    s, e = _vtt_time(a), _vtt_time(rest.split()[0])
                except (ValueError, IndexError):
                    break
                body = " ".join(lines[i + 1 :])
                body = re.sub(r"<[^>]+>", "", body)
                body = re.sub(r"\s+", " ", body).strip()
                if body and e > s:
                    if cues and body == cues[-1][2]:
                        cues[-1] = (cues[-1][0], e, body)
                    else:
                        cues.append((s, e, body))
                break
    return cues
