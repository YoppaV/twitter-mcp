"""GET followers / following / likers / retweeters → list[User].

All four shapes share the GraphQL timeline-of-users payload. Each helper here
is a thin wrapper over ``scroll_collect`` with the right URL and fragment.

For accounts that are protected, blocked, or otherwise unavailable, Twitter
serves an empty timeline; the helpers return ``[]`` with no error — that's the
expected behaviour, not a bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..models import User
from ..parsers import extract_user_list
from ..scraper import scroll_collect
from ._ids import normalize_handle, parse_tweet_id_or_url

if TYPE_CHECKING:
    from playwright.async_api import Page

FOLLOWERS_FRAGMENT = "/Followers"
FOLLOWING_FRAGMENT = "/Following"
FAVORITERS_FRAGMENT = "/Favoriters"
RETWEETERS_FRAGMENT = "/Retweeters"


def _user_key(user: User) -> str:
    return user.user_id or user.handle


async def fetch_followers(
    page: "Page",
    *,
    handle: str,
    limit: int | None = None,
) -> list[User]:
    h = normalize_handle(handle)
    return await scroll_collect(
        page,
        f"https://x.com/{h}/followers",
        FOLLOWERS_FRAGMENT,
        extract_user_list,
        limit=limit,
        key_of=_user_key,
    )


async def fetch_following(
    page: "Page",
    *,
    handle: str,
    limit: int | None = None,
) -> list[User]:
    h = normalize_handle(handle)
    return await scroll_collect(
        page,
        f"https://x.com/{h}/following",
        FOLLOWING_FRAGMENT,
        extract_user_list,
        limit=limit,
        key_of=_user_key,
    )


async def fetch_likers(
    page: "Page",
    *,
    id_or_url: str,
    limit: int | None = None,
) -> list[User]:
    """Users who liked a tweet. Likes are public unless the author is private."""
    tweet_id, tweet_url = parse_tweet_id_or_url(id_or_url)
    likes_url = _swap_status_path(tweet_url, "likes")
    return await scroll_collect(
        page,
        likes_url,
        FAVORITERS_FRAGMENT,
        extract_user_list,
        limit=limit,
        key_of=_user_key,
    )


async def fetch_retweeters(
    page: "Page",
    *,
    id_or_url: str,
    limit: int | None = None,
) -> list[User]:
    """Users who retweeted a tweet (not quote-retweets — see get_tweet_quotes)."""
    tweet_id, tweet_url = parse_tweet_id_or_url(id_or_url)
    retweets_url = _swap_status_path(tweet_url, "retweets")
    return await scroll_collect(
        page,
        retweets_url,
        RETWEETERS_FRAGMENT,
        extract_user_list,
        limit=limit,
        key_of=_user_key,
    )


def _swap_status_path(tweet_url: str, suffix: str) -> str:
    """Convert ``.../status/<id>`` to ``.../status/<id>/<suffix>``."""
    return tweet_url.rstrip("/") + f"/{suffix}"
