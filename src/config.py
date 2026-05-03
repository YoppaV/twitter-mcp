"""Configuration loader. Reads ``.env`` and exposes paths + Twitter handle."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SESSION_DIR = PROJECT_ROOT / "sessions"
DATA_DIR = PROJECT_ROOT / "data"
TWITTER_DATA_DIR = DATA_DIR / "twitter"
TWITTER_BOOKMARKS_DIR = TWITTER_DATA_DIR / "bookmarks"


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or invalid."""


@dataclass(frozen=True)
class Config:
    twitter_username: str
    session_dir: Path
    data_dir: Path
    twitter_data_dir: Path
    twitter_bookmarks_dir: Path

    @property
    def twitter_session_file(self) -> Path:
        return self.session_dir / f"{self.twitter_username}_twitter_state.json"


def load_config() -> Config:
    load_dotenv(PROJECT_ROOT / ".env")

    twitter_username = os.getenv("TWITTER_USERNAME", "").strip()

    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    TWITTER_BOOKMARKS_DIR.mkdir(parents=True, exist_ok=True)

    return Config(
        twitter_username=twitter_username,
        session_dir=SESSION_DIR,
        data_dir=DATA_DIR,
        twitter_data_dir=TWITTER_DATA_DIR,
        twitter_bookmarks_dir=TWITTER_BOOKMARKS_DIR,
    )


cfg = load_config()
