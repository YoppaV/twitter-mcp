"""Endpoint integration tests with a faked Playwright Page.

Each test stands up a ``FakePage`` that records response handlers, then synthesizes
a ``FakeResponse`` carrying a captured GraphQL fixture and dispatches it through
the handler. The endpoint code is exercised end-to-end except for actual browser
IO and scrolling sleeps (we monkey-patch ``asyncio.sleep`` to a no-op).

Run with: ``venv/bin/python -m unittest tests.test_endpoints -v``
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any, Awaitable, Callable
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from twitter_sdk import scraper as scraper_mod  # noqa: E402
from twitter_sdk.endpoints import (  # noqa: E402
    article,
    bookmarks,
    home,
    media,
    search,
    social_graph,
    trends,
    tweet,
    user,
)
from twitter_sdk.models import (  # noqa: E402
    Article,
    MediaItem,
    QuotedTweet,
    Tweet,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeResponse:
    """Mimics a playwright.async_api.Response for the response handler."""

    def __init__(self, url: str, payload: dict[str, Any]) -> None:
        self.url = url
        self._payload = payload

    async def json(self) -> dict[str, Any]:
        return self._payload


class FakePage:
    """Minimal Page double.

    The endpoint installs a ``response`` listener; the test fires synthetic
    responses through it after ``goto``.
    """

    def __init__(
        self,
        *,
        url: str = "https://x.com/home",
        responses: list[FakeResponse] | None = None,
    ) -> None:
        self.url = url
        self._responses = responses or []
        self._listeners: dict[str, list[Callable[..., Awaitable[None]]]] = {}
        self.scroll_calls = 0
        self.evaluate_calls: list[str] = []
        self.goto_calls: list[str] = []

    def on(self, event: str, handler: Callable[..., Any]) -> None:
        self._listeners.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Callable[..., Any]) -> None:
        self._listeners.get(event, []).remove(handler)

    async def goto(self, url: str, **_: Any) -> None:
        self.goto_calls.append(url)
        self.url = url
        await self._fire_responses()

    async def evaluate(self, script: str) -> None:
        self.evaluate_calls.append(script)
        self.scroll_calls += 1

    async def _fire_responses(self) -> None:
        for response in self._responses:
            for handler in self._listeners.get("response", []):
                await handler(response)


def _no_sleep_patch() -> mock._patch:
    async def _zero(*_: Any, **__: Any) -> None:
        return None

    return mock.patch.object(scraper_mod.asyncio, "sleep", _zero)


def _run(coro: Awaitable[Any]) -> Any:
    return asyncio.run(coro)


class BookmarksEndpointTests(unittest.TestCase):
    def test_returns_serialized_tweets(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/Bookmarks?variables=...",
                    _load("graphql_bookmarks.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(bookmarks.fetch(page, limit=10))
        self.assertEqual([t.tweet_id for t in tweets], ["1111", "2222"])
        self.assertEqual(page.goto_calls, ["https://x.com/i/bookmarks"])

    def test_since_id_truncates(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/Bookmarks",
                    _load("graphql_bookmarks.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(bookmarks.fetch(page, since_id="2222"))
        self.assertEqual([t.tweet_id for t in tweets], ["1111"])

    def test_limit_caps(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/Bookmarks",
                    _load("graphql_bookmarks.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(bookmarks.fetch(page, limit=1))
        self.assertEqual(len(tweets), 1)


class HomeEndpointTests(unittest.TestCase):
    def test_for_you_uses_home_url(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/HomeTimeline",
                    _load("graphql_home.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(home.fetch(page, feed="for_you", limit=5))
        self.assertEqual(page.goto_calls, ["https://x.com/home"])
        self.assertEqual([t.tweet_id for t in tweets], ["3333", "4444"])

    def test_following_uses_latest_url(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/HomeLatestTimeline",
                    _load("graphql_home.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(home.fetch(page, feed="following", limit=5))
        self.assertEqual(page.goto_calls, ["https://x.com/home?f=following"])
        self.assertEqual(len(tweets), 2)

    def test_invalid_feed_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(home.fetch(page, feed="garbage"))


class SearchEndpointTests(unittest.TestCase):
    def test_top_mode_navigates_correctly(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/SearchTimeline",
                    _load("graphql_search.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(search.fetch(page, query="claude code", mode="top"))
        self.assertEqual(len(tweets), 1)
        self.assertIn("q=claude+code", page.goto_calls[0])
        self.assertIn("f=top", page.goto_calls[0])

    def test_latest_mode_uses_live_filter(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/SearchTimeline",
                    _load("graphql_search.json"),
                )
            ]
        )
        with _no_sleep_patch():
            _run(search.fetch(page, query="anthropic", mode="latest"))
        self.assertIn("f=live", page.goto_calls[0])

    def test_invalid_mode_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(search.fetch(page, query="x", mode="bad"))

    def test_empty_query_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(search.fetch(page, query="   "))


class TweetEndpointTests(unittest.TestCase):
    def test_focal_separated_from_replies(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/TweetDetail",
                    _load("graphql_tweet_detail.json"),
                )
            ]
        )
        focal, replies = _run(tweet.fetch(page, id_or_url="8000"))
        assert focal is not None
        self.assertEqual(focal.tweet_id, "8000")
        self.assertEqual([r.tweet_id for r in replies], ["8001", "8002"])

    def test_include_replies_false_drops_replies(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/TweetDetail",
                    _load("graphql_tweet_detail.json"),
                )
            ]
        )
        _, replies = _run(
            tweet.fetch(page, id_or_url="8000", include_replies=False)
        )
        self.assertEqual(replies, [])

    def test_url_input_extracts_id(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/TweetDetail",
                    _load("graphql_tweet_detail.json"),
                )
            ]
        )
        focal, _ = _run(
            tweet.fetch(page, id_or_url="https://x.com/frank/status/8000")
        )
        assert focal is not None
        self.assertEqual(focal.tweet_id, "8000")
        self.assertIn("frank/status/8000", page.goto_calls[0])

    def test_invalid_input_raises(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(tweet.fetch(page, id_or_url="not-a-tweet"))

    def test_returns_none_on_missing_payload(self) -> None:
        page = FakePage(responses=[])
        with mock.patch.object(scraper_mod, "SINGLE_FETCH_TIMEOUT_S", 0.05):
            focal, replies = _run(tweet.fetch(page, id_or_url="8000"))
        self.assertIsNone(focal)
        self.assertEqual(replies, [])


class UserEndpointTests(unittest.TestCase):
    def test_profile_fetch(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/UserByScreenName",
                    _load("graphql_user_profile.json"),
                )
            ]
        )
        profile = _run(user.fetch_profile(page, handle="ivy"))
        assert profile is not None
        self.assertEqual(profile.handle, "ivy")
        self.assertEqual(profile.followers_count, 12345)

    def test_strips_at_prefix(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/UserByScreenName",
                    _load("graphql_user_profile.json"),
                )
            ]
        )
        _run(user.fetch_profile(page, handle="@ivy"))
        self.assertEqual(page.goto_calls, ["https://x.com/ivy"])

    def test_invalid_handle_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(user.fetch_profile(page, handle="bad handle!"))

    def test_user_tweets_basic(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/UserTweets",
                    _load("graphql_user_tweets.json"),
                )
            ]
        )
        with _no_sleep_patch():
            tweets = _run(user.fetch_tweets(page, handle="eve"))
        self.assertEqual([t.tweet_id for t in tweets], ["7000", "7001"])
        self.assertEqual(page.goto_calls, ["https://x.com/eve"])

    def test_user_tweets_with_replies_changes_url(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/UserTweetsAndReplies",
                    _load("graphql_user_tweets.json"),
                )
            ]
        )
        with _no_sleep_patch():
            _run(user.fetch_tweets(page, handle="eve", include_replies=True))
        self.assertEqual(page.goto_calls, ["https://x.com/eve/with_replies"])

    def test_user_tweets_media_only(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/UserMedia",
                    _load("graphql_user_tweets.json"),
                )
            ]
        )
        with _no_sleep_patch():
            _run(user.fetch_tweets(page, handle="eve", include_media_only=True))
        self.assertEqual(page.goto_calls, ["https://x.com/eve/media"])


class TrendsEndpointTests(unittest.TestCase):
    def test_returns_trends_for_default_category(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/GenericTimelineById",
                    _load("graphql_trends.json"),
                )
            ]
        )
        with _no_sleep_patch():
            result = _run(trends.fetch(page, category="trending", limit=10))
        self.assertEqual([t.name for t in result], ["Claude 4.7", "Real Madrid", "#NewYearsDay"])
        self.assertEqual(page.goto_calls, ["https://x.com/explore/tabs/trending"])

    def test_each_category_has_distinct_url(self) -> None:
        for category, url in trends.URL_BY_CATEGORY.items():
            page = FakePage(
                responses=[
                    FakeResponse(
                        "https://x.com/i/api/graphql/abc/GenericTimelineById",
                        _load("graphql_trends.json"),
                    )
                ]
            )
            with _no_sleep_patch():
                _run(trends.fetch(page, category=category, limit=1))
            self.assertEqual(page.goto_calls, [url], category)

    def test_invalid_category_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(trends.fetch(page, category="garbage"))


class SocialGraphEndpointTests(unittest.TestCase):
    def _page_with_users(self, fragment: str) -> FakePage:
        return FakePage(
            responses=[
                FakeResponse(
                    f"https://x.com/i/api/graphql/abc{fragment}",
                    _load("graphql_user_list.json"),
                )
            ]
        )

    def test_followers_navigates_followers_page(self) -> None:
        page = self._page_with_users(social_graph.FOLLOWERS_FRAGMENT)
        with _no_sleep_patch():
            users = _run(social_graph.fetch_followers(page, handle="ivy"))
        self.assertEqual([u.handle for u in users], ["alice", "bob"])
        self.assertEqual(page.goto_calls, ["https://x.com/ivy/followers"])

    def test_following_navigates_following_page(self) -> None:
        page = self._page_with_users(social_graph.FOLLOWING_FRAGMENT)
        with _no_sleep_patch():
            users = _run(social_graph.fetch_following(page, handle="ivy"))
        self.assertEqual(len(users), 2)
        self.assertEqual(page.goto_calls, ["https://x.com/ivy/following"])

    def test_likers_navigates_to_likes_subpath(self) -> None:
        page = self._page_with_users(social_graph.FAVORITERS_FRAGMENT)
        with _no_sleep_patch():
            users = _run(social_graph.fetch_likers(page, id_or_url="9999"))
        self.assertEqual(len(users), 2)
        self.assertEqual(page.goto_calls, ["https://x.com/i/web/status/9999/likes"])

    def test_likers_uses_handle_path_when_url_input(self) -> None:
        page = self._page_with_users(social_graph.FAVORITERS_FRAGMENT)
        with _no_sleep_patch():
            _run(
                social_graph.fetch_likers(
                    page, id_or_url="https://x.com/frank/status/9999"
                )
            )
        self.assertEqual(page.goto_calls, ["https://x.com/frank/status/9999/likes"])

    def test_retweeters_navigates_to_retweets_subpath(self) -> None:
        page = self._page_with_users(social_graph.RETWEETERS_FRAGMENT)
        with _no_sleep_patch():
            users = _run(social_graph.fetch_retweeters(page, id_or_url="9999"))
        self.assertEqual(len(users), 2)
        self.assertEqual(page.goto_calls, ["https://x.com/i/web/status/9999/retweets"])

    def test_invalid_handle_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(social_graph.fetch_followers(page, handle="bad handle!"))

    def test_invalid_tweet_id_rejected(self) -> None:
        page = FakePage()
        with self.assertRaises(ValueError):
            _run(social_graph.fetch_likers(page, id_or_url="nope"))


class ThreadEndpointTests(unittest.TestCase):
    def test_filters_to_focal_author_only(self) -> None:
        page = FakePage(
            responses=[
                FakeResponse(
                    "https://x.com/i/api/graphql/abc/TweetDetail",
                    _load("graphql_tweet_thread.json"),
                )
            ]
        )
        focal, thread = _run(tweet.fetch_thread(page, id_or_url="9000"))
        assert focal is not None
        self.assertEqual(focal.tweet_id, "9000")
        self.assertEqual([t.tweet_id for t in thread], ["9001", "9003"])
        self.assertTrue(all(t.author_handle == "frank" for t in thread))

    def test_returns_empty_when_focal_missing(self) -> None:
        page = FakePage(responses=[])
        with mock.patch.object(scraper_mod, "SINGLE_FETCH_TIMEOUT_S", 0.05):
            focal, thread = _run(tweet.fetch_thread(page, id_or_url="9999"))
        self.assertIsNone(focal)
        self.assertEqual(thread, [])


class ArticlePage:
    """Mock for the article DOM-extraction path (no GraphQL)."""

    def __init__(
        self,
        *,
        url: str = "https://x.com/i/article/123",
        body_text: str = "Article body",
        title: str = "Article Title",
        selector_found: bool = True,
        blocks: list[dict[str, Any]] | None = None,
        cover_src: str = "",
        blocks_raise: bool = False,
    ) -> None:
        self.url = url
        self._body_text = body_text
        self._title = title
        self._selector_found = selector_found
        self._blocks = blocks or []
        self._cover_src = cover_src
        self._blocks_raise = blocks_raise
        self.goto_calls: list[str] = []

    async def goto(self, url: str, **_: Any) -> None:
        self.goto_calls.append(url)
        self.url = url

    async def wait_for_selector(self, selector: str, *, timeout: int = 0) -> None:
        if not self._selector_found:
            raise TimeoutError("selector not found")

    async def inner_text(self, selector: str) -> str:
        if selector == article.TITLE_SELECTOR:
            return self._title
        return self._body_text

    async def evaluate(self, script: str) -> Any:
        if self._blocks_raise:
            raise RuntimeError("evaluate failed")
        return {
            "title": self._title,
            "coverSrc": self._cover_src,
            "blocks": self._blocks,
        }


class ArticleEndpointTests(unittest.TestCase):
    def test_returns_full_article(self) -> None:
        page = ArticlePage(
            body_text="Hello article",
            title="My Title",
            blocks=[
                {"type": "text", "tag": "P", "text": "Hello article",
                 "fs": 17, "html": "Hello article"},
            ],
        )
        result = _run(article.fetch(page, id_or_url="1846123456789012345"))
        assert result is not None
        self.assertEqual(result.article_id, "1846123456789012345")
        self.assertEqual(result.url, "https://x.com/i/article/1846123456789012345")
        self.assertEqual(result.title, "My Title")
        self.assertEqual(result.body_text, "Hello article")
        self.assertEqual(result.char_count, len("Hello article"))
        # body_html is now a standalone rendered HTML document
        self.assertIn("<!DOCTYPE html>", result.body_html)
        self.assertIn("<h1>My Title</h1>", result.body_html)
        self.assertIn("<p>Hello article</p>", result.body_html)

    def test_url_input_extracts_id(self) -> None:
        page = ArticlePage()
        result = _run(
            article.fetch(
                page,
                id_or_url="https://x.com/i/article/1846123456789012345?foo=bar",
            )
        )
        assert result is not None
        self.assertEqual(result.article_id, "1846123456789012345")

    def test_invalid_input_raises(self) -> None:
        page = ArticlePage()
        with self.assertRaises(ValueError):
            _run(article.fetch(page, id_or_url="not-an-article"))

    def test_returns_none_when_reader_does_not_render(self) -> None:
        page = ArticlePage(selector_found=False)
        result = _run(article.fetch(page, id_or_url="123"))
        self.assertIsNone(result)

    def test_extracts_article_media_and_renders_markdown(self) -> None:
        page = ArticlePage(
            title="My Article",
            cover_src="https://pbs.twimg.com/media/cover.jpg",
            blocks=[
                {"type": "text", "tag": "P", "text": "Intro paragraph.",
                 "fs": 17,
                 "html": 'Intro <span style="font-weight: bold;">paragraph</span>.'},
                {"type": "image", "src": "https://pbs.twimg.com/media/a.jpg"},
                # X renders headings as oversized divs, not <h*> tags:
                {"type": "text", "tag": "DIV", "text": "A Section", "fs": 26,
                 "html": "A Section"},
                {"type": "image", "src": "blob:https://x.com/preview"},  # filtered
                {"type": "image",  # avatar — filtered
                 "src": "https://pbs.twimg.com/profile_images/9/x.jpg"},
                {"type": "image", "src": "https://pbs.twimg.com/media/a.jpg"},  # dup
            ],
        )
        result = _run(article.fetch(page, id_or_url="123"))
        assert result is not None
        # cover first, then the unique body image; avatar/blob/dup dropped
        self.assertEqual(
            [m.url for m in result.media],
            [
                "https://pbs.twimg.com/media/cover.jpg",
                "https://pbs.twimg.com/media/a.jpg",
            ],
        )
        md = result.body_markdown
        self.assertIn("# My Article", md)
        self.assertIn("![cover](https://pbs.twimg.com/media/cover.jpg)", md)
        self.assertIn("Intro paragraph.", md)
        self.assertIn("## A Section", md)
        self.assertIn("![image](https://pbs.twimg.com/media/a.jpg)", md)
        self.assertNotIn("profile_images", md)
        self.assertNotIn("blob:", md)
        # paragraph precedes the image, image precedes the heading (order kept)
        self.assertLess(md.index("Intro paragraph."), md.index("media/a.jpg"))
        self.assertLess(md.index("media/a.jpg"), md.index("## A Section"))

        html = result.body_html
        self.assertIn("<!DOCTYPE html>", html)
        self.assertIn("<h1>My Article</h1>", html)
        self.assertIn("<h2>A Section</h2>", html)
        self.assertIn(
            '<img src="https://pbs.twimg.com/media/cover.jpg" alt="cover">', html
        )
        self.assertIn(
            '<img src="https://pbs.twimg.com/media/a.jpg" alt="image">', html
        )
        # inline bold survives via the leaf's own innerHTML
        self.assertIn('<span style="font-weight: bold;">paragraph</span>', html)
        self.assertNotIn("profile_images", html)
        self.assertNotIn("blob:", html)

    def test_article_block_extraction_failure_falls_back_to_body_text(self) -> None:
        page = ArticlePage(blocks_raise=True, body_text="Plain fallback body")
        result = _run(article.fetch(page, id_or_url="123"))
        assert result is not None
        self.assertEqual(result.media, ())
        self.assertIn("Plain fallback body", result.body_markdown)
        self.assertIn("Plain fallback body", result.body_html)


def _media_item(kind: str = "photo") -> MediaItem:
    if kind == "photo":
        return MediaItem(
            kind="photo",
            url="https://pbs.twimg.com/media/a.jpg",
            extension="jpg",
        )
    return MediaItem(
        kind="video",
        url="https://video.twimg.com/v.mp4",
        extension="mp4",
        duration_ms=39000,
    )


def _tweet_with(
    tweet_id: str,
    *,
    media: tuple[MediaItem, ...] = (),
    quoted: QuotedTweet | None = None,
) -> Tweet:
    return Tweet(
        tweet_id=tweet_id,
        created_at="2026-01-01T00:00:00+00:00",
        author_handle="frank",
        author_name="Frank",
        author_id="1",
        text="t",
        is_long_form=False,
        likes=0,
        retweets=0,
        replies=0,
        quote_count=0,
        lang="en",
        hashtags=(),
        mentions=(),
        urls=(),
        media=media,
        x_article=None,
        quoted=quoted,
    )


def _quoted_stub(tweet_id: str) -> QuotedTweet:
    return QuotedTweet(
        tweet_id=tweet_id,
        author_handle="bob",
        author_name="Bob",
        text="quoted",
        urls=(),
        x_article=None,
    )


class MediaEndpointTests(unittest.TestCase):
    def test_resolve_tweet_media_returns_focal_media(self) -> None:
        focal = _tweet_with("8000", media=(_media_item("photo"), _media_item("video")))
        with mock.patch.object(
            tweet, "fetch", new=mock.AsyncMock(return_value=(focal, []))
        ):
            sid, kind, items = _run(
                media.resolve_tweet_media(object(), id_or_url="8000")
            )
        self.assertEqual((sid, kind), ("8000", "tweet"))
        self.assertEqual([m.kind for m in items], ["photo", "video"])

    def test_resolve_tweet_media_from_quoted_refetches(self) -> None:
        focal = _tweet_with("8000", media=(), quoted=_quoted_stub("9001"))
        quoted_full = _tweet_with("9001", media=(_media_item("photo"),))
        fetch = mock.AsyncMock(side_effect=[(focal, []), (quoted_full, [])])
        with mock.patch.object(tweet, "fetch", new=fetch):
            sid, kind, items = _run(
                media.resolve_tweet_media(
                    object(), id_or_url="8000", from_quoted=True
                )
            )
        self.assertEqual((sid, kind), ("9001", "tweet"))
        self.assertEqual(len(items), 1)
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(fetch.call_args_list[1].kwargs["id_or_url"], "9001")

    def test_resolve_tweet_media_from_quoted_without_quote_raises(self) -> None:
        focal = _tweet_with("8000", media=(), quoted=None)
        with mock.patch.object(
            tweet, "fetch", new=mock.AsyncMock(return_value=(focal, []))
        ):
            with self.assertRaises(ValueError):
                _run(
                    media.resolve_tweet_media(
                        object(), id_or_url="8000", from_quoted=True
                    )
                )

    def test_resolve_tweet_media_deleted_tweet_raises(self) -> None:
        with mock.patch.object(
            tweet, "fetch", new=mock.AsyncMock(return_value=(None, []))
        ):
            with self.assertRaises(ValueError):
                _run(media.resolve_tweet_media(object(), id_or_url="8000"))

    def test_resolve_article_media_returns_article(self) -> None:
        art = Article(
            article_id="123",
            url="https://x.com/i/article/123",
            title="t",
            body_text="b",
            body_html="h",
            char_count=1,
            media=(_media_item("photo"),),
            body_markdown="# t\n\n![image](https://pbs.twimg.com/media/a.jpg)\n",
        )
        with mock.patch.object(
            article, "fetch", new=mock.AsyncMock(return_value=art)
        ):
            result = _run(media.resolve_article_media(object(), id_or_url="123"))
        self.assertIs(result, art)
        self.assertEqual(result.article_id, "123")
        self.assertEqual(len(result.media), 1)

    def test_resolve_article_media_unrendered_raises(self) -> None:
        with mock.patch.object(
            article, "fetch", new=mock.AsyncMock(return_value=None)
        ):
            with self.assertRaises(ValueError):
                _run(media.resolve_article_media(object(), id_or_url="123"))


if __name__ == "__main__":
    unittest.main()
