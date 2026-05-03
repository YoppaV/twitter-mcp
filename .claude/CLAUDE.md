# Twitter/X MCP Server

This project exposes a read-only Twitter/X SDK as an MCP server so Claude
(Code or Desktop) can query the user's bookmarks, home timeline, search,
individual tweets, and user profiles in real time during any conversation.

There is no batch pipeline anymore. Claude calls the MCP tools directly.

## MCP tools

| Tool | What it does |
|---|---|
| `auth_status` | Check if a Twitter session is loaded (no network call). |
| `get_bookmarks` | List the user's bookmarks, newest first. `limit`, `since_id`. |
| `get_home_timeline` | For You or Following feed. `limit`, `feed`, `since_id`. |
| `get_tweet` | Fetch one tweet by id or URL, plus replies and quoted tweet. |
| `search_tweets` | Twitter search. `query`, `mode` ("top"\|"latest"), `limit`. |
| `get_user_profile` | Bio + counters + pinned tweet id for `handle`. |
| `get_user_tweets` | Profile timeline. `include_replies`, `include_media_only`. |
| `get_x_article` | Full body of a native X long-form article by id/URL. |
| `get_trends` | Explore-tab trends. `category` ("trending"\|"news"\|"sports"\|"entertainment"\|"for_you"), `limit`. |
| `get_user_followers` | Followers of `handle`. `limit`. |
| `get_user_following` | Accounts `handle` follows. `limit`. |
| `get_user_mentions` | Tweets mentioning `@handle`. `limit`, `mode`. |
| `get_thread` | Author-only self-reply chain rooted at `id_or_url`. |
| `get_tweet_quotes` | Quote-tweets of a tweet. `limit`, `mode`. |
| `get_liking_users` | Users who liked a tweet. `limit`. |
| `get_retweeting_users` | Users who retweeted a tweet (not quote-retweets). `limit`. |

All tools return plain JSON (dataclass → dict). No mutations: read-only by design.

## Architecture

```
src/twitter_sdk/
├── server.py          # FastMCP entrypoint, registers the 16 tools
├── browser.py         # Async Playwright singleton (lazy, idle timeout, asyncio.Lock)
├── auth.py            # storage_state load + session_summary + login helpers
├── models.py          # Tweet, MediaItem, XArticle, QuotedTweet, User, Trend
├── parsers.py         # GraphQL → dataclass extractors (pure)
├── scraper.py         # scroll_collect() + intercept_single_response()
└── endpoints/
    ├── _ids.py            # shared handle / tweet-id / article-id parsers
    ├── bookmarks · home · tweet · search · user · article
    ├── trends.py          # get_trends — explore tabs (GenericTimelineById)
    └── social_graph.py    # followers · following · likers · retweeters
```

`scraper.scroll_collect` is generic over the item type (`Tweet`, `User`,
`Trend`); pass a custom `key_of=` to dedupe non-tweet payloads. `tweet.fetch`
and `user.fetch_profile` use `intercept_single_response` (no scrolling).

## Running the server

```bash
venv/bin/python -m twitter_sdk.server
```

Reads:
- `TWITTER_SESSION_FILE` env var, or
- `sessions/<TWITTER_USERNAME>_twitter_state.json`, or
- the only `sessions/*_twitter_state.json` it can find.

`BROWSER_IDLE_TIMEOUT_S` (default 300) controls how long Chromium stays warm
between tool calls before being shut down to free RAM.

## Auth

```bash
venv/bin/python -m scripts.auth_login
```

Headed Chromium → log in to x.com manually → cookies (`auth_token`, `ct0`)
are persisted to `sessions/<handle>_twitter_state.json`. Use
`scripts.auth_import_firefox` if you'd rather reuse a Firefox session.

When the MCP server hits a `/login` redirect mid-call, tools raise
`SessionExpiredError` with the re-login command. They never auto-login.

## Connecting Claude clients

**Claude Code**: `.mcp.json` is committed at the repo root. Open Claude Code
in this directory and the `twitter` server is auto-discovered.

**Claude Desktop**: add to
`~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or
`%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "twitter": {
      "command": "/home/juanr/projects/scraper/venv/bin/python",
      "args": ["-m", "twitter_sdk.server"],
      "env": {
        "PYTHONPATH": "/home/juanr/projects/scraper/src",
        "TWITTER_SESSION_FILE": "/home/juanr/projects/scraper/sessions/jrdiazSB_twitter_state.json"
      }
    }
  }
}
```

## Testing

```bash
venv/bin/python -m unittest discover tests -v
```

unittest stdlib only — no pytest. Tests are fixture-driven (`tests/fixtures/graphql_*.json`)
and never touch the network or a real browser. The endpoint tests use a
`FakePage` that records response handlers and dispatches synthetic GraphQL
payloads through them.

## When GraphQL fragments break

Twitter occasionally rotates its GraphQL operation hashes. The fragment names
(e.g. `Bookmarks`, `HomeTimeline`, `TweetDetail`, `Followers`, `Favoriters`,
`GenericTimelineById`) are stable — they're embedded in the URL path. If a
tool starts returning empty lists:

1. Open `https://x.com/<relevant-page>` in DevTools (Network tab, filter
   "graphql").
2. Find the request whose URL contains the operation we expect.
3. If the fragment name moved, update the constant in
   `src/twitter_sdk/endpoints/<name>.py` (e.g. `FRAGMENT = "/Bookmarks"`,
   `FOLLOWERS_FRAGMENT = "/Followers"`).
4. Capture the JSON response into `tests/fixtures/graphql_<name>.json` and
   add a parser test.

The trends parser (`extract_trends`) tolerates `data.timeline_response.timeline_response.timeline.instructions` and a flatter `data.timeline.instructions` shape. If both fail, capture a fresh fixture from `https://x.com/explore/tabs/trending` and adjust the path-walk in `parsers._trends_from_entry`.

## What was removed

The previous Knowledge/Obsidian pipeline (`twitter_inbox.py`, `twitter_vault.py`,
`scripts.sync_twitter`, `scripts.build_twitter_vault`) is gone. So is the
Instagram cruft (`scripts.login`, `scripts.download`, `scripts.import_*`).
This repo is now exclusively the Twitter MCP SDK.
