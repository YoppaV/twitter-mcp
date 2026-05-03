"""Session/auth helpers — load Playwright storage_state, detect expiry."""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.sync_api import BrowserContext

TWITTER_LOGIN_URL = "https://x.com/i/flow/login"
TWITTER_ORIGIN = "https://x.com"
LOGIN_URL_FRAGMENTS: tuple[str, ...] = ("/login", "/i/flow/login")
REQUIRED_COOKIES: tuple[str, ...] = ("auth_token", "ct0")
DEFAULT_LOGIN_TIMEOUT_S = 600
LOGIN_POLL_INTERVAL_S = 1.5

VIEWPORT = {"width": 1280, "height": 800}
USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)


class SessionExpiredError(RuntimeError):
    """Raised when storage_state no longer authenticates against x.com."""


def is_login_redirect(url: str) -> bool:
    return any(frag in url for frag in LOGIN_URL_FRAGMENTS)


def session_summary(session_path: Path) -> dict[str, object]:
    """Return ``{authenticated, handle, session_path, last_login}``.

    ``authenticated`` is a best-effort flag: True iff the file exists and
    contains the required cookies. Whether the cookies still WORK against
    x.com requires an actual request — done by ``auth_status`` tool.
    """
    summary: dict[str, object] = {
        "authenticated": False,
        "handle": _handle_from_session_path(session_path),
        "session_path": str(session_path),
        "last_login": None,
        "missing_cookies": list(REQUIRED_COOKIES),
    }
    if not session_path.exists():
        return summary

    try:
        raw = json.loads(session_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return summary

    cookies = raw.get("cookies") or []
    cookie_names = {c.get("name") for c in cookies}
    missing = [name for name in REQUIRED_COOKIES if name not in cookie_names]
    summary["missing_cookies"] = missing
    summary["authenticated"] = not missing

    try:
        mtime = datetime.fromtimestamp(session_path.stat().st_mtime, tz=timezone.utc)
        summary["last_login"] = mtime.isoformat()
    except OSError:
        pass

    return summary


def _handle_from_session_path(path: Path) -> str:
    name = path.stem  # ``jrdiazSB_twitter_state``
    if name.endswith("_twitter_state"):
        return name[: -len("_twitter_state")]
    return name


def collect_twitter_cookies(context: "BrowserContext") -> dict[str, str]:
    """Return a flat ``{name: value}`` dict for all x.com cookies."""
    cookies = context.cookies(TWITTER_ORIGIN)
    return {c["name"]: c["value"] for c in cookies}


def wait_for_twitter_login(
    context: "BrowserContext",
    timeout_s: int = DEFAULT_LOGIN_TIMEOUT_S,
) -> dict[str, str]:
    """Poll cookies until all REQUIRED_COOKIES are present or timeout elapses."""
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        cookies = collect_twitter_cookies(context)
        if all(name in cookies for name in REQUIRED_COOKIES):
            return cookies
        time.sleep(LOGIN_POLL_INTERVAL_S)

    cookies = collect_twitter_cookies(context)
    missing = [name for name in REQUIRED_COOKIES if name not in cookies]
    raise TimeoutError(
        f"Timed out after {timeout_s}s waiting for Twitter login. "
        f"Missing cookies: {missing}"
    )


def save_storage_state(context: "BrowserContext", path: Path) -> None:
    """Persist Playwright storage_state (cookies + localStorage) to ``path``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    context.storage_state(path=str(path))
