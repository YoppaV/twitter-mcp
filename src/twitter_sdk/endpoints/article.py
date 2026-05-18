"""Fetch the full body of an X native article (x.com/i/article/<id>).

X articles are long-form posts that don't fit in a tweet. The GraphQL of the
host tweet only exposes ``XArticle`` preview metadata (title + first paragraph);
the body lives behind authenticated rendering at ``/i/article/<id>``.

We navigate Playwright to that URL, wait for the article reader view to render,
and extract both ``inner_text`` (clean for LLM consumption) and full ``content()``
HTML (for callers who want links/markup preserved).

We also scrape the reader DOM for inline media (``<img>`` / ``<video>``).
Articles have no GraphQL backing for their body, so this is best-effort — any
failure degrades to an empty ``media`` tuple rather than aborting the fetch.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import downloader
from ..auth import SessionExpiredError, is_login_redirect
from ..models import Article, MediaItem
from ._ids import parse_article_id_or_url

if TYPE_CHECKING:
    from playwright.async_api import Page

URL_TEMPLATE = "https://x.com/i/article/{article_id}"
READER_SELECTOR = '[data-testid="twitterArticleReadView"]'
TITLE_SELECTOR = "h1"
WAIT_TIMEOUT_MS = 8000

# Media elements inside the reader view only — scoping to the reader keeps
# avatars, nav icons and other chrome out of the result.
MEDIA_SELECTOR = (
    f"{READER_SELECTOR} img, {READER_SELECTOR} video, {READER_SELECTOR} source"
)

# Runs in the page: collapse each matched element to ``{tag, src}``.
# ``currentSrc`` is preferred — for ``<video>`` it resolves the active source.
_MEDIA_EVAL_JS = """
els => els.map(el => ({
    tag: el.tagName.toLowerCase(),
    src: el.currentSrc || el.src || el.getAttribute('src') || ''
}))
"""


async def fetch(page: "Page", *, id_or_url: str) -> Article | None:
    """Return the full Article body, or None if the page didn't render."""
    article_id = parse_article_id_or_url(id_or_url)
    url = URL_TEMPLATE.format(article_id=article_id)

    await page.goto(url, wait_until="domcontentloaded")
    if is_login_redirect(page.url):
        raise SessionExpiredError(
            "Twitter session expired — re-run: python -m scripts.auth_login"
        )

    try:
        await page.wait_for_selector(READER_SELECTOR, timeout=WAIT_TIMEOUT_MS)
    except Exception:
        return None

    try:
        body_text = await page.inner_text(READER_SELECTOR)
    except Exception:
        body_text = ""

    try:
        body_html = await page.content()
    except Exception:
        body_html = ""

    title = ""
    try:
        title = (await page.inner_text(TITLE_SELECTOR)).strip()
    except Exception:
        pass

    media = await _extract_article_media(page)

    return Article(
        article_id=article_id,
        url=url,
        title=title,
        body_text=body_text,
        body_html=body_html,
        char_count=len(body_text),
        media=media,
    )


async def _extract_article_media(page: "Page") -> tuple[MediaItem, ...]:
    """Scrape inline ``<img>``/``<video>`` media from the reader DOM.

    Best-effort: any DOM/eval failure returns ``()``. URLs are de-duplicated
    and filtered through ``downloader.is_downloadable_media_url`` so only real
    Twitter CDN assets survive (``blob:`` previews and trackers are dropped).
    """
    try:
        raw = await page.eval_on_selector_all(MEDIA_SELECTOR, _MEDIA_EVAL_JS)
    except Exception:
        return ()

    items: list[MediaItem] = []
    seen: set[str] = set()
    for entry in raw or []:
        src = (entry.get("src") or "").strip()
        if not src or src in seen:
            continue
        if not downloader.is_downloadable_media_url(src):
            continue
        seen.add(src)
        if entry.get("tag") == "img":
            items.append(MediaItem(kind="photo", url=src, extension="jpg"))
        else:  # video / source
            items.append(MediaItem(kind="video", url=src, extension="mp4"))
    return tuple(items)
