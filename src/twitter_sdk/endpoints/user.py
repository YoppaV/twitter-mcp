"""GET a user profile or their tweets timeline."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import Tweet, User
from ..parsers import extract_user_profile, extract_user_tweets
from ..scraper import intercept_single_response, scroll_collect
from ._ids import normalize_handle

if TYPE_CHECKING:
    from playwright.async_api import Page

PROFILE_FRAGMENT = "/UserByScreenName"
TWEETS_FRAGMENT = "/UserTweets"
TWEETS_REPLIES_FRAGMENT = "/UserTweetsAndReplies"
MEDIA_FRAGMENT = "/UserMedia"


async def fetch_profile(page: "Page", *, handle: str) -> User | None:
    """Return the User dataclass for ``handle`` (no @)."""
    h = normalize_handle(handle)
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
    h = normalize_handle(handle)
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
