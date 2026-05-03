"""End-to-end tests for the FastMCP server with mocked browser/endpoints.

We import the server module, replace its ``_browser`` with a mock that yields a
fake page, and patch the endpoint modules to return canned values. Asserts that
the registered tools serialize results into plain dicts (which is what MCP
clients see).

Run with: ``venv/bin/python -m unittest tests.test_server -v``
"""

from __future__ import annotations

import asyncio
import json
import sys
import unittest
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from twitter_sdk import server as server_mod  # noqa: E402
from twitter_sdk.models import Article, Tweet, User  # noqa: E402

FIXTURES = Path(__file__).parent / "fixtures"


def _make_tweet(tid: str, text: str = "x") -> Tweet:
    return Tweet(
        tweet_id=tid,
        created_at="2026-04-13T00:00:00+00:00",
        author_handle="alice",
        author_name="Alice",
        author_id="10",
        text=text,
        is_long_form=False,
        likes=1,
        retweets=0,
        replies=0,
        quote_count=0,
        lang="en",
        hashtags=(),
        mentions=(),
        urls=(),
        media=(),
        x_article=None,
        quoted=None,
    )


def _make_user() -> User:
    return User(
        user_id="100",
        handle="ivy",
        name="Ivy",
        bio="bio",
        location="Madrid",
        url="",
        created_at="2022-01-03T12:00:00+00:00",
        verified=True,
        protected=False,
        followers_count=10,
        following_count=5,
        tweet_count=100,
        listed_count=1,
        profile_image_url="",
        pinned_tweet_id=None,
    )


class FakeBrowser:
    @asynccontextmanager
    async def page(self) -> Any:
        yield object()  # opaque — endpoints are mocked, never touch the page


def _run(coro: Any) -> Any:
    return asyncio.run(coro)


def _patch_browser() -> mock._patch:
    return mock.patch.object(server_mod, "_browser", FakeBrowser())


class AuthStatusToolTests(unittest.TestCase):
    def test_reports_session_summary(self) -> None:
        with mock.patch.object(server_mod, "_session_path", Path("/nope")):
            result = _run(server_mod.auth_status())
        self.assertFalse(result["authenticated"])
        self.assertEqual(result["session_path"], "/nope")


class GetBookmarksToolTests(unittest.TestCase):
    def test_serializes_to_dicts(self) -> None:
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.bookmarks.fetch",
            new=mock.AsyncMock(return_value=[_make_tweet("1"), _make_tweet("2")]),
        ):
            result = _run(server_mod.get_bookmarks(limit=10))

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0]["tweet_id"], "1")
        # Round-trip through JSON to confirm everything serializes cleanly.
        json.dumps(result)


class GetHomeTimelineToolTests(unittest.TestCase):
    def test_passes_feed_arg(self) -> None:
        fetch = mock.AsyncMock(return_value=[_make_tweet("3")])
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.home.fetch", new=fetch
        ):
            result = _run(
                server_mod.get_home_timeline(limit=5, feed="following")
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(fetch.call_args.kwargs["feed"], "following")
        self.assertEqual(fetch.call_args.kwargs["limit"], 5)


class GetTweetToolTests(unittest.TestCase):
    def test_returns_focal_and_replies(self) -> None:
        focal = _make_tweet("8000", "focal")
        replies = [_make_tweet("8001"), _make_tweet("8002")]
        fetch = mock.AsyncMock(return_value=(focal, replies))
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.tweet.fetch", new=fetch
        ):
            result = _run(server_mod.get_tweet(id_or_url="8000"))
        self.assertEqual(result["focal"]["tweet_id"], "8000")
        self.assertEqual([r["tweet_id"] for r in result["replies"]], ["8001", "8002"])


class SearchTweetsToolTests(unittest.TestCase):
    def test_passes_query_and_mode(self) -> None:
        fetch = mock.AsyncMock(return_value=[_make_tweet("5")])
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.search.fetch", new=fetch
        ):
            result = _run(
                server_mod.search_tweets(query="anthropic", mode="latest", limit=3)
            )
        self.assertEqual(len(result), 1)
        self.assertEqual(fetch.call_args.kwargs["query"], "anthropic")
        self.assertEqual(fetch.call_args.kwargs["mode"], "latest")


class GetUserProfileToolTests(unittest.TestCase):
    def test_returns_user_dict(self) -> None:
        fetch = mock.AsyncMock(return_value=_make_user())
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.user.fetch_profile", new=fetch
        ):
            result = _run(server_mod.get_user_profile(handle="ivy"))
        self.assertEqual(result["handle"], "ivy")
        self.assertEqual(result["followers_count"], 10)

    def test_none_when_user_missing(self) -> None:
        fetch = mock.AsyncMock(return_value=None)
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.user.fetch_profile", new=fetch
        ):
            result = _run(server_mod.get_user_profile(handle="ghost"))
        self.assertIsNone(result)


class GetUserTweetsToolTests(unittest.TestCase):
    def test_serializes_list(self) -> None:
        fetch = mock.AsyncMock(return_value=[_make_tweet("7000"), _make_tweet("7001")])
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.user.fetch_tweets", new=fetch
        ):
            result = _run(server_mod.get_user_tweets(handle="eve"))
        self.assertEqual([t["tweet_id"] for t in result], ["7000", "7001"])


class GetXArticleToolTests(unittest.TestCase):
    def test_returns_serialized_article(self) -> None:
        article = Article(
            article_id="999",
            url="https://x.com/i/article/999",
            title="Title",
            body_text="Body",
            body_html="<p>Body</p>",
            char_count=4,
        )
        fetch = mock.AsyncMock(return_value=article)
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.article.fetch", new=fetch
        ):
            result = _run(server_mod.get_x_article(id_or_url="999"))
        self.assertEqual(result["article_id"], "999")
        self.assertEqual(result["body_text"], "Body")
        self.assertEqual(result["char_count"], 4)

    def test_returns_none_when_unrendered(self) -> None:
        fetch = mock.AsyncMock(return_value=None)
        with _patch_browser(), mock.patch(
            "twitter_sdk.server.article.fetch", new=fetch
        ):
            result = _run(server_mod.get_x_article(id_or_url="999"))
        self.assertIsNone(result)


class ToolRegistrationTests(unittest.TestCase):
    def test_all_eight_tools_registered(self) -> None:
        tools = _run(server_mod.mcp.list_tools())
        names = {t.name for t in tools}
        expected = {
            "auth_status",
            "get_bookmarks",
            "get_home_timeline",
            "get_tweet",
            "search_tweets",
            "get_user_profile",
            "get_user_tweets",
            "get_x_article",
        }
        self.assertEqual(names, expected)


if __name__ == "__main__":
    unittest.main()
