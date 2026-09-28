# app/scraper/article_images.py
"""Recover a photo for articles whose feed item carried none.

Some trade feeds ship no image at all (Business Insurance) or on only half
their items (Insurance Journal), which left the Insurance & Financial
Services tile on its vector for nearly every headline. The article page's own
og:image / twitter:image is the publisher's chosen picture for that story, so
it is read from there — but only for the handful of articles that actually
reach the Command Center, never the whole scrape.

A share image is not always a photo of the story: many publishers fall back
to a site-wide logo card. Those are rejected two ways — by name (logo,
default, placeholder...) and by repetition (the same url returned for two
different stories in one batch is a site default, whatever it is called).
A rejected image leaves the article without one, and the tile shows its
vector, which reads better than a masthead.

Set FETCH_ARTICLE_IMAGES=0 to skip the network entirely (tests, CI).
"""
import asyncio
import logging
import os
import re
from collections import Counter

import aiohttp

logger = logging.getLogger(__name__)

_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
# <head> is all that is needed; stop reading long before the article body.
_MAX_BYTES = 256 * 1024
_CONCURRENCY = 8
_TIMEOUT = aiohttp.ClientTimeout(total=10)

_META_TAG = re.compile(r'<meta\b[^>]*>', re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']')
_IMAGE_KEYS = ('og:image', 'og:image:url', 'og:image:secure_url', 'twitter:image', 'twitter:image:src')
_GENERIC = re.compile(r'logo|default|placeholder|favicon|site-?icon|cropped-|fallback|/social/', re.I)
# Feed images that are thumbnails rather than pictures: ET's B2B sites (ET GCC,
# HRWorld, Telecom, Manufacturing) send 100x100 crops in their feeds, while
# the article page's share image is 1200x627. Treated as no image, so the
# page's own is fetched; kept if the page has none.
_THUMBNAIL = re.compile(r'etb2bimg\.com/thumb/img-size-', re.I)

# url -> image or None, kept for the life of the process so the live API does
# not refetch the same pages on every request inside one cache window.
_seen = {}


def enabled():
    return os.environ.get('FETCH_ARTICLE_IMAGES', '1') != '0'


def share_image(html):
    """The page's og:image (or twitter:image), or None."""
    found = {}
    for tag in _META_TAG.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        key = (attrs.get('property') or attrs.get('name') or '').lower()
        if key in _IMAGE_KEYS and attrs.get('content', '').startswith('http'):
            found.setdefault(key, attrs['content'].strip())
    return next((found[k] for k in _IMAGE_KEYS if k in found), None)


def is_generic(image):
    """True for a site logo or default share card rather than a story photo."""
    return bool(_GENERIC.search(image or ''))


def is_thumbnail(image):
    """True for a feed's thumbnail crop, too small to show as a picture."""
    return bool(_THUMBNAIL.search(image or ''))


async def _fetch_one(session, sem, url):
    async with sem:
        try:
            async with session.get(url) as response:
                response.raise_for_status()
                raw = await response.content.read(_MAX_BYTES)
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
            logger.info("No share image for %s: %s", url, e)
            return url, None
    image = share_image(raw.decode('utf-8', errors='replace'))
    return url, None if is_generic(image) else image


async def _fetch_all(urls):
    sem = asyncio.Semaphore(_CONCURRENCY)
    async with aiohttp.ClientSession(headers=_HEADERS, timeout=_TIMEOUT) as session:
        return await asyncio.gather(*(_fetch_one(session, sem, u) for u in urls))


def fill_missing_images(articles, fetch=None):
    """Give each article dict without an http image its page's share image.

    A feed thumbnail (is_thumbnail) counts as no image; it is kept when the
    page offers nothing better. Mutates ``articles`` in place and returns how
    many gained an image. ``fetch`` (urls -> [(url, image)]) is injectable
    for tests.
    """
    if fetch is None:
        if not enabled():
            return 0
        fetch = lambda urls: asyncio.run(_fetch_all(urls))

    from .news_search import is_search_link
    # A news-search link opens a redirect page, whose share image is the
    # search engine's own card, not the story's.
    missing = [a for a in articles
               if a.get('url') and not is_search_link(a['url'])
               and (not (a.get('image') or '').startswith('http') or is_thumbnail(a.get('image')))]
    todo = sorted({a['url'] for a in missing} - _seen.keys())
    if todo:
        results = dict(fetch(todo))
        # One picture for several different stories is the site's default card.
        repeats = {img for img, n in Counter(i for i in results.values() if i).items() if n > 1}
        for url, image in results.items():
            _seen[url] = None if image in repeats else image

    filled = 0
    for a in missing:
        image = _seen.get(a['url'])
        if image:
            a['image'] = image
            filled += 1
    if missing:
        logger.info("Share images recovered for %d of %d imageless articles.",
                    filled, len(missing))
    return filled


def fill_signal_images(signals_data, articles_by_title):
    """Fill images for every raw article a Signal surfaces — the tiles, hero
    and featured builders all draw from these, so one pass serves all three."""
    titles = {a['title'] for s in signals_data.get('signals', [])
              for a in s.get('articles', [])}
    return fill_missing_images(
        [articles_by_title[t] for t in titles if t in articles_by_title])
