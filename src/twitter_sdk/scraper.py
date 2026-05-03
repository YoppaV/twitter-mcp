"""Generic GraphQL-intercept + scroll loop reused by every list endpoint.

The shape: navigate to a Twitter web URL, install a ``page.on("response", ...)``
handler that filters by GraphQL fragment name and feeds matching payloads to a
parser, then scroll until the limit is reached or the timeline goes quiet.

Single-fetch endpoints (TweetDetail, UserByScreenName) use
``intercept_single_response`` instead — same handler pattern but no scrolling.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING, Any, Awaitable, Callable, TypeVar

from .auth import SessionExpiredError, is_login_redirect

if TYPE_CHECKING:
    from playwright.async_api import Page, Response

SCROLL_DELAY_RANGE_S: tuple[float, float] = (2.0, 5.0)
SCROLL_PIXELS = 3000
DEFAULT_MAX_SCROLLS = 200
EMPTY_SCROLLS_BEFORE_STOP = 15
SINGLE_FETCH_TIMEOUT_S = 20.0


T = TypeVar("T")
Extractor = Callable[[dict[str, Any]], list[T]]


def _default_key_of(item: Any) -> str:
    """Default key extractor: use ``tweet_id`` (stable across timeline shapes)."""
    return getattr(item, "tweet_id", "") or ""


def _make_response_handler(
    fragment: str,
    extractor: Extractor[T],
    collected: dict[str, T],
    order: list[str],
    key_of: Callable[[T], str],
) -> Callable[["Response"], Awaitable[None]]:
    async def on_response(response: "Response") -> None:
        if fragment not in response.url:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        for item in extractor(payload):
            key = key_of(item)
            if not key or key in collected:
                continue
            collected[key] = item
            order.append(key)

    return on_response


async def _sleep_jitter(rng: tuple[float, float]) -> None:
    await asyncio.sleep(random.uniform(*rng))


async def scroll_collect(
    page: "Page",
    initial_url: str,
    fragment: str,
    extractor: Extractor[T],
    *,
    limit: int | None = None,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    empty_streak_stop: int = EMPTY_SCROLLS_BEFORE_STOP,
    since_id: str | None = None,
    key_of: Callable[[T], str] | None = None,
) -> list[T]:
    """Scroll ``initial_url`` and collect items parsed from ``fragment``.

    Stops when ``limit`` reached, when scrolling produces no new items for
    ``empty_streak_stop`` iterations, when ``since_id`` is found, or when
    ``max_scrolls`` is exhausted. ``key_of`` defaults to ``item.tweet_id``;
    pass a different extractor for User/Trend timelines.
    """
    if key_of is None:
        key_of = _default_key_of
    collected: dict[str, T] = {}
    order: list[str] = []

    handler = _make_response_handler(
        fragment, extractor, collected, order, key_of=key_of
    )
    page.on("response", handler)

    try:
        await page.goto(initial_url, wait_until="domcontentloaded")
        if is_login_redirect(page.url):
            raise SessionExpiredError(
                "Twitter session expired — re-run: python -m scripts.auth_login"
            )

        # Initial GraphQL fires on navigation; give it a moment.
        await _sleep_jitter(SCROLL_DELAY_RANGE_S)
        if _should_stop(collected, order, limit, since_id):
            return _materialize(collected, order, limit, since_id)

        empty_streak = 0
        for _ in range(max_scrolls):
            before = len(collected)
            await page.evaluate(f"window.scrollBy(0, {SCROLL_PIXELS})")
            await _sleep_jitter(SCROLL_DELAY_RANGE_S)
            if _should_stop(collected, order, limit, since_id):
                break
            if len(collected) == before:
                empty_streak += 1
                if empty_streak >= empty_streak_stop:
                    break
            else:
                empty_streak = 0
    finally:
        try:
            page.remove_listener("response", handler)
        except Exception:
            pass

    return _materialize(collected, order, limit, since_id)


def _should_stop(
    collected: dict[str, T],
    order: list[str],
    limit: int | None,
    since_id: str | None,
) -> bool:
    if limit is not None and len(collected) >= limit:
        return True
    if since_id is not None and since_id in collected:
        return True
    return False


def _materialize(
    collected: dict[str, T],
    order: list[str],
    limit: int | None,
    since_id: str | None,
) -> list[T]:
    """Return items in observed order, applying limit and since_id boundary."""
    items: list[T] = []
    for key in order:
        if since_id is not None and key == since_id:
            break
        items.append(collected[key])
        if limit is not None and len(items) >= limit:
            break
    return items


async def intercept_single_response(
    page: "Page",
    initial_url: str,
    fragment: str,
    extractor: Callable[[dict[str, Any]], T | list[T]],
    *,
    timeout_s: float | None = None,
) -> T | list[T] | None:
    """Navigate once and return the first matching GraphQL payload's parse.

    Used by endpoints that don't scroll (TweetDetail, UserByScreenName).
    Returns ``None`` if the fragment wasn't observed within ``timeout_s``.
    Looks up ``SINGLE_FETCH_TIMEOUT_S`` from the module at call time so tests
    can monkey-patch the default.
    """
    if timeout_s is None:
        timeout_s = SINGLE_FETCH_TIMEOUT_S
    result_holder: list[T | list[T]] = []
    done = asyncio.Event()

    async def on_response(response: "Response") -> None:
        if done.is_set():
            return
        if fragment not in response.url:
            return
        try:
            payload = await response.json()
        except Exception:
            return
        parsed = extractor(payload)
        if parsed is None:
            return
        if isinstance(parsed, list) and not parsed:
            return
        result_holder.append(parsed)
        done.set()

    page.on("response", on_response)
    try:
        await page.goto(initial_url, wait_until="domcontentloaded")
        if is_login_redirect(page.url):
            raise SessionExpiredError(
                "Twitter session expired — re-run: python -m scripts.auth_login"
            )
        try:
            await asyncio.wait_for(done.wait(), timeout=timeout_s)
        except asyncio.TimeoutError:
            return None
    finally:
        try:
            page.remove_listener("response", on_response)
        except Exception:
            pass

    return result_holder[0] if result_holder else None
