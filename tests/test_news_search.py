"""GCC stories from Google News searches (app/scraper/news_search.py, BP-53).

On 2026-09-28 the GCC feeds had nothing from the last 24 hours (the ET GCC
portal's newest story was Friday evening's) while these searches found five,
among them WACKER's GCC with HCLTech. No network: fetches are stubbed.
"""
import asyncio
import os
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest import mock

from app.analysis.command_center import build_movers
from app.analysis.signals import synthesize_signals
from app.intelligence.normalize import parse_date
from app.scraper import article_images, news_search, scraper
from app.scraper.sources import NEWS_SEARCHES

WHEN = format_datetime(datetime.now(timezone.utc) - timedelta(hours=3), usegmt=True)


def _item(title, source, site, n):
    return f"""<item><title>{title} - {source}</title>
 <link>https://news.google.com/rss/articles/CBMi{n}?oc=5</link>
 <pubDate>{WHEN}</pubDate><description>&lt;a href="x"&gt;{title}&lt;/a&gt;</description>
 <source url="{site}">{source}</source></item>"""


FEED = f"""<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>GCC India</title>
{_item('WACKER establishes Global Capability Center in India with HCLTech', 'Indian Chemical News', 'https://www.indianchemicalnews.com', 1)}
{_item('GCC anti-rabies drive: Over 1L dogs vaccinated', 'The Times of India', 'https://timesofindia.indiatimes.com', 2)}
{_item('GCC leaders meet in Doha to discuss Gulf security', 'Al Jazeera', 'https://www.aljazeera.com', 3)}
{_item('Dr. Ankita Gupta Appointed Chief Business Officer at Fidelitus GCC Nexus', 'hrtoday.in', 'https://hrtoday.in', 4)}
</channel></rss>""".encode('utf-8')


def _run(coro):
    return asyncio.run(coro)


async def _serve(session, url):
    return FEED


class TestParsing(unittest.TestCase):
    def setUp(self):
        self.stories = news_search.parse_search_feed(FEED)

    def test_every_result_is_read(self):
        self.assertEqual(len(self.stories), 4)

    def test_the_headline_loses_the_publisher_suffix_and_keeps_its_source(self):
        first = self.stories[0]
        self.assertEqual(first['title'], 'WACKER establishes Global Capability Center in India with HCLTech')
        self.assertEqual(first['source'], 'Indian Chemical News')
        self.assertEqual(first['source_url'], 'https://www.indianchemicalnews.com')
        self.assertTrue(news_search.is_search_link(first['url']))

    def test_dates_pass_the_freshness_parser(self):
        self.assertIsNotNone(parse_date(self.stories[0]['date']))


class TestScreening(unittest.TestCase):
    def test_keeps_only_what_the_gcc_rubric_accepts(self):
        titles = [s['title'] for s in _run(news_search.scrape_news_search(None, NEWS_SEARCHES[0], _serve))]
        self.assertEqual(titles, ['WACKER establishes Global Capability Center in India with HCLTech',
                                  'Dr. Ankita Gupta Appointed Chief Business Officer at Fidelitus GCC Nexus'])

    def test_the_chennai_corporation_and_the_gulf_are_not_gccs(self):
        self.assertFalse(news_search.is_gcc_story('GCC anti-rabies drive: Over 1L dogs vaccinated'))
        self.assertFalse(news_search.is_gcc_story('GCC leaders meet in Doha to discuss Gulf security'))

    def test_an_unreachable_feed_gives_nothing(self):
        async def fail(session, url):
            return None
        self.assertEqual(_run(news_search.scrape_news_search(None, NEWS_SEARCHES[0], fail)), [])

    def test_results_are_capped(self):
        many = FEED.replace(b'</channel>', b''.join(
            _item(f'Firm{i} opens GCC in Pune', 'Mint', 'https://www.livemint.com', 10 + i).encode()
            for i in range(30)) + b'</channel>')

        async def serve(session, url):
            return many
        self.assertEqual(len(_run(news_search.scrape_news_search(None, NEWS_SEARCHES[0], serve))),
                         news_search.MAX_STORIES)


class TestDownstream(unittest.TestCase):
    def test_no_share_image_is_fetched_for_a_search_link(self):
        asked = []
        article = {'title': 'x', 'url': 'https://news.google.com/rss/articles/CBMi1', 'image': ''}
        article_images.fill_missing_images([article], fetch=lambda urls: asked.extend(urls) or [])
        self.assertEqual(asked, [])

    def test_a_mover_found_by_search_is_credited_to_its_publisher(self):
        story = news_search.parse_search_feed(FEED)[3]
        moves = build_movers([story])
        self.assertEqual([m['source'] for m in moves], ['hrtoday.in'])

    def test_a_publishers_own_copy_wins_over_the_search_copy(self):
        title = 'WACKER establishes Global Capability Center in India with HCLTech'
        own = {'title': title, 'url': 'https://www.indianchemicalnews.com/wacker-gcc', 'date': WHEN, 'summary': ''}
        found = news_search.parse_search_feed(FEED)[0]
        tiles = synthesize_signals([own, found])
        urls = [a['url'] for s in tiles['signals'] for a in s['articles']]
        self.assertEqual(urls, ['https://www.indianchemicalnews.com/wacker-gcc'])


class TestWiring(unittest.TestCase):
    def test_searches_are_google_news_limited_to_the_last_day(self):
        self.assertTrue(NEWS_SEARCHES)
        for url in NEWS_SEARCHES:
            self.assertTrue(url.startswith('https://news.google.com/rss/search?'), url)
            self.assertIn('when%3A1d', url)

    def test_the_scrape_lists_search_stories_after_the_feeds(self):
        async def feed(session, url):
            return [{'title': 'Acme opens GCC in Pune', 'url': 'https://www.livemint.com/acme',
                     'date': WHEN, 'summary': '', 'image': '', 'author': ''}]

        async def no_sitemap(session, url, fetch):
            return []

        async def search(session, url, fetch):
            return [{'title': 'Acme opens GCC in Pune', 'url': f'https://news.google.com/rss/articles/{len(url)}',
                     'date': WHEN, 'summary': '', 'image': '', 'author': '', 'source': 'Mint', 'source_url': ''}]

        with mock.patch.object(scraper, 'TIER_1_SOURCES', {}), \
             mock.patch.object(scraper, 'TIER_2_SOURCES', {'x': ['https://www.livemint.com/rss']}), \
             mock.patch.object(scraper, 'scrape_source', feed), \
             mock.patch.object(scraper, 'scrape_news_sitemap', no_sitemap), \
             mock.patch.object(scraper, 'scrape_news_search', search), \
             mock.patch.dict(os.environ, {'NEWS_API_KEY': ''}):
            articles = _run(scraper.run_scraper())
        self.assertEqual(articles[0]['url'], 'https://www.livemint.com/acme')
        self.assertTrue(all(news_search.is_search_link(a['url']) for a in articles[1:]))


if __name__ == '__main__':
    unittest.main()
