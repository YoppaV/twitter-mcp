"""Resolve which media items belong to a tweet, a quoted tweet, or an article.

Sits between the Playwright endpoints (``tweet.fetch`` / ``article.fetch``) and
the ``download_media`` server tool. This layer answers *which URLs* — it never
downloads bytes (that's ``downloader.fetch_bytes``).

A ``SessionExpiredError`` raised by the underlying ``fetch`` calls propagates
unchanged, so the tool surfaces the re-login instruction like every other one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import MediaItem
from . import article, tweet

if TYPE_CHECKING:
    from playwright.async_api import Page

# (source_id, source_kind, media)
Resolved = tuple[str, str, tuple[MediaItem, ...]]


async def resolve_tweet_media(
    page: "Page",
    *,
    id_or_url: str,
    from_quoted: bool = False,
) -> Resolved:
    """Resolve media for a tweet — or, with ``from_quoted``, its quoted tweet.

    A quoted/reposted tweet comes back inside the focal Tweet's ``quoted``
    field with no ``media``; we re-fetch it by id so its full ``media`` is
    populated. Raises ``ValueError`` if the tweet can't be loaded, or if
    ``from_quoted`` is set but there is no quoted tweet.
    """
    focal, _ = await tweet.fetch(
        page, id_or_url=id_or_url, include_replies=False, include_quote=True
    )
    if focal is None:
        raise ValueError(
            f"Tweet {id_or_url!r} could not be loaded — it may be deleted, "
            "protected, or the page failed to render."
        )

    if not from_quoted:
        return focal.tweet_id, "tweet", focal.media

    if focal.quoted is None:
        raise ValueError(
            f"Tweet {id_or_url!r} has no quoted/reposted tweet to pull media "
            "from. Drop from_quoted to download the tweet's own media."
        )

    quoted_id = focal.quoted.tweet_id
    quoted, _ = await tweet.fetch(
        page, id_or_url=quoted_id, include_replies=False, include_quote=False
    )
    if quoted is None:
        raise ValueError(
            f"The quoted tweet {quoted_id!r} could not be loaded — it may be "
            "deleted or protected."
        )
    return quoted.tweet_id, "tweet", quoted.media


async def resolve_article_media(page: "Page", *, id_or_url: str) -> Resolved:
    """Resolve media for a native X article via its rendered reader DOM.

    Raises ``ValueError`` if the article didn't render (deleted, restricted,
    or the reader view never appeared).
    """
    result = await article.fetch(page, id_or_url=id_or_url)
    if result is None:
        raise ValueError(
            f"Article {id_or_url!r} could not be loaded — it may be deleted, "
            "restricted, or the reader view didn't render."
        )
    return result.article_id, "article", result.media
