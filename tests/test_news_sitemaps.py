"""GCC stories from news sitemaps (app/scraper/news_sitemaps.py).

Moneycontrol's RSS feeds froze in April 2024, but its Google News sitemap is
live. On 2026-09-25 it listed two GCC stories no other source carried. Only
stories whose headline names a capability centre are taken, each described
from its own page. No network: fetches are stubbed.
"""
import asyncio
import os
import unittest
from unittest import mock

from app.analysis.signals import classify_article
from app.intelligence.normalize import parse_date, publisher_for
from app.scraper import news_sitemaps, scraper
from app.scraper.sources import NEWS_SITEMAPS

SITEMAP = b"""<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"
        xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"
        xmlns:image="http://www.google.com/schemas/sitemap-image/1.1">
<url><loc>https://www.moneycontrol.com/technology/indias-tier-2-gcc-growth-article-14037713.html</loc>
 <news:news><news:publication><news:name>Moneycontrol</news:name><news:language>en</news:language></news:publication>
 <news:publication_date>2026-09-25T09:18:56+05:30</news:publication_date>
 <news:title><![CDATA[India\xe2\x80\x99s Tier-2 GCC growth brings infrastructure, talent into focus]]></news:title></news:news>
 <image:image><image:loc>https://images.moneycontrol.com/gcc.jpg</image:loc></image:image></url>
<url><loc>https://www.moneycontrol.com/news/business/tobacco-farmers-loan-14038510.html</loc>
 <news:news><news:publication><news:name>Moneycontrol</news:name><news:language>en</news:language></news:publication>
 <news:publication_date>2026-09-25T19:55:45+05:30</news:publication_date>
 <news:title><![CDATA[Govt approves one-time interest-free loan of Rs 50,000 per barn for tobacco farmers]]></news:title></news:news></url>
<url><loc>https://www.moneycontrol.com/news/world/gcc-visa-14038000.html</loc>
 <news:news><news:publication><news:name>Moneycontrol</news:name><news:language>en</news:language></news:publication>
 <news:publication_date>2026-09-25T12:00:00+05:30</news:publication_date>
 <news:title><![CDATA[GCC countries approve unified tourist visa]]></news:title></news:news></url>
<url><loc>https://www.moneycontrol.com/technology/gccs-cut-thousands-of-jobs-article-14038260.html</loc>
 <news:news><news:publication><news:name>Moneycontrol</news:name><news:language>en</news:language></news:publication>
 <news:publication_date>2026-09-25T14:55:37+05:30</news:publication_date>
 <news:title><![CDATA[GCCs cut thousands of jobs even as India takes on bigger global mandates]]></news:title></news:news></url>
</urlset>"""

PAGE = ('<html><head><meta name="description" content="Fallback description.">'
        '<meta property="og:description" content="A new report by 3AI QuantX estimates a '
        '20-25 percent gap in advanced AI and data roles.">'
        '</head><body>...</body></html>')


def _run(coro):
    return asyncio.run(coro)


async def _fetch(session, url):
    return SITEMAP


async def _describe(session, story):
    story['summary'] = 'Described from the page.'


class TestParse(unittest.TestCase):

    def test_every_story_newest_first(self):
        stories = news_sitemaps.parse_news_sitemap(SITEMAP)
        self.assertEqual(len(stories), 4)
        self.assertEqual(stories[0]['date'], '2026-09-25T19:55:45+05:30')
        tier2 = next(s for s in stories if 'Tier-2' in s['title'])
        self.assertEqual(tier2['title'], 'India’s Tier-2 GCC growth brings infrastructure, talent into focus')
        self.assertEqual(tier2['image'], 'https://images.moneycontrol.com/gcc.jpg')
        self.assertTrue(tier2['url'].endswith('-14037713.html'))

    def test_dates_pass_the_freshness_parser(self):
        for story in news_sitemaps.parse_news_sitemap(SITEMAP):
            self.assertIsNotNone(parse_date(story['date']), story['date'])

    def test_an_entry_without_a_headline_is_skipped(self):
        broken = SITEMAP.replace(b'<news:title><![CDATA[GCC countries approve unified tourist visa]]></news:title>', b'')
        self.assertEqual(len(news_sitemaps.parse_news_sitemap(broken)), 3)

    def test_description_prefers_og(self):
        self.assertTrue(news_sitemaps.page_description(PAGE).startswith('A new report by 3AI QuantX'))
        self.assertEqual(news_sitemaps.page_description('<meta name="description" content="Only this.">'),
                         'Only this.')
        self.assertEqual(news_sitemaps.page_description('<html></html>'), '')


class TestOnlyGCCStoriesAreTaken(unittest.TestCase):

    def test_keeps_capability_centre_headlines_only(self):
        """The tobacco story and the Gulf bloc's visa story stay out."""
        with mock.patch.object(news_sitemaps, '_describe', _describe):
            stories = _run(news_sitemaps.scrape_news_sitemap(None, NEWS_SITEMAPS[0], _fetch))
        self.assertEqual(sorted(s['title'][:10] for s in stories), ['GCCs cut t', 'India’s Ti'])
        self.assertTrue(all(s['summary'] == 'Described from the page.' for s in stories))

    def test_page_fetches_are_bounded(self):
        with mock.patch.object(news_sitemaps, '_describe', _describe), \
             mock.patch.object(news_sitemaps, 'MAX_STORIES', 1):
            stories = _run(news_sitemaps.scrape_news_sitemap(None, NEWS_SITEMAPS[0], _fetch))
        self.assertEqual([s['date'] for s in stories], ['2026-09-25T14:55:37+05:30'])

    def test_an_unreachable_sitemap_gives_nothing(self):
        async def fail(session, url):
            return None
        self.assertEqual(_run(news_sitemaps.scrape_news_sitemap(None, NEWS_SITEMAPS[0], fail)), [])

    def test_both_stories_reach_the_gcc_tile(self):
        for title, summary in (
                ('India’s Tier-2 GCC growth brings infrastructure, talent into focus',
                 'A new report by 3AI QuantX estimates a 20-25 percent gap in advanced AI and data roles.'),
                ('GCCs cut thousands of jobs even as India takes on bigger global mandates',
                 'EIIRTrend CEO Pareekh Jain puts the figure at 25,000-30,000.')):
            self.assertEqual(classify_article(title, summary, True)[0], 'Signal GCC', title)


class TestWiring(unittest.TestCase):

    def test_moneycontrol_is_read_from_its_sitemap_and_credited(self):
        self.assertIn('https://www.moneycontrol.com/news/news-sitemap.xml', NEWS_SITEMAPS)
        self.assertEqual(publisher_for('www.moneycontrol.com'), 'Moneycontrol')

    def test_the_scrape_includes_sitemap_stories(self):
        async def no_rss(session, url):
            return []

        async def gcc_story(session, url, fetch):
            return [{'title': 'Acme opens GCC in Pune', 'url': 'https://www.moneycontrol.com/a-1.html',
                     'date': '2026-09-25T09:00:00+05:30', 'summary': '', 'image': '', 'author': ''}]

        async def no_search(session, url, fetch):
            return []

        with mock.patch.object(scraper, 'scrape_source', no_rss), \
             mock.patch.object(scraper, 'scrape_news_sitemap', gcc_story), \
             mock.patch.object(scraper, 'scrape_news_search', no_search), \
             mock.patch.dict(os.environ, {'NEWS_API_KEY': ''}):
            articles = _run(scraper.run_scraper())
        self.assertEqual([a['url'] for a in articles], ['https://www.moneycontrol.com/a-1.html'])


if __name__ == '__main__':
    unittest.main()
