"""FastMCP server exposing read-only Twitter/X tools to Claude.

Run with::

    venv/bin/python -m twitter_sdk.server

Configure clients (Claude Code: ``.mcp.json`` at repo root; Claude Desktop:
``claude_desktop_config.json``). Required env var: ``TWITTER_SESSION_FILE``
pointing at the Playwright storage_state JSON written by ``scripts/auth_login``.
"""

from __future__ import annotations

import os
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP, Image

from . import downloader
from .auth import USER_AGENT, SessionExpiredError, session_summary
from .browser import DEFAULT_IDLE_TIMEOUT_S, BrowserSession
from .endpoints import (
    article,
    bookmarks,
    home,
    media,
    search,
    social_graph,
    trends,
    tweet,
    user,
)
from .endpoints._ids import parse_tweet_id_or_url
from .models import DownloadedMedia, MediaDownloadResult, MediaItem, SkippedMedia

load_dotenv()

mcp = FastMCP("twitter-sdk")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SESSION_DIR = _PROJECT_ROOT / "sessions"

# Where ``download_media`` writes fetched media. Treated as temporary scratch
# (gitignored). A future ``TWITTER_DOWNLOADS_DIR`` env var would plug in here —
# this single constant is the only place that would need to change.
_DOWNLOADS_DIR = _PROJECT_ROOT / "downloads"


def _resolve_session_path() -> Path:
    explicit = os.getenv("TWITTER_SESSION_FILE", "").strip()
    if explicit:
        return Path(explicit).expanduser()

    handle = os.getenv("TWITTER_USERNAME", "").strip()
    if handle:
        return _DEFAULT_SESSION_DIR / f"{handle}_twitter_state.json"

    candidates = sorted(_DEFAULT_SESSION_DIR.glob("*_twitter_state.json"))
    if candidates:
        return candidates[0]

    return _DEFAULT_SESSION_DIR / "twitter_state.json"


def _idle_timeout() -> int:
    raw = os.getenv("BROWSER_IDLE_TIMEOUT_S", "").strip()
    if not raw:
        return DEFAULT_IDLE_TIMEOUT_S
    try:
        return max(0, int(raw))
    except ValueError:
        return DEFAULT_IDLE_TIMEOUT_S


def _headless() -> bool:
    raw = os.getenv("BROWSER_HEADLESS", "true").strip().lower()
    return raw not in ("0", "false", "no")


_session_path = _resolve_session_path()
_browser = BrowserSession(
    _session_path,
    idle_timeout_s=_idle_timeout(),
    headless=_headless(),
)

_http_client: httpx.AsyncClient | None = None


def _get_http_client() -> httpx.AsyncClient:
    """Lazily build the shared AsyncClient used for media downloads.

    Created on first use so merely importing this module (e.g. in tests)
    never opens a client. Reused across calls — httpx pools connections.
    """
    global _http_client
    if _http_client is None:
        _http_client = httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            headers={"User-Agent": USER_AGENT},
        )
    return _http_client


def _serialize(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return asdict(value)
    if isinstance(value, list):
        return [_serialize(v) for v in value]
    if isinstance(value, tuple):
        return [_serialize(v) for v in value]
    if isinstance(value, dict):
        return {k: _serialize(v) for k, v in value.items()}
    return value


def _detect_source(id_or_url: str, source: str) -> str:
    """Resolve the ``source`` arg of ``download_media`` to "tweet" or "article".

    ``auto`` (the default) inspects the URL: ``/i/article/`` ⇒ article,
    a ``/status/`` URL or a bare numeric id ⇒ tweet.
    """
    chosen = (source or "auto").strip().lower()
    if chosen in ("tweet", "article"):
        return chosen
    if chosen != "auto":
        raise ValueError(
            f"Invalid source {source!r}. Use 'auto', 'tweet', or 'article'."
        )
    if "/i/article/" in id_or_url.lower():
        return "article"
    return "tweet"


def _select_indices(
    items: tuple[MediaItem, ...],
    indices: list[int] | None,
) -> list[tuple[int, MediaItem]]:
    """Pair each media item with its original 0-based index.

    When ``indices`` is given, keep only those positions. The original index
    is retained so saved filenames stay stable regardless of the filter.
    """
    enumerated = list(enumerate(items))
    if indices is None:
        return enumerated
    wanted = set(indices)
    return [(i, item) for i, item in enumerated if i in wanted]


@mcp.tool()
async def auth_status() -> dict[str, Any]:
    """Report whether a Twitter/X session is loaded and likely valid.

    Returns the session file path, the handle inferred from the filename,
    last-modified timestamp (proxy for last login), and an `authenticated`
    flag based on the presence of `auth_token` + `ct0` cookies in the file.

    This does NOT make a network request — to verify the session actually
    works against x.com, call any other tool. Session-expired errors there
    point back here.
    """
    return session_summary(_session_path)


@mcp.tool()
async def get_bookmarks(
    limit: int = 50,
    since_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read the user's Twitter bookmarks, newest first.

    Args:
        limit: Max number of bookmarks to return (default 50).
        since_id: Stop scrolling once this tweet_id is observed. Useful for
            incremental fetches: pass the most recent tweet_id you've seen.
    """
    async with _browser.page() as page:
        tweets = await bookmarks.fetch(page, limit=limit, since_id=since_id)
    return _serialize(tweets)


@mcp.tool()
async def get_home_timeline(
    limit: int = 50,
    feed: str = "for_you",
    since_id: str | None = None,
) -> list[dict[str, Any]]:
    """Read the user's home timeline.

    Args:
        limit: Max number of tweets to return (default 50).
        feed: "for_you" (algorithmic) or "following" (chronological from
            followed accounts).
        since_id: Stop once this tweet_id is observed.
    """
    async with _browser.page() as page:
        tweets = await home.fetch(
            page, feed=feed, limit=limit, since_id=since_id
        )
    return _serialize(tweets)


@mcp.tool()
async def get_tweet(
    id_or_url: str,
    include_replies: bool = True,
    include_quote: bool = True,
) -> dict[str, Any]:
    """Fetch a single tweet plus its conversation context.

    Args:
        id_or_url: Either the numeric tweet id (e.g. "1488816925400616964")
            or a full https://x.com/<handle>/status/<id> URL.
        include_replies: Include replies under the focal tweet.
        include_quote: Include the quoted tweet in the focal tweet's payload.

    Returns:
        ``{"focal": Tweet|None, "replies": [Tweet, ...]}``
    """
    async with _browser.page() as page:
        focal, replies = await tweet.fetch(
            page,
            id_or_url=id_or_url,
            include_replies=include_replies,
            include_quote=include_quote,
        )
    return {
        "focal": _serialize(focal),
        "replies": _serialize(replies),
    }


@mcp.tool()
async def search_tweets(
    query: str,
    limit: int = 50,
    mode: str = "top",
) -> list[dict[str, Any]]:
    """Run a Twitter search.

    Args:
        query: Twitter search query (supports operators: from:, since:, etc.).
        limit: Max results to return (default 50).
        mode: "top" (relevance) or "latest" (chronological).
    """
    async with _browser.page() as page:
        tweets = await search.fetch(page, query=query, mode=mode, limit=limit)
    return _serialize(tweets)


@mcp.tool()
async def get_user_profile(handle: str) -> dict[str, Any] | None:
    """Fetch a user's public profile (bio, counts, pinned tweet id).

    Args:
        handle: Twitter handle without the leading "@".
    """
    async with _browser.page() as page:
        profile = await user.fetch_profile(page, handle=handle)
    return _serialize(profile)


@mcp.tool()
async def get_x_article(id_or_url: str) -> dict[str, Any] | None:
    """Fetch the full body of a native X long-form article.

    Tweets that embed an X article only expose preview metadata (title + first
    paragraph) via ``tweet.x_article``. This tool navigates to the article's
    own page and returns the rendered body — both clean text (for LLM use)
    and the full HTML (for callers that want links/markup preserved).

    Args:
        id_or_url: The numeric article id (e.g. "1846123456789012345") or a
            full https://x.com/i/article/<id> URL.

    Returns:
        ``{article_id, url, title, body_text, body_html, char_count, media,
        body_markdown}`` or ``None`` if the article failed to render (deleted,
        restricted, or the reader view didn't appear within ~8 seconds).
        ``media`` lists photos/videos scraped from the reader DOM (best-effort,
        may be empty); ``body_markdown`` and ``body_html`` re-render the body
        as Markdown / a standalone HTML document with images inlined. Pass the
        article to ``download_media`` to fetch those bytes and save the files.
    """
    async with _browser.page() as page:
        result = await article.fetch(page, id_or_url=id_or_url)
    return _serialize(result)


@mcp.tool()
async def get_user_tweets(
    handle: str,
    limit: int = 50,
    include_replies: bool = False,
    include_media_only: bool = False,
) -> list[dict[str, Any]]:
    """Fetch tweets from a user's profile timeline.

    Args:
        handle: Twitter handle without the leading "@".
        limit: Max tweets to return.
        include_replies: Switch to the "With Replies" tab.
        include_media_only: Switch to the "Media" tab (overrides include_replies).
    """
    async with _browser.page() as page:
        tweets = await user.fetch_tweets(
            page,
            handle=handle,
            limit=limit,
            include_replies=include_replies,
            include_media_only=include_media_only,
        )
    return _serialize(tweets)


@mcp.tool()
async def get_trends(
    category: str = "trending",
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Read the current Twitter/X trends for one of the explore tabs.

    Args:
        category: One of "trending", "news", "sports", "entertainment",
            "for_you" (the user's personalized tab).
        limit: Max trends to return (default 20).

    Each item carries ``name``, ``query`` (raw search query), ``url``,
    ``post_count`` (0 if not exposed) and ``category`` (domain context).
    """
    async with _browser.page() as page:
        result = await trends.fetch(page, category=category, limit=limit)
    return _serialize(result)


@mcp.tool()
async def get_user_followers(
    handle: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List a user's followers, newest first.

    Returns ``[]`` for protected/blocked accounts — not an error.

    Args:
        handle: Twitter handle without the leading "@".
        limit: Max users to return (default 50).
    """
    async with _browser.page() as page:
        result = await social_graph.fetch_followers(
            page, handle=handle, limit=limit
        )
    return _serialize(result)


@mcp.tool()
async def get_user_following(
    handle: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List who a user follows.

    Returns ``[]`` for protected/blocked accounts — not an error.

    Args:
        handle: Twitter handle without the leading "@".
        limit: Max users to return (default 50).
    """
    async with _browser.page() as page:
        result = await social_graph.fetch_following(
            page, handle=handle, limit=limit
        )
    return _serialize(result)


@mcp.tool()
async def get_user_mentions(
    handle: str,
    limit: int = 50,
    mode: str = "latest",
) -> list[dict[str, Any]]:
    """Tweets mentioning ``@handle``.

    Thin wrapper over ``search_tweets`` with query ``@<handle>``. Use
    ``mode="latest"`` (default) for chronological monitoring or ``"top"``
    for a relevance-ranked view.
    """
    async with _browser.page() as page:
        tweets_ = await search.fetch(
            page, query=f"@{handle}", mode=mode, limit=limit
        )
    return _serialize(tweets_)


@mcp.tool()
async def get_thread(id_or_url: str) -> dict[str, Any]:
    """Reconstruct an author-only thread from a focal tweet.

    Returns ``{"focal": Tweet|None, "thread": [Tweet, ...]}``: only replies
    authored by the focal's author are included, sorted chronologically.
    Replies from other accounts are dropped. Useful for "chase down a long
    self-reply chain" without paging through unrelated commentary.
    """
    async with _browser.page() as page:
        focal, thread = await tweet.fetch_thread(page, id_or_url=id_or_url)
    return {
        "focal": _serialize(focal),
        "thread": _serialize(thread),
    }


@mcp.tool()
async def get_tweet_quotes(
    id_or_url: str,
    limit: int = 50,
    mode: str = "latest",
) -> list[dict[str, Any]]:
    """Quote-tweets of a tweet (people quoting it, not just retweeting).

    Wraps ``search_tweets`` with ``quoted_tweet_id:<id>``. Twitter doesn't
    expose all quotes for every account; if results look thin, retry with
    a different ``mode`` or fall back to ``search_tweets`` with the tweet URL.
    """
    tweet_id, _ = parse_tweet_id_or_url(id_or_url)
    async with _browser.page() as page:
        tweets_ = await search.fetch(
            page,
            query=f"quoted_tweet_id:{tweet_id}",
            mode=mode,
            limit=limit,
        )
    return _serialize(tweets_)


@mcp.tool()
async def get_liking_users(
    id_or_url: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Users who liked a tweet, newest like first.

    Likes are public unless the tweet's author is protected. Returns ``[]``
    if the like list is hidden by the author or empty.
    """
    async with _browser.page() as page:
        result = await social_graph.fetch_likers(
            page, id_or_url=id_or_url, limit=limit
        )
    return _serialize(result)


@mcp.tool()
async def get_retweeting_users(
    id_or_url: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Users who retweeted a tweet (excludes quote-retweets).

    For quote-retweets specifically, call ``get_tweet_quotes``.
    """
    async with _browser.page() as page:
        result = await social_graph.fetch_retweeters(
            page, id_or_url=id_or_url, limit=limit
        )
    return _serialize(result)


@mcp.tool(structured_output=False)
async def download_media(
    id_or_url: str,
    source: str = "auto",
    indices: list[int] | None = None,
    from_quoted: bool = False,
    download_videos: bool = False,
) -> list[Any]:
    """Download a tweet's (or article's) media so Claude can actually see it.

    Every other tool returns JSON only — for a tweet with a photo or video you
    get the URL and metadata, but never the pixels. This tool fetches the bytes:

    - **Photos** are downloaded, saved under ``downloads/``, AND returned
      inline so they enter the conversation as viewable images.
    - **Videos / GIFs** are NOT fetched unless ``download_videos=True`` — they
      can be large and Claude cannot watch them anyway. Otherwise they appear
      in the result's ``skipped`` list with their URL + duration.

    Works for the focal tweet, a quoted/reposted tweet (``from_quoted=True``),
    and native X articles. For an article the body is additionally saved as
    ``downloads/article_<id>.md`` and ``downloads/article_<id>.html`` — the
    article re-rendered with the downloaded images inlined in their original
    positions, so it can be read offline.

    Args:
        id_or_url: A tweet id/URL, or an X article id/URL.
        source: "auto" (detect from the URL), "tweet", or "article". Only
            needed to disambiguate a bare numeric id.
        indices: 0-based positions of the media items to download. None = all.
        from_quoted: Download the quoted/reposted tweet's media instead of the
            focal tweet's own media.
        download_videos: Opt in to downloading videos/GIFs. When set there is
            no size cap — the opt-in is the only gate.

    Returns:
        A list of inline images (photos) followed by one JSON summary:
        ``{source_id, source_kind, downloaded:[...], skipped:[...],
        markdown_path, html_path}``. ``markdown_path`` / ``html_path`` are the
        saved article files (else null for tweets). Saved files live under
        ``downloads/`` (temporary scratch).
    """
    detected = _detect_source(id_or_url, source)
    article_markdown: str | None = None
    article_html: str | None = None

    async with _browser.page() as page:
        if detected == "article":
            art = await media.resolve_article_media(page, id_or_url=id_or_url)
            source_id, source_kind, items = art.article_id, "article", art.media
            article_markdown = art.body_markdown
            article_html = art.body_html
        else:
            source_id, source_kind, items = await media.resolve_tweet_media(
                page, id_or_url=id_or_url, from_quoted=from_quoted
            )

    selected = _select_indices(items, indices)

    _DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
    client = _get_http_client()

    images: list[Image] = []
    downloaded: list[DownloadedMedia] = []
    skipped: list[SkippedMedia] = []

    for index, item in selected:
        is_photo = item.kind == "photo"

        # Videos/GIFs are opt-in only — never auto-downloaded.
        if not is_photo and not download_videos:
            skipped.append(
                SkippedMedia(
                    kind=item.kind,
                    source_url=item.url,
                    reason="video_not_requested",
                    duration_ms=item.duration_ms,
                )
            )
            continue

        # Images get a safety-net cap; opted-in videos get none (user's call).
        max_bytes = downloader.IMAGE_MAX_BYTES if is_photo else None
        outcome = await downloader.fetch_bytes(
            client, item.url, max_bytes=max_bytes
        )

        if not outcome.ok or outcome.data is None:
            skipped.append(
                SkippedMedia(
                    kind=item.kind,
                    source_url=item.url,
                    reason=outcome.reason or downloader.REASON_DOWNLOAD_ERROR,
                    duration_ms=item.duration_ms,
                )
            )
            continue

        fmt = downloader.content_type_to_format(
            outcome.content_type, item.extension
        )
        ext = downloader.format_to_extension(fmt)
        path = _DOWNLOADS_DIR / f"{source_kind}_{source_id}_{index:02d}.{ext}"
        path.write_bytes(outcome.data)

        downloaded.append(
            DownloadedMedia(
                kind=item.kind,
                source_url=item.url,
                saved_path=str(path),
                byte_size=len(outcome.data),
                content_type=outcome.content_type,
            )
        )
        # Only photos go back inline — Claude can view images, not video bytes.
        if is_photo:
            images.append(Image(data=outcome.data, format=fmt))

    # For articles, also save the body as Markdown and as a standalone HTML
    # document, both with images inlined — each downloaded media's remote URL
    # is rewritten to its local filename (relative: the files sit in
    # downloads/ next to the images).
    markdown_path: str | None = None
    html_path: str | None = None
    if article_markdown is not None:
        markdown_path = _write_article_file(
            source_id, "md", article_markdown, downloaded
        )
    if article_html is not None:
        html_path = _write_article_file(
            source_id, "html", article_html, downloaded
        )

    result = MediaDownloadResult(
        source_id=source_id,
        source_kind=source_kind,
        downloaded=tuple(downloaded),
        skipped=tuple(skipped),
        markdown_path=markdown_path,
        html_path=html_path,
    )
    return [*images, _serialize(result)]


def _write_article_file(
    source_id: str,
    extension: str,
    rendered: str,
    downloaded: list[DownloadedMedia],
) -> str:
    """Rewrite remote image URLs to local filenames, write the file, return path."""
    for entry in downloaded:
        rendered = rendered.replace(entry.source_url, Path(entry.saved_path).name)
    path = _DOWNLOADS_DIR / f"article_{source_id}.{extension}"
    path.write_text(rendered, encoding="utf-8")
    return str(path)


def main() -> None:
    try:
        mcp.run(transport="stdio")
    except SessionExpiredError as exc:
        # Surface a clean message; the MCP layer otherwise hides exceptions
        # in stdio mode.
        import sys

        print(f"twitter-sdk: {exc}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
