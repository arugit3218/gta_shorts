"""URL ingestion via yt-dlp (public, downloadable sources only).

- Only http(s) URLs to public hosts are accepted (no localhost / private network targets).
- No cookies, no credentials, no DRM circumvention: if a site needs login, we report it.
- Downloads are capped to a maximum height to save disk, and de-duplicated by normalised URL.
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
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
