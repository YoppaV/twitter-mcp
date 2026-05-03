"""Import a Twitter/X session from Firefox cookies into Playwright storage_state format.

Workflow:
  1. Open Firefox and log into https://x.com manually (Google, 2FA, captcha, etc).
  2. Close Firefox so it releases the SQLite lock on cookies.sqlite.
  3. Run this script. It locates the active Firefox profile, reads cookies for
     x.com / twitter.com, and writes them as a Playwright storage_state JSON
     into ``sessions/<handle>_twitter_state.json``.

Supports both native Linux (``~/.mozilla/firefox``) and WSL2 running against a
Windows Firefox install under ``/mnt/c/Users/<user>/AppData/Roaming/Mozilla/Firefox``.
"""

from __future__ import annotations

import argparse
import glob
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path

from rich.console import Console

from src.config import ConfigError, cfg

console = Console()

TWITTER_DOMAINS = (".x.com", "x.com", ".twitter.com", "twitter.com")
REQUIRED_COOKIES = ("auth_token", "ct0")

# Firefox sameSite integer → Playwright string
_SAMESITE_MAP = {0: "None", 1: "Lax", 2: "Strict"}


def _candidate_profile_dirs() -> list[Path]:
    home = Path.home()
    candidates: list[Path] = []

    linux_roots = (
        home / ".mozilla" / "firefox",
        home / "snap" / "firefox" / "common" / ".mozilla" / "firefox",
    )
    for root in linux_roots:
        if not root.exists():
            continue
        candidates.extend(Path(p) for p in glob.glob(str(root / "*.default*")))
        candidates.extend(Path(p) for p in glob.glob(str(root / "*.default-release")))

    for windows_user_dir in glob.glob("/mnt/c/Users/*"):
        win_root = (
            Path(windows_user_dir)
            / "AppData"
            / "Roaming"
            / "Mozilla"
            / "Firefox"
            / "Profiles"
        )
        try:
            if not win_root.exists():
                continue
        except PermissionError:
            continue
        try:
            candidates.extend(Path(p) for p in glob.glob(str(win_root / "*.default*")))
            candidates.extend(Path(p) for p in glob.glob(str(win_root / "*.default-release")))
        except PermissionError:
            continue

    unique: list[Path] = []
    seen: set[Path] = set()
    for path in candidates:
        if path in seen:
            continue
        seen.add(path)
        unique.append(path)
    return unique


def _find_cookies_db(explicit: Path | None) -> Path:
    if explicit is not None:
        if not explicit.exists():
            raise FileNotFoundError(f"cookies.sqlite not found at {explicit}")
        return explicit

    profiles = [p for p in _candidate_profile_dirs() if (p / "cookies.sqlite").exists()]
    if not profiles:
        raise FileNotFoundError(
            "Could not locate any Firefox profile with a cookies.sqlite. "
            "Pass --cookies-db explicitly."
        )

    profiles.sort(key=lambda p: (p / "cookies.sqlite").stat().st_mtime, reverse=True)
    chosen = profiles[0] / "cookies.sqlite"
    console.print(f"[blue]Using Firefox profile:[/blue] {chosen.parent}")
    return chosen


def _read_twitter_cookies(db_path: Path) -> list[dict]:
    """Return Playwright-shaped cookies for Twitter/X from Firefox cookies.sqlite."""
    with tempfile.TemporaryDirectory() as tmp:
        snapshot = Path(tmp) / "cookies.sqlite"
        shutil.copy2(db_path, snapshot)

        uri = f"file:{snapshot}?mode=ro"
        with sqlite3.connect(uri, uri=True) as conn:
            placeholders = ",".join("?" * len(TWITTER_DOMAINS))
            rows = conn.execute(
                f"""
                SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite
                FROM moz_cookies
                WHERE host IN ({placeholders})
                """,
                TWITTER_DOMAINS,
            ).fetchall()

    cookies: list[dict] = []
    for name, value, host, path, expiry, is_secure, is_http_only, same_site in rows:
        expires_raw = int(expiry) if expiry else 0
        # Newer Firefox (Ubuntu 24.04 snap) stores expiry in milliseconds; older
        # builds stored it in seconds. Anything above ~year 2286 in seconds is
        # definitely milliseconds — normalize to seconds.
        if expires_raw > 10_000_000_000:
            expires_raw //= 1000
        expires = expires_raw if expires_raw > 0 else -1
        cookies.append(
            {
                "name": name,
                "value": value,
                "domain": host,
                "path": path or "/",
                "expires": expires,
                "httpOnly": bool(is_http_only),
                "secure": bool(is_secure),
                "sameSite": _SAMESITE_MAP.get(int(same_site or 0), "Lax"),
            }
        )
    return cookies


def _build_storage_state(cookies: list[dict]) -> dict:
    return {"cookies": cookies, "origins": []}


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import Firefox Twitter/X cookies into Playwright storage_state format."
    )
    parser.add_argument(
        "--cookies-db",
        type=Path,
        default=None,
        help="Override path to Firefox cookies.sqlite",
    )
    parser.add_argument(
        "--username",
        type=str,
        default=None,
        help="Override Twitter handle (defaults to TWITTER_USERNAME from .env)",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()

    try:
        handle = (args.username or cfg.twitter_username).strip()
        session_dir = cfg.session_dir
    except ConfigError as exc:
        console.print(f"[red]{exc}[/red]")
        return 2

    if not handle:
        console.print(
            "[red]TWITTER_USERNAME not set in .env and --username not provided.[/red]"
        )
        return 2

    try:
        db_path = _find_cookies_db(args.cookies_db)
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        return 1

    cookies = _read_twitter_cookies(db_path)
    cookie_names = {c["name"] for c in cookies}
    missing = [name for name in REQUIRED_COOKIES if name not in cookie_names]
    if missing:
        console.print(
            f"[red]Missing required Twitter cookies {missing}. "
            "Are you actually logged into x.com in this Firefox profile? "
            "Remember to fully close Firefox before running this.[/red]"
        )
        return 1

    session_file = session_dir / f"{handle}_twitter_state.json"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(_build_storage_state(cookies), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    console.print(
        f"[green]Wrote {len(cookies)} cookies for {handle} to {session_file}[/green]"
    )
    console.print(
        "[green]Now run: python -m scripts.download_twitter --limit 3[/green]"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
