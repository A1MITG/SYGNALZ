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
"""
import logging
from html import unescape
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..analysis.gcc_rubric import MIN_SCORE, gcc_means_gulf, score_gcc

logger = logging.getLogger(__name__)

MAX_STORIES = 15        # per search
HEADLINE_WEIGHT = 2     # signals.TITLE_MULTIPLIER: a result is scored on its headline alone
SEARCH_HOSTS = frozenset({'news.google.com'})


def is_search_link(url):
    """True for a news-search redirect link: it has no page of its own to read."""
    return urlparse(url or '').netloc in SEARCH_HOSTS


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
    """The GCC tile's own rubric, on the headline; the Gulf Cooperation Council is not a GCC."""
    return score_gcc(title, '', HEADLINE_WEIGHT) >= MIN_SCORE and not gcc_means_gulf(title)


async def scrape_news_search(session, url, fetch):
    """The GCC stories in one search feed. ``fetch`` is scraper.fetch_html."""
    xml = await fetch(session, url)
    if not xml:
        return []
    stories = [s for s in parse_search_feed(xml) if is_gcc_story(s['title'])][:MAX_STORIES]
    logger.info("%s: %d GCC stories", url, len(stories))
    return stories
