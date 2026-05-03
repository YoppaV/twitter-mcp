# Twitter/X MCP Server

Read-only Twitter/X access exposed as an MCP server, so Claude Code and Claude
Desktop can query your bookmarks, home timeline, search, individual tweets,
and user profiles during any conversation — without you running scripts by hand.

Auth is reused from a Playwright browser session (no API keys, no rate-limited
official API). Browsing is read-only by design — no posting, liking, following,
or DMs — to minimize the chance of getting your account flagged.

## What it gives Claude

Seven tools, all returning plain JSON:

- `auth_status` — is the session loaded?
- `get_bookmarks(limit, since_id)` — your bookmarks, newest first
- `get_home_timeline(limit, feed, since_id)` — `for_you` or `following`
- `get_tweet(id_or_url, include_replies, include_quote)` — focal tweet + thread
- `search_tweets(query, mode, limit)` — `top` or `latest`
- `get_user_profile(handle)` — bio, counters, pinned tweet
- `get_user_tweets(handle, limit, include_replies, include_media_only)`
- `get_x_article(id_or_url)` — full body of a native X long-form article

## Setup

```bash
cd /home/juanr/projects/scraper
python3 -m venv venv
venv/bin/python -m ensurepip                   # if pip is missing
uv pip install --python venv/bin/python -r requirements.txt
venv/bin/python -m playwright install chromium
cp .env.example .env
$EDITOR .env                                   # set TWITTER_USERNAME
```

WSL2 only — if Playwright complains about missing libs:

```bash
sudo venv/bin/python -m playwright install-deps chromium
```

## Auth (one-time)

Open a real browser, log in to x.com:

```bash
venv/bin/python -m scripts.auth_login
```

A Chromium window opens (WSLg paints it on Windows). Log in manually — 2FA,
captcha, whatever. The script detects the `auth_token` + `ct0` cookies and
writes `sessions/<handle>_twitter_state.json`.

Alternative: reuse a Firefox session (close Firefox first to release the
SQLite lock):

```bash
venv/bin/python -m scripts.auth_import_firefox
```

When the session expires, MCP tools surface a `SessionExpiredError` telling
you to re-run `auth_login`. They never silently re-login.

## Running the MCP server

```bash
venv/bin/python -m twitter_sdk.server
```

Speaks JSON-RPC over stdio. You normally don't run this directly — Claude
Code and Claude Desktop spawn it for you.

Quick poke from the command line:

```bash
npx @modelcontextprotocol/inspector venv/bin/python -m twitter_sdk.server
```

The inspector lists the seven tools and lets you call them with arbitrary
arguments.

## Connecting Claude Code

`.mcp.json` is committed at the repo root. Just `cd` into this directory in
Claude Code and the `twitter` server is auto-discovered. Then ask things like:

- "What are my last 5 bookmarks?"
- "Search Twitter for tweets about Anthropic in the last hour."
- "Give me the focal tweet at https://x.com/anthropicai/status/12345 plus its replies."
- "What does @karpathy's profile look like and what are his last 20 tweets?"

## Connecting Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS)
or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "twitter": {
      "command": "/absolute/path/to/scraper/venv/bin/python",
      "args": ["-m", "twitter_sdk.server"],
      "env": {
        "PYTHONPATH": "/absolute/path/to/scraper/src",
        "TWITTER_SESSION_FILE": "/absolute/path/to/scraper/sessions/yourhandle_twitter_state.json"
      }
    }
  }
}
```

Restart Claude Desktop. The tools appear in the tool picker.

Logs (Desktop): `~/Library/Logs/Claude/` (macOS).

## Configuration

| Env var | Default | What it does |
|---|---|---|
| `TWITTER_SESSION_FILE` | `sessions/<TWITTER_USERNAME>_twitter_state.json` | Path to Playwright storage_state JSON |
| `TWITTER_USERNAME` | _empty_ | Used to derive session path if `TWITTER_SESSION_FILE` is unset |
| `BROWSER_IDLE_TIMEOUT_S` | `300` | Tear down Chromium after this many seconds idle |
| `BROWSER_HEADLESS` | `true` | Set `false` to debug with a visible browser |

## Architecture

```
src/twitter_sdk/
├── server.py          FastMCP — registers the 7 tools
├── browser.py         Async Playwright singleton + idle shutdown + asyncio.Lock
├── auth.py            storage_state load + session_summary + login helpers
├── models.py          Tweet / MediaItem / XArticle / QuotedTweet / User
├── parsers.py         GraphQL → dataclass extractors (pure)
├── scraper.py         scroll_collect() + intercept_single_response()
└── endpoints/         bookmarks · home · tweet · search · user
```

`scraper.scroll_collect` takes `(url, fragment, extractor)`. All 5 timeline
endpoints share that one loop; only their starting URL and GraphQL fragment
differ.

## Tests

```bash
venv/bin/python -m unittest discover tests -v
```

43 tests, all offline. Fixture-driven (`tests/fixtures/graphql_*.json`) — no
network, no real browser. The endpoint tests use a `FakePage` that records the
`response` event handler, then dispatches synthetic Twitter GraphQL payloads
through it to exercise the full path through `scroll_collect()`.

## Troubleshooting

**"Twitter session expired"** — `auth_token` is no longer valid. Re-run
`scripts.auth_login`.

**A tool returns `[]` for everything** — Twitter rotated a GraphQL operation
hash. Open DevTools on `x.com/<relevant-page>`, find the new operation name in
the `*/graphql/*/<Name>` URL, and update the `FRAGMENT` constant in the matching
`src/twitter_sdk/endpoints/*.py` file. Capture the JSON response as a new
fixture and add a parser test.

**Server doesn't appear in Claude Desktop** — check
`~/Library/Logs/Claude/mcp*.log`. Most often the path in `claude_desktop_config.json`
is wrong, or `PYTHONPATH` doesn't include `src`.

**Out of RAM** — lower `BROWSER_IDLE_TIMEOUT_S` (e.g. `60`) so Chromium tears
down sooner between calls.

## Out of scope

- Writes (post, like, bookmark, follow, DM). Decided up front.
- Cache or persistence between server restarts. Each call fetches fresh.
- Multiple accounts simultaneously. One `TWITTER_SESSION_FILE` per server.
