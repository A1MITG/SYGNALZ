# app/scraper/scraper.py
import asyncio
import logging
import os
import random
from html import unescape

import aiohttp
from bs4 import BeautifulSoup

from .news_search import scrape_news_search
from .news_sitemaps import scrape_news_sitemap
from .sources import EXCLUDE_URL_PARTS, NEWS_SEARCHES, NEWS_SITEMAPS, TIER_1_SOURCES, TIER_2_SOURCES

logger = logging.getLogger(__name__)


async def fetch_newsapi_articles(session, api_key):
    """Fetch articles from NewsAPI with insurance keywords."""
    url = f"https://newsapi.org/v2/everything?q=insurance+OR+insurer+OR+reinsurance&language=en&sortBy=publishedAt&pageSize=20&apiKey={api_key}"
    try:
        async with session.get(url) as response:
            if response.status == 200:
                data = await response.json()
                articles = []
                for item in data.get('articles', []):
                    # NewsAPI sometimes returns null (not missing) fields
                    # for removed/paywalled articles — `or ''` guards
                    # against that; `.get(k, '')` alone would not, since
                    # the key is present with value None.
                    title = (item.get('title') or '').strip()
                    url = item.get('url') or ''
                    date = item.get('publishedAt') or ''
                    if title and url:
                        articles.append({
                            'title': title,
                            'url': url,
                            'date': date
                        })
                return articles
            else:
                logger.warning("NewsAPI error: %s", response.status)
                return []
    except Exception as e:
        logger.warning("Error fetching NewsAPI: %s", e)
        return []

async def fetch_html(session, url):
    """Fetch raw response bytes from a single URL.

    Deliberately undecoded: aiohttp's response.text() trusts only the HTTP
    Content-Type header's charset (defaulting to utf-8 when absent), which
    several sources get wrong or omit. Handing raw bytes to BeautifulSoup
    instead lets it detect the real encoding from the document's own XML/
    HTML charset declaration, avoiding mojibake on misdeclared feeds.
    """
    # Add random delay to avoid rate limiting
    await asyncio.sleep(random.uniform(1, 3))
    try:
        async with session.get(url, timeout=10) as response:
            response.raise_for_status()
            return await response.read()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("Error fetching %s: %s", url, e)
        return None

async def scrape_source(session, url):
    """Scrape a single source for article links and titles."""
    html = await fetch_html(session, url)
    if not html:
        return []

    articles = []
    if url.endswith('.rss') or url.endswith('.xml') or 'rss' in url.lower() or 'feed' in url.lower():
        # Parse as RSS/XML
        soup = BeautifulSoup(html, 'xml')
        for item in soup.find_all(['item', 'entry']):
            title_tag = item.find('title')
            link_tag = item.find('link')
            date_tag = item.find('pubDate') or item.find('published') or item.find('updated')
            # The feed already carries a description; it was simply never read.
            # It is the summary the whole intelligence pipeline scores against.
            desc_tag = (item.find('description') or item.find('summary')
                        or item.find('content'))
            media = (item.find('enclosure') or item.find('media:content')
                     or item.find('media:thumbnail'))
            author_tag = item.find('author') or item.find('creator')
            if title_tag and link_tag:
                # Some feeds (ET Telecom) escape their titles twice, so one
                # parse still leaves "&amp;" in the text.
                title = unescape(title_tag.text.strip())
                link = link_tag.get('href') or link_tag.text.strip()
                if not link.startswith('http'):
                    link = url.rsplit('/', 1)[0] + '/' + link
                date = date_tag.text.strip() if date_tag else None
                # Descriptions are frequently escaped HTML — unwrap to text.
                summary = ''
                if desc_tag and desc_tag.text:
                    summary = BeautifulSoup(desc_tag.text, 'html.parser').get_text(
                        ' ', strip=True)[:1200]
                if title and len(title) > 10:
                    articles.append({
                        'title': title,
                        'url': link,
                        'date': date,
                        'summary': summary,
                        'image': (media.get('url') or '') if media else '',
                        'author': author_tag.text.strip() if author_tag else ''
                    })
    else:
        # Parse as HTML
        soup = BeautifulSoup(html, 'html.parser')
        # Basic HTML scraping: find article links
        count = 0
        for a_tag in soup.find_all('a', href=True):
            href = a_tag['href']
            title = a_tag.text.strip()
            if title and len(title) > 10 and len(title) < 200 and ('news' in href.lower() or 'article' in href.lower() or '/202' in href) and not any(word in title.lower() for word in ['home', 'about', 'contact', 'privacy', 'terms']):
                full_url = href if href.startswith('http') else url.rstrip('/') + '/' + href.lstrip('/')
                articles.append({
                    'title': title,
                    'url': full_url,
                    'date': None
                })
                count += 1
                if count >= 10:  # Limit to 10 per site
                    break
    return articles

async def run_scraper(tier='all'):
    """Run the scraper for the specified tier of sources."""
    sources_to_scan = []
    if tier == 'tier1' or tier == 'all':
        for category in TIER_1_SOURCES.values():
            sources_to_scan.extend(category)
    if tier == 'tier2' or tier == 'all':
        for category in TIER_2_SOURCES.values():
            sources_to_scan.extend(category)

    all_articles = []
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'}
    async with aiohttp.ClientSession(headers=headers) as session:
        tasks = [scrape_source(session, url) for url in sources_to_scan]
        # GCC stories from news sitemaps (publishers whose RSS is dead).
        if tier in ('tier2', 'all'):
            tasks += [scrape_news_sitemap(session, url, fetch_html) for url in NEWS_SITEMAPS]
            # GCC stories from news searches, last: a publisher's own copy of
            # the same story comes first and is the one kept.
            tasks += [scrape_news_search(session, url, fetch_html) for url in NEWS_SEARCHES]
        # return_exceptions so one source's parse failure can't abort the
        # whole scrape — the rest of the sources still return their articles.
        results = await asyncio.gather(*tasks, return_exceptions=True)
        for result in results:
            if isinstance(result, Exception):
                logger.warning("Source scrape failed: %s", result)
                continue
            all_articles.extend(result)

        # Add NewsAPI articles if key is available
        news_api_key = os.environ.get('NEWS_API_KEY', '')
        if news_api_key:
            news_articles = await fetch_newsapi_articles(session, news_api_key)
            all_articles.extend(news_articles)

    all_articles = [a for a in all_articles
                    if not any(part in (a.get('url') or '') for part in EXCLUDE_URL_PARTS)]

    # Deduplicate articles by URL
    all_articles = list({article['url']: article for article in all_articles if article.get('url')}.values())

    return all_articles
