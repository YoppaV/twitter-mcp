# twitter-mcp

![tests](https://github.com/YoppaV/twitter-mcp/actions/workflows/tests.yml/badge.svg)

A **read-only personal scraper** for Twitter/X — packaged as two things in
one repo:

1. **`twitter_sdk`** — a Playwright-driven Python library for fetching *your
   own* logged-in bookmarks, home timeline, search results, tweet threads,
   user profiles, and long-form X articles. Imported as a regular package.
2. **An MCP server** — exposes those endpoints as 17 tools so Claude Code
   and Claude Desktop can query your account during any conversation, with
   no separate scripts to run.

Auth is reused from a real Playwright browser session — no API keys, no
official-API rate limits. Browsing is read-only by design (no posting,
liking, following, or DMs) to minimize the chance of getting your account
flagged.

> **What makes it useful**: bookmarks and tweets that *quote another tweet*
> are fully expanded — the quoted tweet's text and media are fetched and
> merged into the result. Bookmarks pointing at a native X long-form article
> get the full article body (Markdown + HTML, images inlined) instead of a
> 140-char preview. This is what makes downstream ingestion (e.g.
> [`social-ingest`](https://github.com/YoppaV/social-ingest)) actually
> readable.

## The 17 MCP tools

All return plain JSON, except `download_media`, which also returns binary
content (downloaded images, viewable inline):

| Tool | What it does |
|---|---|
| `auth_status` | Is the session loaded? (No network call.) |
| `get_bookmarks(limit, since_id)` | Your bookmarks, newest first. |
| `get_home_timeline(limit, feed, since_id)` | `for_you` or `following`. |
| `get_tweet(id_or_url, include_replies, include_quote)` | Focal tweet + replies + quoted. |
| `search_tweets(query, mode, limit)` | Twitter search, `top` or `latest`. |
| `get_user_profile(handle)` | Bio, counters, pinned tweet. |
| `get_user_tweets(handle, limit, include_replies, include_media_only)` | Profile timeline. |
| `get_x_article(id_or_url)` | Full body of a native X long-form article. |
| `get_trends(category, limit)` | Explore tab — `trending`, `news`, `sports`, `entertainment`, `for_you`. |
| `get_user_followers(handle, limit)` | Who follows this user (newest first). |
| `get_user_following(handle, limit)` | Who this user follows. |
| `get_user_mentions(handle, limit, mode)` | Tweets that mention `@handle`. |
| `get_thread(id_or_url)` | Author-only self-reply chain from a focal tweet. |
| `get_tweet_quotes(id_or_url, limit, mode)` | Quote-tweets of a tweet. |
| `get_liking_users(id_or_url, limit)` | Users who liked a tweet. |
| `get_retweeting_users(id_or_url, limit)` | Users who retweeted (excludes quotes). |
| `download_media(id_or_url, source, indices, from_quoted, download_videos)` | Download a tweet's/article's media — photos returned inline so Claude can see them; videos/GIFs opt-in. Articles also saved as Markdown + HTML with images inlined. |

## Setup

Requires **Python 3.10+**, [`uv`](https://docs.astral.sh/uv/) for dependency
management, and ~500 MB of disk for the Chromium binary that Playwright
downloads.

Install `uv` (one-time, skip if you already have it):

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Then bootstrap the project:

```bash
git clone https://github.com/YoppaV/twitter-mcp
cd twitter-mcp

# 1. Create the virtualenv and install dependencies
uv venv venv                                   # creates ./venv with the right Python
uv pip install --python venv/bin/python -r requirements.txt

# 2. Install the package itself editable — needed so the MCP Inspector
#    (``mcp dev``) can load src/twitter_sdk/server.py as a single file.
uv pip install --python venv/bin/python -e .

# 3. Download the Chromium binary Playwright drives
venv/bin/python -m playwright install chromium

# 4. Configure your handle
cp .env.example .env                           # then set TWITTER_USERNAME (required)
```

`TWITTER_USERNAME` is the only **mandatory** value in `.env` — it's used to
name the session file (`<handle>_twitter_state.json`) and `auth_login`
refuses to run without it.

WSL2 only — if Playwright complains about missing system libs:

```bash
sudo venv/bin/python -m playwright install-deps chromium
```

(`auth_login` opens a headed Chromium window. On WSL2 this is rendered by
WSLg, which ships with Windows 11 by default.)

## First-time session init

The Playwright `storage_state` cookie file lives at
`~/.config/twitter-mcp/sessions/<handle>_twitter_state.json` by default
(override with `TWITTER_SESSION_FILE`). Pre-existing files in
`./sessions/` are also picked up as a fallback for backwards compatibility.

Two ways to create the session:

```bash
# (1) Interactive: open a real Chromium window and log in by hand.
venv/bin/python -m scripts.auth_login
```

A Chromium window opens (WSLg paints it on Windows). Log in manually —
2FA, captcha, whatever. The script waits until the `auth_token` + `ct0`
cookies appear, then persists the storage state.

```bash
# (2) Reuse a Firefox session you're already logged into.
# Close Firefox first to release the SQLite lock.
venv/bin/python -m scripts.auth_import_firefox
```

When the session expires, MCP tools surface a `SessionExpiredError`
telling you to re-run `auth_login`. They never silently re-login.

## Use as a library (`twitter_sdk`)

For sibling projects that just want the SDK:

```bash
uv pip install -e /path/to/twitter-mcp
```

```python
import asyncio
from pathlib import Path
from twitter_sdk.browser import BrowserSession
from twitter_sdk.endpoints import bookmarks

async def main():
    session = Path.home() / ".config/twitter-mcp/sessions/yourhandle_twitter_state.json"
    browser = BrowserSession(session, idle_timeout_s=0, headless=True)
    try:
        async with browser.page() as page:
            for tweet in await bookmarks.fetch(page, limit=20):
                print(tweet.tweet_id, tweet.text[:80])
    finally:
        await browser.shutdown()

asyncio.run(main())
```

This is the same surface area [`social-ingest`](https://github.com/YoppaV/social-ingest)
consumes for its Twitter pipeline. The `Tweet` dataclass carries a
`quoted` field with the embedded quoted-tweet metadata and a `media` tuple
with photo/video/gif URLs already resolved.

## Use as an MCP server

### Claude Code

`.mcp.json` is committed at the repo root. `cd` into this directory in
Claude Code and the `twitter` server is auto-discovered. With
`TWITTER_USERNAME` set in `.env`, the server auto-resolves the session at
`~/.config/twitter-mcp/sessions/<handle>_twitter_state.json` — no extra
config needed. Override with `TWITTER_SESSION_FILE` only if your session
lives somewhere else.

Restart Claude Code (or `/mcp` reconnect) after editing `.env` so the
server picks up the new values. Then ask things like:

- "What are my last 5 bookmarks?"
- "Search Twitter for tweets about Anthropic in the last hour."
- "Give me the focal tweet at https://x.com/anthropicai/status/12345 plus its replies."
- "What does @karpathy's profile look like and what are his last 20 tweets?"
- "Download the images from https://x.com/anthropicai/status/12345 and tell me what's in them."

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`
(macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "twitter": {
      "command": "/absolute/path/to/twitter-mcp/venv/bin/python",
      "args": ["-m", "twitter_sdk.server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/twitter-mcp/src",
        "TWITTER_SESSION_FILE": "/absolute/path/to/sessions/yourhandle_twitter_state.json"
      }
    }
  }
}
```

Restart Claude Desktop. The tools appear in the tool picker.

Logs (Desktop): `~/Library/Logs/Claude/` (macOS).

### Standalone

```bash
venv/bin/python -m twitter_sdk.server
```

Speaks JSON-RPC over stdio. You normally don't run this directly — Claude
Code and Claude Desktop spawn it for you.

### MCP Inspector

Inspect the server interactively with the bundled MCP Inspector (browse
the 17 tools, fire them with arbitrary args, see raw responses):

```bash
venv/bin/mcp dev src/twitter_sdk/server.py
```

The `mcp` CLI ships with the `mcp[cli]` dependency, so no extra install
is needed — just open the printed URL in your browser. Requires Node.js
on `PATH` (the Inspector UI is a small Node app `mcp dev` spawns for you)
and the editable install from setup step 2 (so `mcp dev` can resolve
`twitter_sdk` as a package when it loads `server.py` as a single file).

## Configuration

| Env var | Default | What it does |
|---|---|---|
| `TWITTER_SESSION_FILE` | `~/.config/twitter-mcp/sessions/<TWITTER_USERNAME>_twitter_state.json` | Path to Playwright storage_state JSON |
| `TWITTER_USERNAME` | _empty_ | Used to derive session path if `TWITTER_SESSION_FILE` is unset |
| `TWITTER_DOWNLOADS_DIR` | `./downloads` | Where `download_media` writes fetched media |
| `BROWSER_IDLE_TIMEOUT_S` | `300` | Tear down Chromium after this many seconds idle |
| `BROWSER_HEADLESS` | `true` | Set `false` to debug with a visible browser |

## Architecture

```
src/twitter_sdk/
├── server.py          FastMCP — registers the 17 tools
├── browser.py         Async Playwright singleton + idle shutdown + asyncio.Lock
├── auth.py            storage_state load + session_summary + login helpers
├── models.py          Tweet / MediaItem / XArticle / QuotedTweet / User / Trend
├── parsers.py         GraphQL → dataclass extractors (pure)
├── scraper.py         scroll_collect() + intercept_single_response()
├── downloader.py      pure httpx streaming download helper (no Playwright)
└── endpoints/
    ├── _ids.py        shared handle / tweet-id / article-id parsers
    ├── bookmarks · home · tweet · search · user · article
    ├── media          resolve_tweet_media / resolve_article_media
    ├── trends         get_trends — explore tabs
    └── social_graph   followers · following · likers · retweeters
```

`download_media` saves photos to the downloads directory (defaults to
`./downloads/`, override with `TWITTER_DOWNLOADS_DIR`) **and** returns them
inline as viewable images so the picture itself enters the conversation.
Videos/GIFs are opt-in (`download_videos=True`) since Claude can't watch
them. For a native X article it also writes `article_<id>.md` and `.html`
— the article re-rendered (headings, text, images inlined where they
appear), readable offline. The default downloads directory is gitignored
and treated as temporary scratch.

`scraper.scroll_collect` is generic over the item type — it takes
`(url, fragment, extractor)` plus an optional `key_of` for deduplication.
All timeline endpoints (tweets, users, trends) share that one loop.

## Tests

```bash
venv/bin/python -m unittest discover tests -v
```

All offline. Fixture-driven (`tests/fixtures/graphql_*.json`) — no
network, no real browser. The endpoint tests use a `FakePage` that records
the `response` event handler, then dispatches synthetic Twitter GraphQL
payloads through it to exercise the full path through `scroll_collect()`.

CI runs the same suite on Python 3.10 + 3.12.

### Real-network smoke

For an end-to-end check against live x.com (handy after Twitter rotates a
GraphQL fragment), there's a small runner that exercises every tool with
conservative limits:

```bash
venv/bin/python -m scripts.smoke_real
```

Takes 2-4 minutes, prints a PASS/FAIL table per tool, and exits non-zero
if anything broke. Requires a valid session (see "First-time session init").

## Troubleshooting

**"Twitter session expired"** — `auth_token` is no longer valid. Re-run
`scripts.auth_login`.

**A tool returns `[]` for everything** — Twitter rotated a GraphQL operation
hash. Open DevTools on `x.com/<relevant-page>`, find the new operation name
in the `*/graphql/*/<Name>` URL, and update the `FRAGMENT` constant in the
matching `src/twitter_sdk/endpoints/*.py` file. Capture the JSON response
as a new fixture and add a parser test.

**Server doesn't appear in Claude Desktop** — check
`~/Library/Logs/Claude/mcp*.log`. Most often the path in
`claude_desktop_config.json` is wrong, or `PYTHONPATH` doesn't include
`src`.

**Out of RAM** — lower `BROWSER_IDLE_TIMEOUT_S` (e.g. `60`) so Chromium
tears down sooner between calls.

## Caveats

- This is a **personal-scale** scraper of *your own* logged-in account. It
  is not a multi-tenant API. X may detect and flag bulk usage; cookies will
  invalidate at the platform's discretion.
- Respect Twitter/X's Terms of Service. You are responsible for how you use
  the data this tool fetches.
- No warranty, no SLA. Twitter's GraphQL surface rotates without notice —
  expect occasional fixture refreshes when an endpoint changes.

## Out of scope

- Writes (post, like, bookmark, follow, DM). Decided up front.
- Cache or persistence between server restarts. Each call fetches fresh.
- Multiple accounts simultaneously. One `TWITTER_SESSION_FILE` per server.

## Pairing with GetXAPI for Cheaper Read Operations (Optional)

For users who need a cheaper or higher-rate-limit option for read-only Twitter (X) operations such as tweet search, profile lookup, and follower lists, this project can be paired with [GetXAPI](https://getxapi.com), a budget Twitter / X data API priced at $0.05 per 1K tweets versus the official X API basic tier at $200 / month.

Two integration patterns:

1. **Run side-by-side in your AI client.** Keep this project for its primary workflow and add the [official GetXAPI MCP server](https://github.com/getxapi/getxapi-mcp) for read-heavy tasks. Each tool name routes to the backend best suited for that operation.

2. **Add a backend toggle.** For a code-level reference of an optional alternative backend behind a single env variable, see the [PR pattern merged into a sibling project](https://github.com/GenAIwithMS/twitter-mcp/pull/3).

GetXAPI quick start:

- Signup with $0.50 free credit (no card required): https://getxapi.com/signup
- Official GetXAPI MCP server: https://github.com/getxapi/getxapi-mcp
- npm: `@getxapi/mcp`
- Pay-per-call pricing: $0.001 / call, $0.05 / 1K tweets

This pairing is fully optional. No behavior change for existing users.

## License

[MIT](LICENSE) © 2026 Juan Rodríguez Díaz de Greñu.
