"""Immutable dataclasses representing Twitter/X resources.

These dataclasses are the public shape that MCP tools serialize and return.
They are intentionally Twitter-flavoured but framework-free — no Playwright,
no MCP, no httpx imports.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MediaItem:
    kind: str  # "photo" | "video" | "gif"
    url: str
    extension: str  # "jpg" | "mp4"
    duration_ms: int | None = None


@dataclass(frozen=True)
class XArticle:
    """Metadata for a native X long-form article (x.com/i/article/...)."""

    article_id: str
    title: str
    preview_text: str
    cover_image_url: str
    first_published_at: int  # unix seconds


@dataclass(frozen=True)
class QuotedTweet:
    tweet_id: str
    author_handle: str
    author_name: str
    text: str
    urls: tuple[str, ...]
    x_article: XArticle | None


@dataclass(frozen=True)
class Tweet:
    tweet_id: str
    created_at: str
    author_handle: str
    author_name: str
    author_id: str
    text: str
    is_long_form: bool
    likes: int
    retweets: int
    replies: int
    quote_count: int
    lang: str
    hashtags: tuple[str, ...]
    mentions: tuple[str, ...]
    urls: tuple[str, ...]
    media: tuple[MediaItem, ...]
    x_article: XArticle | None
    quoted: QuotedTweet | None


@dataclass(frozen=True)
class Article:
    """Full body of an X native article (x.com/i/article/...).

    ``XArticle`` (in a Tweet) carries only the preview metadata. Use the
    ``get_x_article`` tool to fetch the rendered body text + HTML.

    ``media`` holds the photos/videos extracted from the rendered reader DOM —
    best-effort (X has no GraphQL backing for article bodies), may be empty.
    """

    article_id: str
    url: str
    title: str
    body_text: str
    body_html: str
    char_count: int
    media: tuple[MediaItem, ...] = ()


@dataclass(frozen=True)
class DownloadedMedia:
    """A media item the ``download_media`` tool fetched and saved to disk."""

    kind: str  # "photo" | "video" | "gif"
    source_url: str
    saved_path: str
    byte_size: int
    content_type: str


@dataclass(frozen=True)
class SkippedMedia:
    """A media item ``download_media`` did NOT save, with the reason why.

    ``reason`` is one of: ``video_not_requested`` (a video/GIF was found but
    ``download_videos`` was False), ``too_large``, ``http_404``, ``deleted``,
    ``download_error``, ``unsupported``.
    """

    kind: str  # "photo" | "video" | "gif"
    source_url: str
    reason: str
    duration_ms: int | None = None
    byte_size: int | None = None


@dataclass(frozen=True)
class MediaDownloadResult:
    """Summary payload returned (alongside inline images) by ``download_media``."""

    source_id: str
    source_kind: str  # "tweet" | "article"
    downloaded: tuple[DownloadedMedia, ...]
    skipped: tuple[SkippedMedia, ...]


@dataclass(frozen=True)
class User:
    user_id: str
    handle: str
    name: str
    bio: str
    location: str
    url: str
    created_at: str
    verified: bool
    protected: bool
    followers_count: int
    following_count: int
    tweet_count: int
    listed_count: int
    profile_image_url: str
    pinned_tweet_id: str | None


@dataclass(frozen=True)
class Trend:
    name: str
    query: str
    url: str
    post_count: int
    category: str
