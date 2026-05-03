"""GET a single tweet by ID or URL → Tweet (focal + replies + quoted)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Tweet
from ..parsers import extract_tweet_detail
from ..scraper import intercept_single_response
from ._ids import parse_tweet_id_or_url

if TYPE_CHECKING:
    from playwright.async_api import Page

FRAGMENT = "/TweetDetail"


async def fetch(
    page: "Page",
    *,
    id_or_url: str,
    include_replies: bool = True,
    include_quote: bool = True,
) -> tuple[Tweet | None, list[Tweet]]:
    """Return ``(focal_tweet, replies)``.

    ``include_quote`` is informational — the quoted tweet is always embedded
    in the focal Tweet's ``quoted`` field when present in the GraphQL payload.
    """
    tweet_id, url = parse_tweet_id_or_url(id_or_url)
    tweets = await intercept_single_response(
        page,
        url,
        FRAGMENT,
        extract_tweet_detail,
    )
    if not tweets:
        return None, []

    focal: Tweet | None = None
    replies: list[Tweet] = []
    for t in tweets:  # type: ignore[union-attr]
        if t.tweet_id == tweet_id and focal is None:
            focal = t
        else:
            replies.append(t)

    if focal is None and tweets:
        focal = tweets[0]  # type: ignore[index]
        replies = list(tweets[1:])  # type: ignore[index]

    if not include_replies:
        replies = []

    if focal is not None and not include_quote:
        focal = _strip_quote(focal)

    return focal, replies


def _strip_quote(tweet: Tweet) -> Tweet:
    if tweet.quoted is None:
        return tweet
    from dataclasses import replace

    return replace(tweet, quoted=None)


async def fetch_thread(
    page: "Page",
    *,
    id_or_url: str,
) -> tuple[Tweet | None, list[Tweet]]:
    """Return ``(focal, [tweets-by-same-author-replying-down-the-chain])``.

    A "thread" is the sequence of replies authored by the focal tweet's author —
    used to reconstruct self-replies (the dominant Twitter long-form pattern).
    Replies from other accounts are dropped. Returns ``(None, [])`` when the
    focal can't be loaded.
    """
    focal, replies = await fetch(
        page, id_or_url=id_or_url, include_replies=True, include_quote=True
    )
    if focal is None:
        return None, []
    same_author = [r for r in replies if r.author_handle == focal.author_handle]
    same_author.sort(key=lambda t: t.created_at)
    return focal, same_author
