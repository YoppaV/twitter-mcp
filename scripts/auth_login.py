"""Interactive Twitter/X login through a Playwright-controlled browser.

Launches a headed Chromium (or Firefox) window, navigates to the X login flow,
and waits for the user to authenticate manually (2FA, captcha, checkpoint —
whatever). As soon as the required cookies (``auth_token`` + ``ct0``) appear,
the full ``storage_state`` is serialized to
``sessions/<handle>_twitter_state.json``. The MCP server reads this file on
every tool call.

Designed for WSL2 + WSLg: the browser window is painted on Windows directly.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from rich.console import Console

import sys as _sys
from pathlib import Path as _Path

_sys.path.insert(0, str(_Path(__file__).resolve().parent.parent / "src"))

from twitter_sdk.auth import (  # noqa: E402
    DEFAULT_LOGIN_TIMEOUT_S,
    TWITTER_LOGIN_URL,
    USER_AGENT,
    VIEWPORT,
    save_storage_state,
    wait_for_twitter_login,
)

from src.config import ConfigError, cfg  # noqa: E402

console = Console()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Interactive Twitter/X login via Playwright (WSLg-friendly)."
    )
    parser.add_argument(
        "--browser",
        choices=("chromium", "firefox"),
        default="chromium",
        help="Which Playwright browser to launch (default: chromium).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser headless. Defeats the purpose of interactive login; escape hatch only.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_LOGIN_TIMEOUT_S,
        help=f"Seconds to wait for the user to finish login (default: {DEFAULT_LOGIN_TIMEOUT_S}).",
    )
    parser.add_argument(
        "--username",
        type=str,
        default=None,
        help="Override Twitter handle (defaults to TWITTER_USERNAME from .env).",
    )
    return parser.parse_args()


def _launch_and_save(
    browser_name: str,
    headless: bool,
    timeout_s: int,
    session_file: Path,
) -> None:
    try:
        from playwright.sync_api import Error as PlaywrightError
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright is not installed. Run: uv pip install playwright && "
            "python -m playwright install chromium"
        ) from exc

    with sync_playwright() as pw:
        launcher = getattr(pw, browser_name)
        try:
            browser = launcher.launch(headless=headless)
        except PlaywrightError as exc:
            raise RuntimeError(
                f"Failed to launch {browser_name}: {exc}\n"
                f"Install browser binaries first: python -m playwright install {browser_name}\n"
                "If native libs are missing on WSL2: sudo python -m playwright install-deps "
                f"{browser_name}"
            ) from exc

        try:
            context = browser.new_context(viewport=VIEWPORT, user_agent=USER_AGENT)
            page = context.new_page()
            page.goto(TWITTER_LOGIN_URL, wait_until="domcontentloaded")
            console.print(
                f"[yellow]Log in to x.com in the browser window. "
                f"Waiting up to {timeout_s}s for auth_token + ct0 cookies...[/yellow]"
            )
            wait_for_twitter_login(context, timeout_s)
            save_storage_state(context, session_file)
        finally:
            browser.close()


def main() -> int:
    args = _parse_args()

    handle = (args.username or cfg.twitter_username).strip()
    if not handle:
        console.print(
            "[red]TWITTER_USERNAME not set in .env and --username not provided.[/red]"
        )
        return 2

    session_file = cfg.session_dir / f"{handle}_twitter_state.json"

    try:
        _launch_and_save(args.browser, args.headless, args.timeout, session_file)
    except (RuntimeError, TimeoutError) as exc:
        console.print(f"[red]{exc}[/red]")
        return 1
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    console.print(f"[green]Wrote storage state for {handle} to {session_file}[/green]")
    console.print(
        "[green]Now start the MCP server: venv/bin/python -m twitter_sdk.server[/green]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
