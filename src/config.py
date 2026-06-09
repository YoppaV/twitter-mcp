"""Configuration loader. Reads ``.env`` and exposes the session paths.

The default storage_state location is ``~/.config/twitter-mcp/sessions/`` so
nothing sensitive ever lives inside the checkout. ``<cwd>/sessions/`` is also
probed as a legacy fallback so pre-existing in-repo sessions keep working
without manual migration.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

DEFAULT_SESSION_DIR = Path.home() / ".config" / "twitter-mcp" / "sessions"
LEGACY_SESSION_DIR = Path.cwd() / "sessions"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    twitter_username: str
    session_dir: Path

    @property
    def twitter_session_file(self) -> Path:
        return self.session_dir / f"{self.twitter_username}_twitter_state.json"


def _resolve_session_dir() -> Path:
    """Prefer ``~/.config/twitter-mcp/sessions/``; fall back to ``<cwd>/sessions``
    only if it already exists (legacy in-repo dev-flow)."""
    if LEGACY_SESSION_DIR.exists() and any(LEGACY_SESSION_DIR.glob("*_twitter_state.json")):
        return LEGACY_SESSION_DIR
    return DEFAULT_SESSION_DIR


def load_config() -> Config:
    load_dotenv()

    twitter_username = os.getenv("TWITTER_USERNAME", "").strip()
    session_dir = _resolve_session_dir()
    session_dir.mkdir(parents=True, exist_ok=True)

    return Config(
        twitter_username=twitter_username,
        session_dir=session_dir,
    )


cfg = load_config()
