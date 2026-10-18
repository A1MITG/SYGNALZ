"""Good-news and CSR sources for Bloom (the /bloom page).

Kept apart from sources.py on purpose: these feeds never reach the Command
Center tiles, the Signals page or Today's Brief, so adding or dropping one
here cannot reshuffle the rest of the site.

Two kinds of source:
  * DEDICATED_FEEDS publish only good news, so a story from them needs
    only to avoid Bloom's "never" words (app/analysis/bloom.py).
  * CSR_FEEDS and GOOD_NEWS_SEARCHES carry general news too, so a story from
    them must also carry a good-news cue.

Added 2026-10-18 untested from the cloud workspace (its network blocks these
hosts); the first GitHub build's log says how many stories each one gave.
"""
import asyncio
import logging
from urllib.parse import quote

import aiohttp

from .news_search import parse_search_feed
from .scraper import fetch_html, scrape_source

logger = logging.getLogger(__name__)

DEDICATED_FEEDS = (
    "https://www.goodnewsnetwork.org/feed/",
    "https://www.positive.news/feed/",
    "https://reasonstobecheerful.world/feed/",
    "https://www.theguardian.com/world/series/the-upside/rss",
    "https://www.optimistdaily.com/feed/",
    "https://www.yesmagazine.org/feed",
    # India: individual changemakers.
    "https://thebetterindia.com/feed/",
)

# CSR outlets that also run general news (on 2026-10-18 The CSR Journal's feed
# carried a stampede and an attack beside its CSR stories), so their stories
# are checked like search results: a cue and no NEVER word.
CSR_FEEDS = (
    "https://thecsrjournal.in/feed/",
    "https://indiacsr.in/feed/",
)

_GOOGLE_NEWS = "https://news.google.com/rss/search?hl=en-IN&gl=IN&ceid=IN:en&q="
_GOOGLE_NEWS_WORLD = "https://news.google.com/rss/search?hl=en&gl=US&ceid=US:en&q="
GOOD_NEWS_SEARCHES = (
    tuple(_GOOGLE_NEWS + quote(q) for q in (
        '"CSR initiative" when:7d',
        'CSR (foundation OR donates OR scholarship OR villages) when:7d',
        '(donates OR volunteers OR "gives back") India when:7d',
    ))
    + tuple(_GOOGLE_NEWS_WORLD + quote(q) for q in (
        '(heartwarming OR "good news" OR kindness) when:7d',
        '(donates OR volunteers OR rescued) hero when:7d',
    ))
)

MAX_PER_SEARCH = 20


async def _search(session, url):
    xml = await fetch_html(session, url)
    return parse_search_feed(xml)[:MAX_PER_SEARCH] if xml else []


async def _scrape_all():
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                             '(KHTML, like Gecko) Chrome/124.0 Safari/537.36'}
    async with aiohttp.ClientSession(headers=headers) as session:
        feeds = [scrape_source(session, u) for u in DEDICATED_FEEDS]
        feeds += [scrape_source(session, u) for u in CSR_FEEDS]
        searches = [_search(session, u) for u in GOOD_NEWS_SEARCHES]
        results = await asyncio.gather(*feeds, *searches, return_exceptions=True)

    articles = []
    urls = DEDICATED_FEEDS + CSR_FEEDS + GOOD_NEWS_SEARCHES
    for i, (url, result) in enumerate(zip(urls, results)):
        if isinstance(result, Exception):
            logger.warning("Bloom source failed: %s: %s", url, result)
            continue
        logger.info("Bloom source %s: %d stories", url, len(result))
        dedicated = i < len(DEDICATED_FEEDS)
        for a in result:
            a['dedicated'] = dedicated
            articles.append(a)
    return articles


def get_good_news():
    """Every story the Bloom sources list right now, each tagged `dedicated`."""
    articles = asyncio.run(_scrape_all())
    return list({a['url']: a for a in articles if a.get('url')}.values())
