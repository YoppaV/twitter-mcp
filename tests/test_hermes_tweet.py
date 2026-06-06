import os
import unittest
from unittest.mock import patch

import httpx

from twitter_sdk import hermes_tweet


class HermesTweetTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_builds_request_and_normalizes_tweets(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                json={
                    "tweets": [
                        {
                            "tweet_id": "123",
                            "full_text": "Hermes Agent notes",
                            "createdAt": "2026-06-06T11:00:00Z",
                            "author": {
                                "id": "u1",
                                "screen_name": "hermes",
                                "name": "Hermes",
                            },
                            "metrics": {"likes": "4", "retweets": 2},
                        }
                    ]
                },
            )

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            with patch.dict(
                os.environ,
                {
                    "HERMES_TWEET_API_KEY": "example_key",
                    "HERMES_TWEET_BASE_URL": "https://api.example.test/",
                },
                clear=True,
            ):
                tweets = await hermes_tweet.search(
                    query="Hermes Agent",
                    mode="latest",
                    limit=5,
                    client=client,
                )

        self.assertEqual(len(requests), 1)
        request = requests[0]
        self.assertEqual(
            str(request.url),
            "https://api.example.test/api/v1/x/tweets/search"
            "?q=Hermes+Agent&limit=5&queryType=Latest",
        )
        self.assertEqual(request.headers["x-api-key"], "example_key")
        self.assertEqual(tweets[0].tweet_id, "123")
        self.assertEqual(tweets[0].author_handle, "hermes")
        self.assertEqual(tweets[0].likes, 4)

    async def test_search_requires_key(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(RuntimeError):
                await hermes_tweet.search(query="Hermes Agent", mode="top", limit=5)


if __name__ == "__main__":
    unittest.main()
