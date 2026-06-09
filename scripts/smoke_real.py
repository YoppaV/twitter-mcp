"""Real-network smoke runner — invokes every MCP tool against live x.com.

Run with::

    venv/bin/python -m scripts.smoke_real

Each tool runs with conservative limits (≤5 items) so a full pass takes
≈2-4 min and avoids hammering the GraphQL surface. Failures are logged but
do not abort subsequent checks — the final table summarises PASS/FAIL per tool.

Probe targets are deliberately public, stable accounts:
- ``@AnthropicAI`` for profile / tweets / followers / following / mentions
- ``@karpathy`` for a second profile probe
- The first bookmark and a tweet derived from it for tweet / thread /
  download_media / get_tweet_quotes / get_liking_users / get_retweeting_users
"""

from __future__ import annotations

import asyncio
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

os.environ.setdefault("BROWSER_HEADLESS", "true")
# Tear chromium down right after the run so the script exits cleanly.
os.environ.setdefault("BROWSER_IDLE_TIMEOUT_S", "0")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from twitter_sdk import server  # noqa: E402

PROBE_HANDLE = "AnthropicAI"
PROBE_HANDLE_2 = "karpathy"
# Public, stable tweet to anchor tweet-scoped tools when no bookmark is found.
FALLBACK_TWEET_URL = "https://x.com/AnthropicAI/status/1798086811373625386"


@dataclass
class CheckResult:
    name: str
    ok: bool
    summary: str
    detail: str = ""


async def run_check(
    name: str,
    fn: Callable[[], Awaitable[Any]],
    summarize: Callable[[Any], str],
) -> CheckResult:
    print(f"  → {name} ...", flush=True)
    try:
        value = await fn()
        return CheckResult(name=name, ok=True, summary=summarize(value))
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name=name,
            ok=False,
            summary=f"{type(exc).__name__}: {exc}",
            detail=traceback.format_exc(),
        )


def n_items(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict) and "replies" in value:
        return len(value.get("replies") or [])
    return 0


async def main() -> int:
    print("Twitter/X MCP — real-network smoke run")
    print(f"Session: {server._session_path}")
    print("=" * 60)

    results: list[CheckResult] = []

    # 1. auth_status — local, no network.
    results.append(
        await run_check(
            "auth_status",
            server.auth_status,
            lambda v: f"authenticated={v.get('authenticated')} handle={v.get('handle')}",
        )
    )

    # 2. get_bookmarks — anchor for downstream tools.
    bookmarks_res = await run_check(
        "get_bookmarks(limit=5)",
        lambda: server.get_bookmarks(limit=5),
        lambda v: f"{len(v)} bookmarks; first id={v[0]['tweet_id'] if v else 'n/a'}",
    )
    results.append(bookmarks_res)
    first_bookmark: dict[str, Any] | None = None
    bookmarks_data = []
    try:
        bookmarks_data = await server.get_bookmarks(limit=5)
        first_bookmark = bookmarks_data[0] if bookmarks_data else None
    except Exception:
        pass

    tweet_url = (
        f"https://x.com/{first_bookmark['author_handle']}/status/{first_bookmark['tweet_id']}"
        if first_bookmark
        else FALLBACK_TWEET_URL
    )
    tweet_id = first_bookmark["tweet_id"] if first_bookmark else FALLBACK_TWEET_URL.rsplit("/", 1)[-1]
    print(f"  (anchor tweet: {tweet_url})")

    # 3. get_home_timeline
    results.append(
        await run_check(
            "get_home_timeline(limit=5, feed=for_you)",
            lambda: server.get_home_timeline(limit=5, feed="for_you"),
            lambda v: f"{len(v)} tweets",
        )
    )

    # 4. get_home_timeline following
    results.append(
        await run_check(
            "get_home_timeline(limit=5, feed=following)",
            lambda: server.get_home_timeline(limit=5, feed="following"),
            lambda v: f"{len(v)} tweets",
        )
    )

    # 5. get_tweet — anchor.
    results.append(
        await run_check(
            "get_tweet(anchor)",
            lambda: server.get_tweet(id_or_url=tweet_url, include_replies=True, include_quote=True),
            lambda v: f"focal={'yes' if v.get('focal') else 'no'} replies={len(v.get('replies') or [])}",
        )
    )

    # 6. search_tweets
    results.append(
        await run_check(
            "search_tweets(query=anthropic, mode=top)",
            lambda: server.search_tweets(query="anthropic", mode="top", limit=5),
            lambda v: f"{len(v)} results",
        )
    )

    # 7. get_user_profile
    results.append(
        await run_check(
            f"get_user_profile({PROBE_HANDLE})",
            lambda: server.get_user_profile(handle=PROBE_HANDLE),
            lambda v: f"handle={v.get('handle') if v else None} followers={v.get('followers_count') if v else None}",
        )
    )

    # 8. get_user_tweets
    results.append(
        await run_check(
            f"get_user_tweets({PROBE_HANDLE}, limit=5)",
            lambda: server.get_user_tweets(handle=PROBE_HANDLE, limit=5),
            lambda v: f"{len(v)} tweets",
        )
    )

    # 9. get_trends
    results.append(
        await run_check(
            "get_trends(category=trending, limit=5)",
            lambda: server.get_trends(category="trending", limit=5),
            lambda v: f"{len(v)} trends; first={v[0]['name'] if v else 'n/a'}",
        )
    )

    # 10. get_user_followers
    results.append(
        await run_check(
            f"get_user_followers({PROBE_HANDLE_2}, limit=5)",
            lambda: server.get_user_followers(handle=PROBE_HANDLE_2, limit=5),
            lambda v: f"{len(v)} followers",
        )
    )

    # 11. get_user_following
    results.append(
        await run_check(
            f"get_user_following({PROBE_HANDLE_2}, limit=5)",
            lambda: server.get_user_following(handle=PROBE_HANDLE_2, limit=5),
            lambda v: f"{len(v)} following",
        )
    )

    # 12. get_user_mentions
    results.append(
        await run_check(
            f"get_user_mentions({PROBE_HANDLE}, limit=5)",
            lambda: server.get_user_mentions(handle=PROBE_HANDLE, limit=5),
            lambda v: f"{len(v)} mentions",
        )
    )

    # 13. get_thread
    results.append(
        await run_check(
            "get_thread(anchor)",
            lambda: server.get_thread(id_or_url=tweet_url),
            lambda v: f"focal={'yes' if v.get('focal') else 'no'} thread={len(v.get('thread') or [])}",
        )
    )

    # 14. get_tweet_quotes
    results.append(
        await run_check(
            "get_tweet_quotes(anchor, limit=5)",
            lambda: server.get_tweet_quotes(id_or_url=tweet_url, limit=5),
            lambda v: f"{len(v)} quotes",
        )
    )

    # 15. get_liking_users
    results.append(
        await run_check(
            "get_liking_users(anchor, limit=5)",
            lambda: server.get_liking_users(id_or_url=tweet_url, limit=5),
            lambda v: f"{len(v)} likers",
        )
    )

    # 16. get_retweeting_users
    results.append(
        await run_check(
            "get_retweeting_users(anchor, limit=5)",
            lambda: server.get_retweeting_users(id_or_url=tweet_url, limit=5),
            lambda v: f"{len(v)} retweeters",
        )
    )

    # 17. download_media — opt-out of videos to keep this fast.
    results.append(
        await run_check(
            "download_media(anchor, photos only)",
            lambda: server.download_media(
                id_or_url=tweet_url,
                source="auto",
                download_videos=False,
            ),
            lambda v: (
                f"items={len(v)} "
                f"(images may be returned inline so length can exceed 1)"
            ),
        )
    )

    # 18. get_x_article — only if we have an article reference in bookmarks.
    article_ref = None
    for bm in bookmarks_data:
        if bm.get("x_article") and bm["x_article"].get("article_id"):
            article_ref = bm["x_article"]["article_id"]
            break
    if article_ref:
        results.append(
            await run_check(
                f"get_x_article({article_ref})",
                lambda: server.get_x_article(id_or_url=article_ref),
                lambda v: f"title={v.get('title')[:40] if v and v.get('title') else None} chars={v.get('char_count') if v else None}",
            )
        )
    else:
        results.append(
            CheckResult(
                name="get_x_article",
                ok=True,
                summary="SKIPPED — no article in first 5 bookmarks",
            )
        )

    print("=" * 60)
    print("Results:")
    longest = max(len(r.name) for r in results)
    failed = 0
    for r in results:
        status = "PASS" if r.ok else "FAIL"
        print(f"  [{status}] {r.name.ljust(longest)}  {r.summary}")
        if not r.ok:
            failed += 1

    print("=" * 60)
    print(f"{len(results) - failed} / {len(results)} passed")

    if failed:
        print("\n--- failure tracebacks ---")
        for r in results:
            if not r.ok:
                print(f"\n## {r.name}\n{r.detail}")

    # Make sure Chromium is shut down before exit.
    await server._browser.shutdown()
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
