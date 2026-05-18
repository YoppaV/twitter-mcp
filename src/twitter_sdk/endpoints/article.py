"""Fetch the full body of an X native article (x.com/i/article/<id>).

X articles are long-form posts that don't fit in a tweet. The GraphQL of the
host tweet only exposes ``XArticle`` preview metadata (title + first paragraph);
the body lives behind authenticated rendering at ``/i/article/<id>``.

We navigate Playwright to that URL, wait for the article reader view to render,
and extract three views of the body:

- ``body_text`` — clean ``inner_text``, for LLM consumption.
- ``body_markdown`` — the body re-rendered as Markdown, images inlined.
- ``body_html`` — the body re-rendered as a standalone HTML document, images
  inlined; inline formatting (bold/links) preserved from the source DOM.

The reader page wraps the prose (``twitterArticleRichTextView``) in chrome —
an author header and a footer. We scope extraction to the rich-text view so
that chrome stays out, and pull the cover image separately from the header.
Articles have no GraphQL backing, so the walk is best-effort — any failure
degrades to an empty media tuple and a fallback built from ``body_text``
rather than aborting the fetch.
"""

from __future__ import annotations

from html import escape as _escape
from typing import TYPE_CHECKING, Any

from .. import downloader
from ..auth import SessionExpiredError, is_login_redirect
from ..models import Article, MediaItem
from ._ids import parse_article_id_or_url

if TYPE_CHECKING:
    from playwright.async_api import Page

URL_TEMPLATE = "https://x.com/i/article/{article_id}"
READER_SELECTOR = '[data-testid="twitterArticleReadView"]'
RICH_TEXT_SELECTOR = '[data-testid="twitterArticleRichTextView"]'
TITLE_SELECTOR = '[data-testid="twitter-article-title"]'
WAIT_TIMEOUT_MS = 8000

# Runs in the page. Returns ``{title, coverSrc, blocks}``:
#  - title: the article title element's text.
#  - coverSrc: the header cover image (sits outside the prose container).
#  - blocks: the rich-text view walked depth-first into an ordered list of
#    ``{type:"text", tag, text, fs, html}`` / ``{type:"image"|"video", src}``.
#    A "leaf" is an element with no block-level descendant; its innerText is
#    one block, ``fs`` its computed font size in px, ``html`` its innerHTML
#    (kept so the HTML render preserves inline bold/links). Document order is
#    preserved so each image renders where it appears; ``fs`` lets the render
#    detect headings (X uses oversized divs, not <h*> tags).
_ARTICLE_CONTENT_JS = """
() => {
  const reader = document.querySelector('[data-testid="twitterArticleReadView"]');
  if (!reader) return null;
  const titleEl = reader.querySelector('[data-testid="twitter-article-title"]');
  const title = titleEl ? (titleEl.innerText || '').trim() : '';
  let coverSrc = '';
  const cover = reader.querySelector('[data-testid="tweetPhoto"] img');
  if (cover) coverSrc = cover.currentSrc || cover.src || cover.getAttribute('src') || '';
  const body = reader.querySelector('[data-testid="twitterArticleRichTextView"]')
               || reader;
  const BLOCK_SEL = 'div,p,h1,h2,h3,h4,h5,h6,ul,ol,li,figure,blockquote,' +
                    'section,article,header,footer,aside,table,pre,main';
  const blocks = [];
  function pushMedia(el) {
    const src = el.currentSrc || el.src || el.getAttribute('src') || '';
    if (src) blocks.push({type: el.tagName === 'IMG' ? 'image' : 'video', src: src});
  }
  function walk(node) {
    const kids = node.children;
    for (let i = 0; i < kids.length; i++) {
      const el = kids[i];
      const tag = el.tagName;
      if (tag === 'IMG' || tag === 'VIDEO' || tag === 'SOURCE') {
        pushMedia(el);
        continue;
      }
      if (el.querySelector(BLOCK_SEL)) {
        walk(el);
      } else {
        const text = (el.innerText || '').trim();
        if (text) {
          const fs = parseFloat(getComputedStyle(el).fontSize) || 0;
          blocks.push({type: 'text', tag: tag, text: text, fs: fs,
                       html: el.innerHTML});
        }
        el.querySelectorAll('img,video').forEach(pushMedia);
      }
    }
  }
  walk(body);
  return {title: title, coverSrc: coverSrc, blocks: blocks};
}
"""

_HEADING_TAGS = {"H1", "H2", "H3", "H4", "H5", "H6"}

# X's article editor renders section headings as oversized text inside plain
# <div>s (no <h*> tags). Body copy is ~17px; headings are ~26px. A leaf text
# block at or above this threshold is treated as a section heading.
_HEADING_MIN_FONT_PX = 21.0

# Minimal stylesheet so the saved .html reads well on its own (X's own CSS,
# which the raw DOM relies on, isn't available offline).
_ARTICLE_CSS = """body{max-width:740px;margin:40px auto;padding:0 22px;
font:17px/1.65 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
color:#0f1419;background:#fff;}
h1{font-size:31px;line-height:1.25;}
h2{font-size:23px;margin-top:1.7em;}
h3{font-size:20px;}
p{margin:1em 0;}
img{max-width:100%;height:auto;display:block;margin:1.3em 0;border-radius:10px;}
a{color:#1d9bf0 !important;}
.src{color:#536471;font-size:14px;}
blockquote{border-left:3px solid #cfd9de;margin:1em 0;padding-left:1em;
color:#536471;}"""


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

    await _scroll_to_load(page)

    body_text = await _first_inner_text(page, RICH_TEXT_SELECTOR, READER_SELECTOR)
    content = await _extract_article_content(page)

    title = (content.get("title") or "").strip()
    if not title:
        try:
            title = (await page.inner_text(TITLE_SELECTOR)).strip()
        except Exception:
            pass

    media, body_markdown, body_html = _render_article(
        title, url, body_text, content
    )

    return Article(
        article_id=article_id,
        url=url,
        title=title,
        body_text=body_text,
        body_html=body_html,
        char_count=len(body_text),
        media=media,
        body_markdown=body_markdown,
    )


async def _scroll_to_load(page: "Page", *, max_steps: int = 30) -> None:
    """Scroll through the page so lazy-loaded article images enter the DOM.

    X creates an article image's ``<img>`` only as it nears the viewport;
    without this a long article yields only the images above the fold. Scrolls
    a viewport at a time until the bottom, then back to the top. Best-effort —
    any failure (e.g. a page double without ``wait_for_timeout``) is swallowed.
    """
    try:
        for _ in range(max_steps):
            await page.evaluate("window.scrollBy(0, window.innerHeight)")
            await page.wait_for_timeout(300)
            at_bottom = await page.evaluate(
                "window.innerHeight + window.scrollY "
                ">= document.body.scrollHeight - 2"
            )
            if at_bottom:
                break
        await page.evaluate("window.scrollTo(0, 0)")
        await page.wait_for_timeout(200)
    except Exception:
        pass


async def _first_inner_text(page: "Page", *selectors: str) -> str:
    """Return ``inner_text`` of the first selector that matches with content."""
    for selector in selectors:
        try:
            text = await page.inner_text(selector)
        except Exception:
            continue
        if text:
            return text
    return ""


async def _extract_article_content(page: "Page") -> dict[str, Any]:
    """Run the in-page walk → ``{title, coverSrc, blocks}``. Best-effort → {}."""
    try:
        data = await page.evaluate(_ARTICLE_CONTENT_JS)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _render_article(
    title: str,
    url: str,
    body_text: str,
    content: dict[str, Any],
) -> tuple[tuple[MediaItem, ...], str, str]:
    """Render the extracted content into ``(media, markdown, html)``.

    Media URLs are de-duplicated (one ``MediaItem`` per URL) but every image
    occurrence is rendered. Avatars (``/profile_images/``) and non-CDN URLs
    (emoji, ``blob:`` previews) are dropped. If the walk produced no usable
    body, both renders fall back to ``body_text``.
    """
    media: list[MediaItem] = []
    index_by_src: dict[str, int] = {}
    md_lines: list[str] = []
    html_lines: list[str] = []

    def register(src: str, kind: str) -> bool:
        """Record a usable media URL; return False for filtered/empty URLs."""
        src = (src or "").strip()
        if not src or "/profile_images/" in src:
            return False
        if not downloader.is_downloadable_media_url(src):
            return False
        if src not in index_by_src:
            index_by_src[src] = len(media)
            media.append(
                MediaItem(
                    kind="photo" if kind == "image" else "video",
                    url=src,
                    extension="jpg" if kind == "image" else "mp4",
                )
            )
        return True

    cover_src = (content.get("coverSrc") or "").strip()
    if register(cover_src, "image"):
        md_lines.append(f"![cover]({cover_src})")
        html_lines.append(f'<img src="{cover_src}" alt="cover">')

    for block in content.get("blocks") or []:
        btype = block.get("type")
        if btype == "text":
            md = _text_block_to_md(block, title)
            if md:
                md_lines.append(md)
            html = _text_block_to_html(block, title)
            if html:
                html_lines.append(html)
        elif btype in ("image", "video"):
            src = (block.get("src") or "").strip()
            if register(src, btype):
                if btype == "image":
                    md_lines.append(f"![image]({src})")
                    html_lines.append(f'<img src="{src}" alt="image">')
                else:
                    md_lines.append(f"[Video]({src})")
                    html_lines.append(f'<p><a href="{src}">Video</a></p>')

    markdown = _assemble_markdown(title, url, body_text, md_lines)
    html_doc = _assemble_html(title, url, body_text, html_lines)
    return tuple(media), markdown, html_doc


def _assemble_markdown(
    title: str, url: str, body_text: str, md_lines: list[str]
) -> str:
    header: list[str] = []
    if title:
        header.append(f"# {title}")
    if url:
        header.append(f"*Source: {url}*")
    body = "\n\n".join(md_lines).strip() or body_text.strip()
    markdown = "\n\n".join(part for part in [*header, body] if part).strip()
    return markdown + "\n" if markdown else ""


def _assemble_html(
    title: str, url: str, body_text: str, html_lines: list[str]
) -> str:
    parts: list[str] = []
    if title:
        parts.append(f"<h1>{_escape(title)}</h1>")
    if url:
        parts.append(f'<p class="src">Source: <a href="{url}">{_escape(url)}</a></p>')
    if html_lines:
        parts.extend(html_lines)
    elif body_text.strip():
        parts.append(f"<p>{_escape(body_text.strip())}</p>")
    if not parts:
        return ""
    safe_title = _escape(title) if title else "X Article"
    return (
        '<!DOCTYPE html>\n<html lang="en">\n<head>\n'
        '<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{safe_title}</title>\n"
        f"<style>\n{_ARTICLE_CSS}\n</style>\n"
        "</head>\n<body>\n<article>\n"
        + "\n".join(parts)
        + "\n</article>\n</body>\n</html>\n"
    )


def _text_block_to_md(block: dict[str, Any], title: str) -> str:
    """Render a single text block to Markdown (heading / list item / paragraph).

    Headings are recognised two ways: a real ``<h1>``-``<h6>`` tag, or — the
    common X case — an oversized font on a plain ``<div>`` (see
    ``_HEADING_MIN_FONT_PX``), rendered as a level-2 heading.
    """
    text = (block.get("text") or "").strip()
    if not text or text == title:  # drop a body copy of the title heading
        return ""
    tag = (block.get("tag") or "").upper()
    if tag in _HEADING_TAGS:
        return "#" * int(tag[1]) + " " + " ".join(text.split())
    if (block.get("fs") or 0) >= _HEADING_MIN_FONT_PX:
        return "## " + " ".join(text.split())
    if tag == "LI":
        return "- " + " ".join(text.split())
    if tag == "BLOCKQUOTE":
        return "> " + text.replace("\n", "\n> ")
    return text


def _text_block_to_html(block: dict[str, Any], title: str) -> str:
    """Render a single text block to an HTML element.

    Headings (real tag or oversized font) become ``<h*>`` with escaped text.
    Other blocks reuse the leaf's own ``innerHTML`` so inline bold (X uses
    ``style="font-weight:bold"``) and links (``<a href>``) survive; if that's
    missing the escaped plain text is used.
    """
    text = (block.get("text") or "").strip()
    if not text or text == title:
        return ""
    tag = (block.get("tag") or "").upper()
    if tag in _HEADING_TAGS:
        level = int(tag[1])
        return f"<h{level}>{_escape(text)}</h{level}>"
    if (block.get("fs") or 0) >= _HEADING_MIN_FONT_PX:
        return f"<h2>{_escape(text)}</h2>"
    content = (block.get("html") or "").strip() or _escape(text)
    if tag == "LI":
        return f"<li>{content}</li>"
    if tag == "BLOCKQUOTE":
        return f"<blockquote>{content}</blockquote>"
    return f"<p>{content}</p>"
