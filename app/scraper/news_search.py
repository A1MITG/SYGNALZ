"""GCC stories from news search feeds (BP-53).

The India GCC portals (Financial Express's GCC pages, Analytics India
Magazine, CXOToday, TechCircle) serve their feeds empty to a scraper, and
LinkedIn has no public feed and forbids scraping. Google News indexes those
portals and the rest of the Indian press, so a few GCC searches limited to
the last day (sources.NEWS_SEARCHES) catch what is reported, including the
openings and appointments first announced on LinkedIn. Only the stories the
GCC rubric accepts are kept, as with the news sitemaps.

A search result names its publisher (<source url="https://www.livemint.com">
Livemint</source>) but links through news.google.com. The link opens the
article for a reader, but there is no picture or summary to fetch, so a tile
shows the GCC artwork for it and Featured passes it over. When a publisher's
own feed carries the same story, that copy is the one shown: the scraper
lists search results last, and dedupe.py prefers a known publisher's link.

For the few search stories that reach the page, resolve_links() turns the
news.google.com link into the publisher's own (2026-10-26): the card then
links straight to the article, and article_images can read its photo.
"""
import asyncio
import base64
import json
import logging
import re
from html import unescape
from urllib.parse import quote, urlparse

from bs4 import BeautifulSoup

from ..analysis.gcc_rubric import read_gcc

logger = logging.getLogger(__name__)

MAX_STORIES = 15        # per search
SEARCH_HOSTS = frozenset({'news.google.com'})


def is_search_link(url):
    """True for a news-search redirect link: it has no page of its own to read."""
    return urlparse(url or '').netloc in SEARCH_HOSTS


_ARTICLE_ID = re.compile(r'/articles/([A-Za-z0-9_-]+)')
_SIGNATURE = re.compile(r'data-n-a-sg="([^"]+)"')
_TIMESTAMP = re.compile(r'data-n-a-ts="([^"]+)"')
_BATCH = 'https://news.google.com/_/DotsSplashUi/data/batchexecute'
_HEADERS = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36'}

# search link -> publisher link (or None), kept for the life of the process.
_resolved = {}


def article_id(url):
    """The article id in a Google News link, or None."""
    m = _ARTICLE_ID.search(urlparse(url or '').path)
    return m.group(1) if m else None


def legacy_url(aid):
    """Older ids carry the publisher's URL inside them, base64-encoded; newer
    ones ("AU_yqL...") only point at it and need resolve_one()."""
    try:
        raw = base64.urlsafe_b64decode(aid + '=' * (-len(aid) % 4))
    except (ValueError, TypeError):
        return None
    m = re.search(rb'https?://[\x21-\x7e]+', raw)
    if not m or b'AU_yqL' in raw:
        return None
    return m.group(0).decode('ascii')


def _batch_body(aid, timestamp, signature):
    req = ('["garturlreq",[["X","X",["X","X"],null,null,1,1,"US:en",null,1,null,null,null,null,null,0,1],'
           f'"X","X",1,[1,1,1],1,1,null,0,0,null,0],"{aid}",{timestamp},"{signature}"]')
    return 'f.req=' + quote(json.dumps([[['Fbv4je', req, None, 'generic']]]))


def parse_batch_reply(text):
    """The publisher URL in Google's batchexecute reply, or None."""
    try:
        rows = json.loads(text.split('\n\n', 1)[1])
        url = json.loads(rows[0][2])[1]
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    return url if isinstance(url, str) and url.startswith('http') else None


async def resolve_one(session, url):
    """The publisher's own link behind one Google News link, or None."""
    aid = article_id(url)
    if not aid:
        return None
    direct = legacy_url(aid)
    if direct:
        return direct
    try:
        async with session.get(f'https://news.google.com/articles/{aid}') as r:
            r.raise_for_status()
            page = await r.text()
        sig, ts = _SIGNATURE.search(page), _TIMESTAMP.search(page)
        if not (sig and ts):
            return None
        async with session.post(_BATCH, data=_batch_body(aid, ts.group(1), sig.group(1)),
                                headers={'Content-Type': 'application/x-www-form-urlencoded;charset=UTF-8'}) as r:
            r.raise_for_status()
            return parse_batch_reply(await r.text())
    except Exception as e:      # any failure leaves the search link as it was
        logger.info("Could not resolve %s: %s", url, e)
        return None


async def _resolve_all(urls):
    import aiohttp
    sem = asyncio.Semaphore(4)

    async def one(session, u):
        async with sem:
            return u, await resolve_one(session, u)

    async with aiohttp.ClientSession(headers=_HEADERS, timeout=aiohttp.ClientTimeout(total=15)) as session:
        return await asyncio.gather(*(one(session, u) for u in urls))


def resolve_links(urls, resolve=None):
    """{search link: publisher link} for each link that could be resolved.
    ``resolve`` (urls -> [(url, publisher or None)]) is injectable for tests."""
    todo = sorted({u for u in urls if is_search_link(u)} - _resolved.keys())
    if todo:
        for u, real in (resolve or (lambda us: asyncio.run(_resolve_all(us))))(todo):
            _resolved[u] = real if real and not is_search_link(real) else None
    return {u: _resolved[u] for u in urls if _resolved.get(u)}


def parse_search_feed(xml):
    """Every result in a Google News search feed, as scraper article dicts."""
    soup = BeautifulSoup(xml, 'xml')
    stories = []
    for item in soup.find_all('item'):
        title, link, published = item.find('title'), item.find('link'), item.find('pubDate')
        if not (title and link and published):
            continue
        source = item.find('source')
        publisher = source.text.strip() if source else ''
        headline = unescape(title.text.strip())
        # Google appends " - Publisher" to every headline.
        if publisher and headline.endswith(' - ' + publisher):
            headline = headline[:-len(publisher) - 3].rstrip()
        stories.append({
            'title': headline,
            'url': link.text.strip(),
            'date': published.text.strip(),
            'summary': '',
            'image': '',
            'author': '',
            'source': publisher,
            'source_url': (source.get('url') or '').strip() if source else '',
        })
    return stories


def is_gcc_story(title):
    """The GCC tile's own rubric, on the headline alone. It knows the other
    GCCs: the Gulf Cooperation Council and the Greater Chennai Corporation."""
    return read_gcc(title).is_gcc


async def scrape_news_search(session, url, fetch):
    """The GCC stories in one search feed. ``fetch`` is scraper.fetch_html."""
    xml = await fetch(session, url)
    if not xml:
        return []
    stories = [s for s in parse_search_feed(xml) if is_gcc_story(s['title'])][:MAX_STORIES]
    logger.info("%s: %d GCC stories", url, len(stories))
    return stories
