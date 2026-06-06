"""GET https://x.com/search?q=... → list[Tweet], top or latest."""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import quote_plus

from ..models import Tweet
from .. import hermes_tweet
from ..parsers import extract_search
from ..scraper import scroll_collect

if TYPE_CHECKING:
    from playwright.async_api import Page

FRAGMENT = "/SearchTimeline"


def _build_url(query: str, mode: str) -> str:
    f = "live" if mode == "latest" else "top"
    return f"https://x.com/search?q={quote_plus(query)}&src=typed_query&f={f}"


async def fetch(
    page: "Page",
    *,
    query: str,
    mode: str = "top",
    limit: int | None = None,
) -> list[Tweet]:
    if mode not in ("top", "latest"):
        raise ValueError(f"mode must be 'top' or 'latest', got {mode!r}")
    if not query.strip():
        raise ValueError("query must not be empty")

    if hermes_tweet.is_configured():
        return await hermes_tweet.search(query=query, mode=mode, limit=limit)

    return await scroll_collect(
        page,
        _build_url(query, mode),
        FRAGMENT,
        extract_search,
        limit=limit,
    )
