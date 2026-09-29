# app/scraper/news_sitemaps.py
"""GCC stories and leadership moves from publishers whose RSS is dead but
whose news sitemap is live.

Moneycontrol's RSS feeds froze upstream in April 2024 and were removed on
2026-09-20, yet it still publishes a Google News sitemap for search engines:
its last few days of stories, each with headline, publication time and
picture. On 2026-09-25 it listed two GCC stories no other source carried.
Analytics India Magazine's RSS serves nothing to a scraper; its sitemap works
the same way (BP-57).

Two kinds of story are kept, on the headline alone: those that name a
capability centre (gcc_rubric.names_a_centre), and appointments and exits
stated in the headline (is_a_move, People Movers' own test). A sitemap lists
every story, about 300 a day for Moneycontrol; taking them all would reshuffle
every tile. A sitemap carries no description, so for each story kept the
article page's own og:description is read -- a few pages per build.
"""
import asyncio
import logging
import re
from html import unescape

import aiohttp
from bs4 import BeautifulSoup

from ..analysis.gcc_rubric import names_a_centre
from ..intelligence import events

logger = logging.getLogger(__name__)

# The description is in the page's <head>, a few KB in; stop reading there.
_MAX_BYTES = 64 * 1024
# Stories kept per sitemap per scrape, newest first: a bound on page fetches.
MAX_STORIES = 10   # GCC stories
MAX_MOVES = 8      # leadership moves, as many as People Movers shows

_MOVES = ('EXECUTIVE_APPOINTMENT', 'EXECUTIVE_EXIT')


def is_a_move(title):
    """An appointment or exit the headline itself states (as build_movers requires)."""
    return any(event['type'] in _MOVES and any(x['where'] == 'title' for x in event['evidence'])
               for event in events.detect(title))

_META_TAG = re.compile(r'<meta\b[^>]*>', re.I)
_ATTR = re.compile(r'([\w:-]+)\s*=\s*["\']([^"\']*)["\']')
_DESCRIPTION_KEYS = ('og:description', 'description', 'twitter:description')


def parse_news_sitemap(xml):
    """Every story in a Google News sitemap, newest first, as scraper article dicts."""
    soup = BeautifulSoup(xml, 'xml')
    stories = []
    for entry in soup.find_all('url'):
        loc, title = entry.find('loc'), entry.find('title')
        published = entry.find('publication_date')
        if not (loc and title and published):
            continue
        image = entry.find('image')
        image_loc = image.find('loc') if image else None
        stories.append({
            'title': unescape(title.text.strip()),
            'url': loc.text.strip(),
            'date': published.text.strip(),
            'summary': '',
            'image': image_loc.text.strip() if image_loc else '',
            'author': '',
        })
    stories.sort(key=lambda s: s['date'], reverse=True)
    return stories


def page_description(html):
    """The page's own description (og:description first), or ''."""
    found = {}
    for tag in _META_TAG.findall(html):
        attrs = {k.lower(): v for k, v in _ATTR.findall(tag)}
        key = (attrs.get('property') or attrs.get('name') or '').lower()
        if key in _DESCRIPTION_KEYS and attrs.get('content', '').strip():
            found.setdefault(key, unescape(attrs['content'].strip()))
    return next((found[k] for k in _DESCRIPTION_KEYS if k in found), '')


async def _describe(session, story):
    """Fill the story's summary from its page; a failed fetch leaves it empty."""
    try:
        async with session.get(story['url'], timeout=aiohttp.ClientTimeout(total=10)) as response:
            response.raise_for_status()
            raw = await response.content.read(_MAX_BYTES)
    except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
        logger.info("No description for %s: %s", story['url'], e)
        return
    story['summary'] = page_description(raw.decode('utf-8', errors='replace'))


async def scrape_news_sitemap(session, url, fetch):
    """The GCC stories and leadership moves in one news sitemap, described
    from their pages.

    ``fetch`` is scraper.fetch_html (session, url -> bytes or None).
    """
    xml = await fetch(session, url)
    if not xml:
        return []
    listed = parse_news_sitemap(xml)
    gcc = [s for s in listed if names_a_centre(s['title'])][:MAX_STORIES]
    moves = [s for s in listed if not names_a_centre(s['title']) and is_a_move(s['title'])][:MAX_MOVES]
    stories = gcc + moves
    await asyncio.gather(*(_describe(session, s) for s in stories))
    logger.info("%s: %d GCC stories, %d leadership moves", url, len(gcc), len(moves))
    return stories
