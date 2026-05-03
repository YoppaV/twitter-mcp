"""GET a single tweet by ID or URL → Tweet (focal + replies + quoted)."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..models import Tweet
from ..parsers import extract_tweet_detail
from ..scraper import intercept_single_response

if TYPE_CHECKING:
    from playwright.async_api import Page

FRAGMENT = "/TweetDetail"

_ID_RE = re.compile(r"(\d+)")
_STATUS_PATH_RE = re.compile(r"/(\w+)/status/(\d+)")


def _parse_id_or_url(id_or_url: str) -> tuple[str, str]:
    """Return ``(tweet_id, navigation_url)``.

    If the input looks like a numeric ID, navigate via /i/web/status/<id>
    (works without knowing the author handle). If it's a URL, normalize it.
    """
    text = id_or_url.strip()
    match = _STATUS_PATH_RE.search(text)
    if match:
        handle, tid = match.group(1), match.group(2)
        return tid, f"https://x.com/{handle}/status/{tid}"

    id_match = _ID_RE.fullmatch(text)
    if id_match:
        tid = id_match.group(1)
        return tid, f"https://x.com/i/web/status/{tid}"

    raise ValueError(
        f"Could not parse a tweet id or URL from {id_or_url!r}. "
        "Pass the numeric id or a full https://x.com/<handle>/status/<id> URL."
    )


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
    tweet_id, url = _parse_id_or_url(id_or_url)
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
