"""Optional Hermes Tweet read backend for search results."""

from __future__ import annotations

import os
from typing import Any, Mapping

import httpx

from .models import Tweet

DEFAULT_BASE_URL = "https://api.xquik.com"
SEARCH_PATH = "/api/v1/x/tweets/search"


def is_configured() -> bool:
    return bool(_api_key())


async def search(
    *,
    query: str,
    mode: str,
    limit: int | None,
    client: httpx.AsyncClient | None = None,
) -> list[Tweet]:
    api_key = _api_key()
    if not api_key:
        raise RuntimeError("HERMES_TWEET_API_KEY or XQUIK_API_KEY is required.")

    params: dict[str, Any] = {
        "q": query,
        "limit": limit or 50,
        "queryType": "Latest" if mode == "latest" else "Top",
    }

    close_client = client is None
    active_client = client or httpx.AsyncClient(timeout=30.0, follow_redirects=True)
    try:
        response = await active_client.get(
            f"{_base_url()}{SEARCH_PATH}",
            params=params,
            headers={
                "accept": "application/json",
                "authorization": f"Bearer {api_key}",
                "x-api-key": api_key,
            },
        )
        response.raise_for_status()
        return [_normalize_tweet(item) for item in _extract_records(response.json())]
    finally:
        if close_client:
            await active_client.aclose()


def _api_key() -> str | None:
    return os.getenv("HERMES_TWEET_API_KEY") or os.getenv("XQUIK_API_KEY")


def _base_url() -> str:
    configured = os.getenv("HERMES_TWEET_BASE_URL") or os.getenv("XQUIK_BASE_URL")
    return (configured or DEFAULT_BASE_URL).rstrip("/")


def _extract_records(payload: Any) -> list[Mapping[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, Mapping)]
    if not isinstance(payload, Mapping):
        return []
    for key in ("tweets", "data", "results", "items"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, Mapping)]
    nested = payload.get("result")
    if isinstance(nested, Mapping):
        return _extract_records(nested)
    return []


def _normalize_tweet(record: Mapping[str, Any]) -> Tweet:
    author = record.get("author") or record.get("user") or {}
    if not isinstance(author, Mapping):
        author = {}

    metrics = record.get("metrics") or {}
    if not isinstance(metrics, Mapping):
        metrics = {}
    public_metrics = record.get("public_metrics") or {}
    if not isinstance(public_metrics, Mapping):
        public_metrics = {}

    return Tweet(
        tweet_id=_string(record.get("id") or record.get("tweet_id")),
        created_at=_string(record.get("created_at") or record.get("createdAt")),
        author_handle=_string(author.get("username") or author.get("screen_name")),
        author_name=_string(
            author.get("name") or author.get("username") or author.get("screen_name")
        ),
        author_id=_string(
            record.get("author_id") or record.get("authorId") or author.get("id")
        ),
        text=_string(record.get("text") or record.get("full_text")),
        is_long_form=False,
        likes=_number(metrics.get("likes") or public_metrics.get("like_count")),
        retweets=_number(metrics.get("retweets") or public_metrics.get("retweet_count")),
        replies=_number(metrics.get("replies") or public_metrics.get("reply_count")),
        quote_count=_number(metrics.get("quotes") or public_metrics.get("quote_count")),
        lang=_string(record.get("lang")),
        hashtags=(),
        mentions=(),
        urls=(),
        media=(),
        x_article=None,
        quoted=None,
    )


def _string(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _number(value: Any) -> int:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdecimal():
        return int(value)
    return 0
