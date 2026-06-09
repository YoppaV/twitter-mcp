"""Pure extractors that turn Twitter GraphQL JSON payloads into our dataclasses.

No Playwright / IO here. Functions accept dicts, return dataclasses or ``None``.
This module is exhaustively unit-testable with captured GraphQL fixtures.

Twitter's GraphQL is versioned by request hash but the payload shapes are
stable enough to share parsers across endpoints (Bookmarks, HomeTimeline,
TweetDetail, SearchTimeline, UserTweets all reuse the same Tweet shape).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Iterable

from .models import MediaItem, QuotedTweet, Trend, Tweet, User, XArticle

_PHOTO_NAME_QUERY = "?name=orig"


def parse_created_at(raw: str) -> str:
    """Convert Twitter's ``"Mon Apr 13 01:05:36 +0000 2026"`` to ISO 8601 UTC."""
    if not raw:
        return ""
    try:
        dt = datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y")
    except ValueError:
        return raw
    return dt.isoformat()


def _pick_best_mp4_variant(variants: list[dict[str, Any]]) -> str | None:
    mp4s = [v for v in variants if v.get("content_type") == "video/mp4"]
    if not mp4s:
        return None
    mp4s.sort(key=lambda v: v.get("bitrate") or 0, reverse=True)
    return mp4s[0].get("url")


def _extract_media(legacy: dict[str, Any]) -> tuple[MediaItem, ...]:
    extended = legacy.get("extended_entities") or {}
    media_entries = extended.get("media") or []
    items: list[MediaItem] = []

    for entry in media_entries:
        kind_raw = entry.get("type")
        if kind_raw == "photo":
            base_url = entry.get("media_url_https") or ""
            if not base_url:
                continue
            items.append(
                MediaItem(
                    kind="photo",
                    url=base_url + _PHOTO_NAME_QUERY,
                    extension="jpg",
                )
            )
        elif kind_raw in ("video", "animated_gif"):
            video_info = entry.get("video_info") or {}
            variants = video_info.get("variants") or []
            best = _pick_best_mp4_variant(variants)
            if not best:
                continue
            duration_raw = video_info.get("duration_millis")
            duration_ms = int(duration_raw) if duration_raw is not None else None
            items.append(
                MediaItem(
                    kind="gif" if kind_raw == "animated_gif" else "video",
                    url=best,
                    extension="mp4",
                    duration_ms=duration_ms,
                )
            )

    return tuple(items)


def _extract_hashtags(
    legacy: dict[str, Any], note_result: dict[str, Any] | None
) -> tuple[str, ...]:
    entities = legacy.get("entities") or {}
    tags = [h.get("text", "") for h in (entities.get("hashtags") or []) if h.get("text")]
    if note_result:
        entity_set = note_result.get("entity_set") or {}
        tags.extend(
            h.get("text", "") for h in (entity_set.get("hashtags") or []) if h.get("text")
        )
    return tuple(dict.fromkeys(tags))


def _extract_mentions(
    legacy: dict[str, Any], note_result: dict[str, Any] | None
) -> tuple[str, ...]:
    entities = legacy.get("entities") or {}
    names = [
        m.get("screen_name", "")
        for m in (entities.get("user_mentions") or [])
        if m.get("screen_name")
    ]
    if note_result:
        entity_set = note_result.get("entity_set") or {}
        names.extend(
            m.get("screen_name", "")
            for m in (entity_set.get("user_mentions") or [])
            if m.get("screen_name")
        )
    return tuple(dict.fromkeys(names))


def _extract_urls(
    legacy: dict[str, Any], note_result: dict[str, Any] | None
) -> tuple[str, ...]:
    entities = legacy.get("entities") or {}
    urls = [
        u.get("expanded_url", "")
        for u in (entities.get("urls") or [])
        if u.get("expanded_url")
    ]
    if note_result:
        entity_set = note_result.get("entity_set") or {}
        urls.extend(
            u.get("expanded_url", "")
            for u in (entity_set.get("urls") or [])
            if u.get("expanded_url")
        )
    return tuple(dict.fromkeys(u for u in urls if u))


def _extract_author(result: dict[str, Any]) -> tuple[str, str, str]:
    """Return ``(screen_name, display_name, user_id)``.

    Twitter moved screen_name/name from ``user_results.result.legacy`` to
    ``user_results.result.core``. Try the new location first, fall back.
    """
    core = result.get("core") or {}
    user_results = core.get("user_results") or {}
    user_result = user_results.get("result") or {}
    user_core = user_result.get("core") or {}
    user_legacy = user_result.get("legacy") or {}
    return (
        user_core.get("screen_name") or user_legacy.get("screen_name") or "",
        user_core.get("name") or user_legacy.get("name") or "",
        user_result.get("rest_id", "") or "",
    )


def _note_tweet_result(result: dict[str, Any]) -> dict[str, Any] | None:
    note = result.get("note_tweet") or {}
    note_results = note.get("note_tweet_results") or {}
    return note_results.get("result") or None


def _extract_full_text(
    legacy: dict[str, Any], note_result: dict[str, Any] | None
) -> tuple[str, bool]:
    if note_result:
        note_text = note_result.get("text")
        if note_text:
            return note_text, True
    return legacy.get("full_text", "") or "", False


def _extract_x_article(result: dict[str, Any]) -> XArticle | None:
    article = result.get("article") or {}
    article_results = article.get("article_results") or {}
    article_result = article_results.get("result")
    if not article_result:
        return None

    cover_media = article_result.get("cover_media") or {}
    media_info = cover_media.get("media_info") or {}
    metadata = article_result.get("metadata") or {}

    return XArticle(
        article_id=article_result.get("rest_id", "") or "",
        title=article_result.get("title", "") or "",
        preview_text=article_result.get("preview_text", "") or "",
        cover_image_url=media_info.get("original_img_url", "") or "",
        first_published_at=int(metadata.get("first_published_at_secs") or 0),
    )


def _unwrap_visibility_shim(result: dict[str, Any]) -> dict[str, Any]:
    if result.get("__typename") == "TweetWithVisibilityResults":
        return result.get("tweet") or {}
    return result


def _extract_quoted(result: dict[str, Any]) -> QuotedTweet | None:
    wrapper = result.get("quoted_status_result") or {}
    q = wrapper.get("result")
    if not q:
        return None

    q = _unwrap_visibility_shim(q)
    q_legacy = q.get("legacy")
    if not q_legacy:
        return None

    author_handle, author_name, _ = _extract_author(q)
    note_result = _note_tweet_result(q)
    text, _ = _extract_full_text(q_legacy, note_result)
    urls = _extract_urls(q_legacy, note_result)

    return QuotedTweet(
        tweet_id=q.get("rest_id", "") or q_legacy.get("id_str", "") or "",
        author_handle=author_handle,
        author_name=author_name,
        text=text,
        urls=urls,
        x_article=_extract_x_article(q),
    )


def tweet_from_item_content(item_content: dict[str, Any]) -> Tweet | None:
    tweet_results = item_content.get("tweet_results") or {}
    result = tweet_results.get("result") or {}
    result = _unwrap_visibility_shim(result)
    return tweet_from_result(result)


def tweet_from_result(result: dict[str, Any]) -> Tweet | None:
    """Build a Tweet from a ``...result`` GraphQL node.

    Used both by timeline parsers (after unwrapping ``itemContent``) and by
    TweetDetail (where the tweet result sits at the top of the payload).
    """
    result = _unwrap_visibility_shim(result)
    legacy = result.get("legacy")
    if not legacy:
        return None

    tweet_id = result.get("rest_id") or legacy.get("id_str") or ""
    if not tweet_id:
        return None

    author_handle, author_name, author_id = _extract_author(result)
    note_result = _note_tweet_result(result)
    text, is_long_form = _extract_full_text(legacy, note_result)

    return Tweet(
        tweet_id=tweet_id,
        created_at=parse_created_at(legacy.get("created_at", "")),
        author_handle=author_handle,
        author_name=author_name,
        author_id=author_id,
        text=text,
        is_long_form=is_long_form,
        likes=int(legacy.get("favorite_count") or 0),
        retweets=int(legacy.get("retweet_count") or 0),
        replies=int(legacy.get("reply_count") or 0),
        quote_count=int(legacy.get("quote_count") or 0),
        lang=legacy.get("lang", "") or "",
        hashtags=_extract_hashtags(legacy, note_result),
        mentions=_extract_mentions(legacy, note_result),
        urls=_extract_urls(legacy, note_result),
        media=_extract_media(legacy),
        x_article=_extract_x_article(result),
        quoted=_extract_quoted(result),
    )


def _iter_timeline_instructions(
    timeline_root: dict[str, Any],
) -> Iterable[dict[str, Any]]:
    instructions = (timeline_root or {}).get("instructions") or []
    for instruction in instructions:
        instr_type = instruction.get("type")
        if instr_type == "TimelineAddEntries":
            yield from instruction.get("entries") or []
        elif instr_type == "TimelinePinEntry":
            entry = instruction.get("entry")
            if entry:
                yield entry
        elif instr_type == "TimelineAddToModule":
            for module_item in instruction.get("moduleItems") or []:
                yield {"content": {"entryType": "TimelineTimelineItem", "itemContent": module_item.get("item", {}).get("itemContent", {})}}


def _entries_from_entry(entry: dict[str, Any]) -> Iterable[Tweet]:
    """Yield Tweet objects from any timeline entry shape."""
    content = entry.get("content") or {}
    entry_type = content.get("entryType")
    entry_id = str(entry.get("entryId") or "")

    if "promoted-" in entry_id or entry_id.startswith("promotedTweet-"):
        return

    if entry_type in (None, "TimelineTimelineItem"):
        item_content = content.get("itemContent") or {}
        if item_content.get("itemType") and item_content.get("itemType") != "TimelineTweet":
            return
        tweet = tweet_from_item_content(item_content)
        if tweet is not None:
            yield tweet
        return

    if entry_type == "TimelineTimelineModule":
        for item in content.get("items") or []:
            inner = item.get("item") or {}
            item_content = inner.get("itemContent") or {}
            if item_content.get("itemType") and item_content.get("itemType") != "TimelineTweet":
                continue
            tweet = tweet_from_item_content(item_content)
            if tweet is not None:
                yield tweet


def _walk_timeline(timeline_root: dict[str, Any]) -> list[Tweet]:
    tweets: list[Tweet] = []
    for entry in _iter_timeline_instructions(timeline_root):
        tweets.extend(_entries_from_entry(entry))
    return tweets


def extract_bookmarks(payload: dict[str, Any]) -> list[Tweet]:
    """Bookmarks live at ``data.bookmark_timeline_v2.timeline``."""
    data = payload.get("data") or {}
    timeline_v2 = data.get("bookmark_timeline_v2") or {}
    return _walk_timeline(timeline_v2.get("timeline") or {})


def extract_home(payload: dict[str, Any]) -> list[Tweet]:
    """Home timeline lives at ``data.home.home_timeline_urt``.

    Strips promoted entries (ads).
    """
    data = payload.get("data") or {}
    home = data.get("home") or {}
    return _walk_timeline(home.get("home_timeline_urt") or {})


def extract_search(payload: dict[str, Any]) -> list[Tweet]:
    """SearchTimeline lives at ``data.search_by_raw_query.search_timeline.timeline``."""
    data = payload.get("data") or {}
    search = data.get("search_by_raw_query") or {}
    search_timeline = search.get("search_timeline") or {}
    return _walk_timeline(search_timeline.get("timeline") or {})


def extract_user_tweets(payload: dict[str, Any]) -> list[Tweet]:
    """UserTweets lives at ``data.user.result.timeline_v2.timeline``.

    Older UserTweets responses use ``timeline.timeline``; we try both.
    """
    data = payload.get("data") or {}
    user = data.get("user") or {}
    result = user.get("result") or {}
    timeline_v2 = result.get("timeline_v2") or result.get("timeline") or {}
    timeline = timeline_v2.get("timeline") or {}
    return _walk_timeline(timeline)


def extract_tweet_detail(payload: dict[str, Any]) -> list[Tweet]:
    """TweetDetail lives at ``data.threaded_conversation_with_injections_v2``.

    Returns the focal tweet first, followed by replies in conversation order.
    """
    data = payload.get("data") or {}
    convo = data.get("threaded_conversation_with_injections_v2") or {}
    return _walk_timeline(convo)


def _user_from_result(result: dict[str, Any] | None) -> User | None:
    """Build a User from a ``user_results.result`` (or top-level ``result``) node.

    Used by both ``extract_user_profile`` (single user) and
    ``extract_user_list`` (followers/following/likers/retweeters timelines),
    which carry the same node shape.
    """
    if not result:
        return None
    if result.get("__typename") == "UserUnavailable":
        return None

    core = result.get("core") or {}
    legacy = result.get("legacy") or {}
    avatar = result.get("avatar") or {}
    location = result.get("location") or {}
    verification = result.get("verification") or {}
    privacy = result.get("privacy") or {}

    handle = core.get("screen_name") or legacy.get("screen_name") or ""
    name = core.get("name") or legacy.get("name") or ""
    user_id = result.get("rest_id") or legacy.get("id_str") or ""
    if not user_id and not handle:
        return None

    bio = legacy.get("description", "") or ""
    url = ""
    entities = legacy.get("entities") or {}
    url_entities = (entities.get("url") or {}).get("urls") or []
    if url_entities:
        url = url_entities[0].get("expanded_url") or ""

    pinned_ids = legacy.get("pinned_tweet_ids_str") or []
    pinned_tweet_id = pinned_ids[0] if pinned_ids else None

    return User(
        user_id=user_id,
        handle=handle,
        name=name,
        bio=bio,
        location=location.get("location") or legacy.get("location", "") or "",
        url=url,
        created_at=parse_created_at(core.get("created_at") or legacy.get("created_at", "")),
        verified=bool(verification.get("verified") or legacy.get("verified") or False),
        protected=bool(privacy.get("protected") or legacy.get("protected") or False),
        followers_count=int(legacy.get("followers_count") or 0),
        following_count=int(legacy.get("friends_count") or 0),
        tweet_count=int(legacy.get("statuses_count") or 0),
        listed_count=int(legacy.get("listed_count") or 0),
        profile_image_url=avatar.get("image_url") or legacy.get("profile_image_url_https", "") or "",
        pinned_tweet_id=pinned_tweet_id,
    )


def extract_user_profile(payload: dict[str, Any]) -> User | None:
    """UserByScreenName payload → User dataclass.

    Layout: ``data.user.result`` carries ``core`` (handle/name) +
    ``legacy`` (counters, bio) + ``rest_id``.
    """
    data = payload.get("data") or {}
    user = data.get("user") or {}
    return _user_from_result(user.get("result"))


def extract_user_list(payload: dict[str, Any]) -> list[User]:
    """Parse a Followers / Following / Favoriters / Retweeters payload.

    All four timelines share a structure:
    ``data.user.result.timeline.timeline.instructions[]`` holding entries with
    ``content.itemContent.user_results.result``. Reuses
    ``_iter_timeline_instructions`` so pin / module / add-entries shapes all
    work.
    """
    data = payload.get("data") or {}
    user = data.get("user") or {}
    result = user.get("result") or {}
    timeline_v2 = result.get("timeline") or result.get("timeline_v2") or {}
    timeline_root = timeline_v2.get("timeline") or {}

    users: list[User] = []
    seen: set[str] = set()
    for entry in _iter_timeline_instructions(timeline_root):
        for parsed in _users_from_entry(entry):
            key = parsed.user_id or parsed.handle
            if not key or key in seen:
                continue
            seen.add(key)
            users.append(parsed)
    return users


def _users_from_entry(entry: dict[str, Any]) -> Iterable[User]:
    content = entry.get("content") or {}
    entry_type = content.get("entryType")
    entry_id = str(entry.get("entryId") or "")

    if "promoted-" in entry_id or entry_id.startswith("promotedTweet-"):
        return

    if entry_type in (None, "TimelineTimelineItem"):
        item_content = content.get("itemContent") or {}
        item_type = item_content.get("itemType")
        if item_type and item_type != "TimelineUser":
            return
        user = _user_from_user_results(item_content)
        if user is not None:
            yield user
        return

    if entry_type == "TimelineTimelineModule":
        for item in content.get("items") or []:
            inner = item.get("item") or {}
            item_content = inner.get("itemContent") or {}
            item_type = item_content.get("itemType")
            if item_type and item_type != "TimelineUser":
                continue
            user = _user_from_user_results(item_content)
            if user is not None:
                yield user


def _user_from_user_results(item_content: dict[str, Any]) -> User | None:
    user_results = item_content.get("user_results") or {}
    return _user_from_result(user_results.get("result"))


def extract_trends(payload: dict[str, Any]) -> list[Trend]:
    """Parse a GenericTimelineById trends payload into Trend objects.

    Trends entries carry an ``itemContent.itemType == "TimelineTrend"`` with a
    ``name``, optional ``trend_url.url``, and ``trend_metadata`` carrying the
    domain context (category) plus ``meta_description`` (post count, e.g.
    ``"15.2K posts"``).
    """
    data = payload.get("data") or {}
    timeline_response = data.get("timeline_response") or data.get("timeline") or {}
    inner = timeline_response.get("timeline_response") or timeline_response
    timeline_root = inner.get("timeline") or inner

    trends: list[Trend] = []
    seen: set[str] = set()
    for entry in _iter_timeline_instructions(timeline_root):
        for trend in _trends_from_entry(entry):
            if trend.name in seen:
                continue
            seen.add(trend.name)
            trends.append(trend)
    return trends


def _trends_from_entry(entry: dict[str, Any]) -> Iterable[Trend]:
    content = entry.get("content") or {}
    entry_type = content.get("entryType")

    if entry_type in (None, "TimelineTimelineItem"):
        item_content = content.get("itemContent") or {}
        item_type = item_content.get("itemType")
        if item_type and item_type != "TimelineTrend":
            return
        trend = _trend_from_item_content(item_content)
        if trend is not None:
            yield trend
        return

    if entry_type == "TimelineTimelineModule":
        for item in content.get("items") or []:
            inner = item.get("item") or {}
            item_content = inner.get("itemContent") or {}
            item_type = item_content.get("itemType")
            if item_type and item_type != "TimelineTrend":
                continue
            trend = _trend_from_item_content(item_content)
            if trend is not None:
                yield trend


def _trend_from_item_content(item_content: dict[str, Any]) -> Trend | None:
    trend_node = item_content.get("trend") or item_content
    name = trend_node.get("name") or trend_node.get("trend_name") or ""
    if not name:
        return None

    trend_url = trend_node.get("trend_url") or {}
    if isinstance(trend_url, dict):
        url = trend_url.get("url") or ""
    else:
        url = trend_url

    metadata = trend_node.get("trend_metadata") or {}
    category = metadata.get("domain_context") or ""
    meta_description = metadata.get("meta_description") or ""
    post_count = _parse_post_count(meta_description)

    query = trend_node.get("trend_keyword") or name
    return Trend(
        name=name,
        query=query,
        url=url or f"https://x.com/search?q={name}",
        post_count=post_count,
        category=category,
    )


def _parse_post_count(text: str) -> int:
    """Extract a numeric count from strings like ``"15.2K posts"`` or ``"302 Tweets"``."""
    if not text:
        return 0
    cleaned = text.strip().split()
    if not cleaned:
        return 0
    head = cleaned[0].replace(",", "")
    multiplier = 1
    if head.endswith(("K", "k")):
        multiplier = 1_000
        head = head[:-1]
    elif head.endswith(("M", "m")):
        multiplier = 1_000_000
        head = head[:-1]
    elif head.endswith(("B", "b")):
        multiplier = 1_000_000_000
        head = head[:-1]
    try:
        value = float(head)
    except ValueError:
        return 0
    return int(value * multiplier)


# Map from fragment name → extractor. Used by the generic scroll loop.
EXTRACTORS: dict[str, Callable[[dict[str, Any]], list[Tweet]]] = {
    "Bookmarks": extract_bookmarks,
    "HomeTimeline": extract_home,
    "HomeLatestTimeline": extract_home,
    "SearchTimeline": extract_search,
    "UserTweets": extract_user_tweets,
    "UserTweetsAndReplies": extract_user_tweets,
    "UserMedia": extract_user_tweets,
    "TweetDetail": extract_tweet_detail,
}
