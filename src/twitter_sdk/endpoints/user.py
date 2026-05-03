"""GET a user profile or their tweets timeline."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ..models import Tweet, User
from ..parsers import extract_user_profile, extract_user_tweets
from ..scraper import intercept_single_response, scroll_collect

if TYPE_CHECKING:
    from playwright.async_api import Page

PROFILE_FRAGMENT = "/UserByScreenName"
TWEETS_FRAGMENT = "/UserTweets"
TWEETS_REPLIES_FRAGMENT = "/UserTweetsAndReplies"
MEDIA_FRAGMENT = "/UserMedia"

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")


def _normalize_handle(handle: str) -> str:
    h = handle.strip().lstrip("@")
    if not _HANDLE_RE.match(h):
        raise ValueError(
            f"Invalid Twitter handle {handle!r}. Must be 1–15 chars, "
            "letters/digits/underscores only."
        )
    return h


async def fetch_profile(page: "Page", *, handle: str) -> User | None:
    """Return the User dataclass for ``handle`` (no @)."""
    h = _normalize_handle(handle)
    return await intercept_single_response(
        page,
        f"https://x.com/{h}",
        PROFILE_FRAGMENT,
        extract_user_profile,
    )


async def fetch_tweets(
    page: "Page",
    *,
    handle: str,
    limit: int | None = None,
    include_replies: bool = False,
    include_media_only: bool = False,
) -> list[Tweet]:
    """Scroll a user's profile timeline.

    ``include_replies`` switches to the With Replies tab (UserTweetsAndReplies).
    ``include_media_only`` switches to the Media tab (UserMedia).
    """
    h = _normalize_handle(handle)
    if include_media_only:
        url = f"https://x.com/{h}/media"
        fragment = MEDIA_FRAGMENT
    elif include_replies:
        url = f"https://x.com/{h}/with_replies"
        fragment = TWEETS_REPLIES_FRAGMENT
    else:
        url = f"https://x.com/{h}"
        fragment = TWEETS_FRAGMENT

    return await scroll_collect(
        page,
        url,
        fragment,
        extract_user_tweets,
        limit=limit,
    )
