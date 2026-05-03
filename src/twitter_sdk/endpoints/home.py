"""GET https://x.com/home → list[Tweet], For You or Following feed."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Tweet
from ..parsers import extract_home
from ..scraper import scroll_collect

if TYPE_CHECKING:
    from playwright.async_api import Page

FOR_YOU_URL = "https://x.com/home"
FOLLOWING_URL = "https://x.com/home?f=following"
FOR_YOU_FRAGMENT = "/HomeTimeline"
FOLLOWING_FRAGMENT = "/HomeLatestTimeline"


async def fetch(
    page: "Page",
    *,
    feed: str = "for_you",
    limit: int | None = None,
    since_id: str | None = None,
) -> list[Tweet]:
    if feed not in ("for_you", "following"):
        raise ValueError(f"feed must be 'for_you' or 'following', got {feed!r}")

    if feed == "following":
        url, fragment = FOLLOWING_URL, FOLLOWING_FRAGMENT
    else:
        url, fragment = FOR_YOU_URL, FOR_YOU_FRAGMENT

    return await scroll_collect(
        page,
        url,
        fragment,
        extract_home,
        limit=limit,
        since_id=since_id,
    )
