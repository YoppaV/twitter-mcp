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

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

from .auth import SessionExpiredError, session_summary
from .browser import DEFAULT_IDLE_TIMEOUT_S, BrowserSession
from .endpoints import article, bookmarks, home, search, tweet, user

load_dotenv()

mcp = FastMCP("twitter-sdk")

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_SESSION_DIR = _PROJECT_ROOT / "sessions"


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
        ``{article_id, url, title, body_text, body_html, char_count}`` or
        ``None`` if the article failed to render (deleted, restricted, or the
        reader view didn't appear within ~8 seconds).
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
