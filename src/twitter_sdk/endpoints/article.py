"""Fetch the full body of an X native article (x.com/i/article/<id>).

X articles are long-form posts that don't fit in a tweet. The GraphQL of the
host tweet only exposes ``XArticle`` preview metadata (title + first paragraph);
the body lives behind authenticated rendering at ``/i/article/<id>``.

We navigate Playwright to that URL, wait for the article reader view to render,
and extract both ``inner_text`` (clean for LLM consumption) and full ``content()``
HTML (for callers who want links/markup preserved).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ..auth import SessionExpiredError, is_login_redirect
from ..models import Article
from ._ids import parse_article_id_or_url

if TYPE_CHECKING:
    from playwright.async_api import Page

URL_TEMPLATE = "https://x.com/i/article/{article_id}"
READER_SELECTOR = '[data-testid="twitterArticleReadView"]'
TITLE_SELECTOR = "h1"
WAIT_TIMEOUT_MS = 8000


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

    return Article(
        article_id=article_id,
        url=url,
        title=title,
        body_text=body_text,
        body_html=body_html,
        char_count=len(body_text),
    )
