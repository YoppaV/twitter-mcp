"""Shared id/handle parsers used by multiple endpoints + the server."""

from __future__ import annotations

import re

_HANDLE_RE = re.compile(r"^[A-Za-z0-9_]{1,15}$")
_NUM_RE = re.compile(r"(\d+)")
_STATUS_PATH_RE = re.compile(r"/(\w+)/status/(\d+)")
_ARTICLE_PATH_RE = re.compile(r"/i/article/(\d+)")


def normalize_handle(handle: str) -> str:
    """Strip ``@`` and validate. Raise ValueError on invalid input."""
    h = handle.strip().lstrip("@")
    if not _HANDLE_RE.match(h):
        raise ValueError(
            f"Invalid Twitter handle {handle!r}. Must be 1–15 chars, "
            "letters/digits/underscores only."
        )
    return h


def parse_tweet_id_or_url(text: str) -> tuple[str, str]:
    """Return ``(tweet_id, navigation_url)``.

    Numeric ids navigate via ``/i/web/status/<id>`` (works without knowing the
    author handle). URLs are normalized to ``https://x.com/<handle>/status/<id>``.
    """
    raw = text.strip()
    match = _STATUS_PATH_RE.search(raw)
    if match:
        handle, tid = match.group(1), match.group(2)
        return tid, f"https://x.com/{handle}/status/{tid}"

    id_match = _NUM_RE.fullmatch(raw)
    if id_match:
        tid = id_match.group(1)
        return tid, f"https://x.com/i/web/status/{tid}"

    raise ValueError(
        f"Could not parse a tweet id or URL from {text!r}. "
        "Pass the numeric id or a full https://x.com/<handle>/status/<id> URL."
    )


def parse_article_id_or_url(text: str) -> str:
    """Return the article id from a numeric id or ``/i/article/<id>`` URL."""
    raw = text.strip()
    path_match = _ARTICLE_PATH_RE.search(raw)
    if path_match:
        return path_match.group(1)
    id_match = _NUM_RE.fullmatch(raw)
    if id_match:
        return id_match.group(1)
    raise ValueError(
        f"Could not parse an article id from {text!r}. "
        "Pass the numeric id or a https://x.com/i/article/<id> URL."
    )
