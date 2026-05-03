"""Unit tests for twitter_sdk.parsers.

Each test loads a captured (or synthesized) GraphQL fixture from
``tests/fixtures/graphql_*.json`` and asserts the extractor returns the
expected Tweet/User objects. Pure CPU, no Playwright, no network.

Run with: ``venv/bin/python -m unittest tests.test_parsers -v``
"""

from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "src"))

from twitter_sdk import parsers
from twitter_sdk.models import Tweet, User

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class BookmarksParserTests(unittest.TestCase):
    def test_extracts_tweets_in_order(self) -> None:
        payload = _load("graphql_bookmarks.json")
        tweets = parsers.extract_bookmarks(payload)
        ids = [t.tweet_id for t in tweets]
        self.assertEqual(ids, ["1111", "2222"])

    def test_tweet_fields_populated(self) -> None:
        payload = _load("graphql_bookmarks.json")
        first = parsers.extract_bookmarks(payload)[0]
        self.assertIsInstance(first, Tweet)
        self.assertEqual(first.author_handle, "alice")
        self.assertEqual(first.author_name, "Alice")
        self.assertEqual(first.text, "Hello world from Alice")
        self.assertEqual(first.likes, 5)
        self.assertEqual(first.retweets, 1)
        self.assertEqual(first.lang, "en")
        self.assertTrue(first.created_at.startswith("2026-04-13"))

    def test_hashtags_and_mentions(self) -> None:
        second = parsers.extract_bookmarks(_load("graphql_bookmarks.json"))[1]
        self.assertEqual(second.hashtags, ("tag1",))
        self.assertEqual(second.mentions, ("alice",))


class HomeParserTests(unittest.TestCase):
    def test_skips_promoted_keeps_module(self) -> None:
        tweets = parsers.extract_home(_load("graphql_home.json"))
        ids = [t.tweet_id for t in tweets]
        self.assertEqual(ids, ["3333", "4444"])

    def test_handles_missing_root(self) -> None:
        self.assertEqual(parsers.extract_home({}), [])


class SearchParserTests(unittest.TestCase):
    def test_extracts_search_result(self) -> None:
        tweets = parsers.extract_search(_load("graphql_search.json"))
        self.assertEqual(len(tweets), 1)
        self.assertEqual(tweets[0].author_handle, "anthropicai")
        self.assertEqual(tweets[0].likes, 1000)


class UserTweetsParserTests(unittest.TestCase):
    def test_pinned_then_chronological(self) -> None:
        tweets = parsers.extract_user_tweets(_load("graphql_user_tweets.json"))
        self.assertEqual([t.tweet_id for t in tweets], ["7000", "7001"])
        self.assertEqual(tweets[0].text, "Pinned tweet")


class TweetDetailParserTests(unittest.TestCase):
    def test_focal_then_replies(self) -> None:
        tweets = parsers.extract_tweet_detail(_load("graphql_tweet_detail.json"))
        ids = [t.tweet_id for t in tweets]
        self.assertEqual(ids, ["8000", "8001", "8002"])

    def test_returns_empty_on_missing_root(self) -> None:
        self.assertEqual(parsers.extract_tweet_detail({"data": {}}), [])


class UserProfileParserTests(unittest.TestCase):
    def test_full_profile_extraction(self) -> None:
        user = parsers.extract_user_profile(_load("graphql_user_profile.json"))
        assert user is not None
        self.assertIsInstance(user, User)
        self.assertEqual(user.handle, "ivy")
        self.assertEqual(user.name, "Ivy")
        self.assertEqual(user.user_id, "100")
        self.assertEqual(user.bio, "I write things")
        self.assertEqual(user.followers_count, 12345)
        self.assertEqual(user.following_count, 123)
        self.assertEqual(user.tweet_count, 4567)
        self.assertEqual(user.location, "Madrid")
        self.assertEqual(user.url, "https://ivy.example.com")
        self.assertEqual(user.pinned_tweet_id, "7000")
        self.assertTrue(user.verified)
        self.assertFalse(user.protected)

    def test_returns_none_when_missing(self) -> None:
        self.assertIsNone(parsers.extract_user_profile({"data": {}}))


class CreatedAtParserTests(unittest.TestCase):
    def test_iso_round_trip(self) -> None:
        out = parsers.parse_created_at("Mon Apr 13 01:05:36 +0000 2026")
        self.assertTrue(out.startswith("2026-04-13T01:05:36"))

    def test_passthrough_on_unparseable(self) -> None:
        self.assertEqual(parsers.parse_created_at("nonsense"), "nonsense")
        self.assertEqual(parsers.parse_created_at(""), "")


if __name__ == "__main__":
    unittest.main()
