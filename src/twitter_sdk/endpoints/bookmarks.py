"""GET https://x.com/i/bookmarks → list[Tweet]."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Tweet
from ..parsers import extract_bookmarks
from ..scraper import scroll_collect

if TYPE_CHECKING:
    from playwright.async_api import Page

URL = "https://x.com/i/bookmarks"
FRAGMENT = "/Bookmarks"


async def fetch(
    page: "Page",
    *,
    limit: int | None = None,
    since_id: str | None = None,
) -> list[Tweet]:
    return await scroll_collect(
        page,
        URL,
        FRAGMENT,
        extract_bookmarks,
        limit=limit,
        since_id=since_id,
    )
