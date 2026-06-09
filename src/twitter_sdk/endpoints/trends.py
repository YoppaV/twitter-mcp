"""GET https://x.com/explore/tabs/<category> → list[Trend].

Twitter serves the explore tabs through ``GenericTimelineById`` GraphQL
operations. The fragment name is stable, the timeline path is well-known,
and the parser handles missing fields (no post_count exposed = 0).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Trend
from ..parsers import extract_trends
from ..scraper import scroll_collect

if TYPE_CHECKING:
    from playwright.async_api import Page

FRAGMENT = "/GenericTimelineById"

URL_BY_CATEGORY = {
    "trending": "https://x.com/explore/tabs/trending",
    "news": "https://x.com/explore/tabs/news",
    "sports": "https://x.com/explore/tabs/sports",
    "entertainment": "https://x.com/explore/tabs/entertainment",
    "for_you": "https://x.com/explore/tabs/for-you",
}


async def fetch(
    page: "Page",
    *,
    category: str = "trending",
    limit: int | None = 20,
) -> list[Trend]:
    """Return the current trends for ``category``.

    ``category`` must be one of: ``trending``, ``news``, ``sports``,
    ``entertainment``, ``for_you``.
    """
    if category not in URL_BY_CATEGORY:
        valid = ", ".join(sorted(URL_BY_CATEGORY))
        raise ValueError(f"category must be one of: {valid}. Got {category!r}.")

    return await scroll_collect(
        page,
        URL_BY_CATEGORY[category],
        FRAGMENT,
        extract_trends,
        limit=limit,
        key_of=lambda t: t.name,
    )
