"""The global feed registry: config/feeds.yaml (2026-10-26).

Each feed is the publisher's own RSS, or a Google News search (`site:` for
one publisher), or RSS with a search to fall back on when the feed returns
nothing. Every story it yields carries its feed's tags:

    feed_id, source, source_country, region, language, sectors, trade,
    press_release

so the screen (app/analysis/screen.py), the language gate
(app/intelligence/language.py) and the country cap (app/analysis/geo_cap.py)
can read them. A search result links through news.google.com and names its
publisher, as with the GCC searches (news_search.py).
"""
import logging
import pathlib
from functools import lru_cache
from urllib.parse import quote

import yaml

from .news_search import parse_search_feed

logger = logging.getLogger(__name__)

CONFIG = pathlib.Path(__file__).resolve().parents[2] / "config" / "feeds.yaml"

MAX_ITEMS = 20          # per feed, newest first in the feed's own order
_GOOGLE_NEWS = "https://news.google.com/rss/search?hl={hl}&gl={gl}&ceid={ceid}&q="


@lru_cache(maxsize=1)
def load():
    """The registry as parsed YAML ({} when the file is missing)."""
    if not CONFIG.exists():
        return {}
    return yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}


def feeds():
    """Every enabled feed in config/feeds.yaml."""
    return [f for f in (load().get("feeds") or []) if f.get("enabled", True)]


def search_url(query, edition=None, language="en"):
    """A Google News search for the last day, in the given edition."""
    cfg = load()
    edition = edition or (cfg.get("default_edition") or {}).get(language, "en-US")
    hl, gl, ceid = (cfg.get("editions") or {}).get(edition, ["en-US", "US", "US:en"])
    return _GOOGLE_NEWS.format(hl=hl, gl=gl, ceid=quote(ceid, safe="")) + quote(f"{query} when:1d")


def tag(story, feed):
    """The story with its feed's tags; a search result keeps its publisher."""
    story.update({
        "feed_id": feed["id"],
        "source": story.get("source") or feed.get("name", ""),
        "source_country": feed.get("country"),
        "region": feed.get("region"),
        "language": feed.get("language", "en"),
        "sectors": list(feed.get("sectors") or []),
        "trade": bool(feed.get("trade")),
        "press_release": bool(feed.get("press_releases")),
    })
    return story


async def _search(session, query, feed, fetch):
    xml = await fetch(session, search_url(query, feed.get("edition"), feed.get("language", "en")))
    return parse_search_feed(xml) if xml else []


async def scrape_feed(session, feed, fetch, parse_rss):
    """One registry feed's stories, tagged. ``fetch`` is scraper.fetch_html;
    ``parse_rss`` is scraper.scrape_source with the feed read as XML."""
    stories = []
    if feed.get("url"):
        stories = await parse_rss(session, feed["url"], force_xml=True)
        if not stories and feed.get("fallback"):
            stories = await _search(session, feed["fallback"], feed, fetch)
    elif feed.get("search"):
        stories = await _search(session, feed["search"], feed, fetch)
    stories = stories[: feed.get("max_items", MAX_ITEMS)]
    logger.info("feed %s: %d stories", feed["id"], len(stories))
    return [tag(s, feed) for s in stories]
