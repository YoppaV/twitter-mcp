"""Pure HTTP download helper — streams media bytes off Twitter/X's CDNs.

No Playwright here. The endpoints layer (``endpoints/media.py``) resolves which
media *URLs* belong to a tweet/article; this module turns those URLs into bytes.
``httpx`` is streamed so an oversized response can be aborted mid-flight without
buffering the whole thing.

Framework-free apart from the ``httpx.AsyncClient`` passed in by the caller —
which keeps it trivially unit-testable with a fake client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING
from urllib.parse import urlparse

if TYPE_CHECKING:
    import httpx

# Safety net for images. Images on Twitter are small (a few MB at most), so
# this rarely trips — it just guards against a pathological response. Videos
# opted in via ``download_videos`` pass ``max_bytes=None`` (no cap by design).
IMAGE_MAX_BYTES = 25 * 1024 * 1024

# Twitter/X media CDN hosts. Article-DOM extraction can surface arbitrary
# ``<img>`` srcs (tracking pixels, avatars, ``blob:`` previews); restricting
# downloads to these hosts keeps the tool from fetching unrelated URLs.
_ALLOWED_HOSTS = frozenset({"pbs.twimg.com", "video.twimg.com", "pbs.x.com"})

# ``reason`` values surfaced on a failed FetchOutcome / a SkippedMedia entry.
REASON_TOO_LARGE = "too_large"
REASON_HTTP_404 = "http_404"
REASON_DOWNLOAD_ERROR = "download_error"

_CONTENT_TYPE_FORMATS: dict[str, str] = {
    "image/jpeg": "jpeg",
    "image/jpg": "jpeg",
    "image/png": "png",
    "image/webp": "webp",
    "image/gif": "gif",
    "video/mp4": "mp4",
}


@dataclass(frozen=True)
class FetchOutcome:
    """Result of a single ``fetch_bytes`` call.

    ``ok`` True ⇒ ``data`` holds the bytes. ``ok`` False ⇒ ``reason`` explains
    why (``http_404`` | ``too_large`` | ``download_error``).
    """

    ok: bool
    data: bytes | None
    content_type: str
    reason: str | None


async def fetch_bytes(
    client: "httpx.AsyncClient",
    url: str,
    *,
    max_bytes: int | None,
) -> FetchOutcome:
    """Stream ``url`` into memory, aborting if it exceeds ``max_bytes``.

    ``max_bytes=None`` disables the cap (used for opted-in video downloads).
    A 404 is distinguished from other failures so callers can report a
    deleted/missing asset distinctly from a transient network error.
    """
    try:
        async with client.stream("GET", url) as response:
            status = response.status_code
            if status == 404:
                return FetchOutcome(False, None, "", REASON_HTTP_404)
            if status >= 400:
                return FetchOutcome(False, None, "", REASON_DOWNLOAD_ERROR)

            content_type = response.headers.get("content-type", "") or ""
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if max_bytes is not None and total > max_bytes:
                    return FetchOutcome(False, None, content_type, REASON_TOO_LARGE)
                chunks.append(chunk)
            return FetchOutcome(True, b"".join(chunks), content_type, None)
    except Exception:
        # Connection reset, DNS failure, timeout, malformed response — all
        # collapse to a single best-effort error; the tool keeps going.
        return FetchOutcome(False, None, "", REASON_DOWNLOAD_ERROR)


def content_type_to_format(content_type: str, fallback_ext: str) -> str:
    """Map a Content-Type header (or a fallback file extension) to a format.

    Returns one of ``jpeg`` / ``png`` / ``webp`` / ``gif`` / ``mp4``. The
    format string feeds ``mcp.server.fastmcp.Image(format=...)``; use
    ``format_to_extension`` to turn it into a filename suffix.
    """
    ct = (content_type or "").split(";")[0].strip().lower()
    if ct in _CONTENT_TYPE_FORMATS:
        return _CONTENT_TYPE_FORMATS[ct]

    ext = (fallback_ext or "").lower().lstrip(".")
    if ext in ("jpg", "jpeg"):
        return "jpeg"
    if ext in ("png", "webp", "gif", "mp4"):
        return ext
    return "jpeg"


def format_to_extension(fmt: str) -> str:
    """Turn a format string into a filename extension (``jpeg`` → ``jpg``)."""
    return "jpg" if fmt == "jpeg" else fmt


def is_downloadable_media_url(url: str) -> bool:
    """True only for ``http(s)`` URLs on a known Twitter/X media CDN host.

    Rejects ``blob:`` / ``data:`` URIs and any non-CDN host — a safety filter
    for the best-effort article-DOM media extraction, which can pick up
    arbitrary element ``src`` attributes.
    """
    if not url:
        return False
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in ("http", "https"):
        return False
    return parsed.hostname in _ALLOWED_HOSTS
