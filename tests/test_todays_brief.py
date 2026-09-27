"""Today's Brief: the Command Center's stories as a text-only newsletter.

The closer's two buttons were both dead ends: "View Today's Brief" pointed at
#brief, which nothing on the page carries, and "Explore Signals" opened the
site's own front door in a new tab. One button now opens /brief: each tile's
five stories as a linked headline, its source and one line from the feed.
"""
import re
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from app.analysis.brief import (SUMMARY_LIMIT, TILE_ORDER, build_brief, one_line,
                                render_brief, source_name)
from app.main import create_app
from app.scraper import store

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'app' / 'static' / 'command_center_source.html'
WORKFLOW = ROOT / '.github' / 'workflows' / 'build-signals.yml'


class TestOneLine(unittest.TestCase):
    """The summary is the feed's own description, trimmed to one sentence.
    Each case is a quirk seen in the 2026-09-24 scrape."""

    def test_takes_the_first_sentence(self):
        self.assertEqual(
            one_line("Iran has offered to reopen the Strait of Hormuz within seven days. "
                     "Talks continue in New York."),
            "Iran has offered to reopen the Strait of Hormuz within seven days.")

    def test_skips_feed_filler(self):
        self.assertEqual(
            one_line("This live blog is now closed. Tehran appears to have relaxed its "
                     "preconditions for reopening the strait."),
            "Tehran appears to have relaxed its preconditions for reopening the strait.")
        self.assertEqual(one_line("Happy Wednesday! The return of MDR on UPI is pushing "
                                  "banks to rethink products."),
                         "The return of MDR on UPI is pushing banks to rethink products.")

    def test_does_not_stop_at_an_abbreviation(self):
        self.assertEqual(
            one_line("St. John's University will honor The Hartford's chief executive. Next."),
            "St. John's University will honor The Hartford's chief executive.")

    def test_does_not_stop_at_a_middle_initial(self):
        self.assertEqual(
            one_line("The school will honor Chairman and CEO Christopher J. Swift at its "
                     "annual dinner. Next."),
            "The school will honor Chairman and CEO Christopher J. Swift at its annual dinner.")

    def test_drops_the_appeared_first_on_trailer(self):
        self.assertEqual(
            one_line("Coca-Cola and its bottlers will expand production in the US The post "
                     "Coca-Cola to spend $10B appeared first on Supply Chain Dive."),
            "Coca-Cola and its bottlers will expand production in the US")

    def test_strips_a_repeated_headline(self):
        self.assertEqual(
            one_line("NSE shares debut today. The exchange's IPO was subscribed 5.7 times.",
                     title="NSE shares debut today"),
            "The exchange's IPO was subscribed 5.7 times.")

    def test_strips_markup_and_entities(self):
        self.assertEqual(
            one_line("<p>Banks warn AI shopping bots raise scam &amp; fraud risks.</p>"),
            "Banks warn AI shopping bots raise scam & fraud risks.")

    def test_caps_long_lines_on_a_word_boundary(self):
        text = "Supply " + "chains keep shifting across every major trade lane " * 6 + "now."
        line = one_line(text)
        self.assertTrue(line.endswith("…"))
        self.assertLessEqual(len(line), SUMMARY_LIMIT + 1)
        kept = line[:-1]
        self.assertTrue(text.startswith(kept))
        self.assertEqual(text[len(kept)], " ", "cut mid-word")

    def test_nothing_usable_is_empty(self):
        for summary in (None, "", "Happy Wednesday!", "Short.", "This live blog is now closed."):
            self.assertEqual(one_line(summary), "", summary)


class TestBuildBrief(unittest.TestCase):

    def setUp(self):
        self.engines = {
            'economy': {'name': 'Economy, Business & Markets', 'articles': [
                {'title': f'Economy story {i}', 'url': f'https://www.reuters.com/{i}'}
                for i in range(1, 9)]},
            'global': {'name': 'Global Affairs', 'articles': [
                {'title': 'Iran offers to reopen the strait', 'url': 'https://example.com/iran'}]},
            'healthcare': {'name': 'Healthcare', 'articles': []},
            'banking': {'name': 'Banking', 'comingSoon': True, 'note': 'soon'},
            '_featured': {'title': 'Not a tile', 'url': 'https://example.com/f'},
        }
        self.by_title = {'Economy story 1': {
            'summary': 'WhiteOak Capital is preparing an IPO that could raise Rs 1,500 crore. More.'}}
        self.sections = build_brief(self.engines, self.by_title)

    def test_follows_the_page_tile_order(self):
        self.assertEqual([s['name'] for s in self.sections],
                         ['Global Affairs', 'Economy, Business & Markets'])

    def test_carries_the_five_stories_a_tile_cycles(self):
        economy = self.sections[1]['items']
        self.assertEqual([i['title'] for i in economy],
                         [f'Economy story {i}' for i in range(1, 6)])

    def test_leaves_out_empty_and_coming_soon_tiles(self):
        names = {s['name'] for s in self.sections}
        self.assertNotIn('Healthcare', names)
        self.assertNotIn('Banking', names)

    def test_item_has_link_source_and_summary(self):
        first = self.sections[1]['items'][0]
        self.assertEqual(first['url'], 'https://www.reuters.com/1')
        self.assertEqual(first['source'], 'reuters.com')
        self.assertEqual(first['summary'],
                         'WhiteOak Capital is preparing an IPO that could raise Rs 1,500 crore.')

    def test_story_missing_from_the_corpus_has_no_summary(self):
        self.assertEqual(self.sections[0]['items'][0]['summary'], '')

    def test_source_name(self):
        self.assertEqual(source_name('https://www.bbc.co.uk/news/x'), 'bbc.co.uk')
        self.assertEqual(source_name('https://telecom.economictimes.indiatimes.com/x'),
                         'telecom.economictimes.indiatimes.com')


class TestTileOrderMatchesThePage(unittest.TestCase):

    def test_same_order_as_engine_order_in_the_page(self):
        match = re.search(r"const ENGINE_ORDER = \[([^\]]*)\]", SOURCE.read_text(encoding='utf-8'))
        page_order = tuple(re.findall(r"'(\w+)'", match.group(1)))
        self.assertEqual(TILE_ORDER, page_order)


class TestRenderedBrief(unittest.TestCase):

    def setUp(self):
        self.sections = [{'name': 'Global Affairs', 'items': [
            {'title': 'Iran offers to reopen the strait', 'url': 'https://www.reuters.com/iran',
             'source': 'reuters.com', 'summary': 'Iran has offered to reopen the strait within seven days.'},
            {'title': '<b>Bold</b> & co', 'url': 'https://example.com/b?x=1&y=2',
             'source': 'example.com', 'summary': ''},
        ]}]
        self.page = render_brief(self.sections, '2026-09-24',
                                 datetime(2026, 9, 24, 3, 30, tzinfo=timezone.utc))

    def test_is_text_only(self):
        self.assertNotIn('<img', self.page)
        # Text only: the one script allowed is Vercel's anonymous page-view counter.
        scripts = re.findall(r'<script[^>]*>.*?</script>', self.page, re.S)
        self.assertEqual(len(scripts), 2)
        self.assertTrue(all('window.va' in s or '/_vercel/insights/script.js' in s for s in scripts))
        self.assertNotIn('url(', self.page)

    def test_headlines_link_to_the_reporting(self):
        self.assertIn('<a href="https://www.reuters.com/iran" target="_blank" rel="noopener">'
                      'Iran offers to reopen the strait</a>', self.page)
        self.assertIn('<span class="src">reuters.com</span>', self.page)
        self.assertIn('Iran has offered to reopen the strait within seven days.', self.page)

    def test_dated_by_the_cache_and_updated_in_ist(self):
        self.assertIn('<h1>Thursday, 24 September 2026</h1>', self.page)
        self.assertIn('Updated 09:00 IST', self.page)
        self.assertIn('2 stories across 1 area', self.page)

    def test_escapes_feed_text(self):
        self.assertIn('&lt;b&gt;Bold&lt;/b&gt; &amp; co', self.page)
        self.assertNotIn('<b>Bold', self.page)
        self.assertIn('href="https://example.com/b?x=1&amp;y=2"', self.page)

    def test_story_without_a_summary_prints_no_empty_line(self):
        self.assertEqual(self.page.count('class="sum"'), 1)

    def test_links_back_to_the_command_center(self):
        self.assertIn('<a href="/">Back to the Command Center</a>', self.page)

    def test_empty_brief_says_so(self):
        page = render_brief([], None, None)
        self.assertIn('No stories yet', page)
        self.assertNotIn('0 stories', page)
        self.assertNotIn('Updated', page)


class TestBriefRoute(unittest.TestCase):
    """Flask's /brief renders the same page from the cache as it stands.

    It must never scrape: a reader would wait on it, and on CI (no cache) it
    would hit the network and write the corpus the other tests read.
    """

    def setUp(self):
        self.client = create_app().test_client()
        # Any scrape fails the test instead of reaching the network.
        patcher = mock.patch.object(store.asyncio, 'run',
                                    side_effect=AssertionError('/brief scraped'))
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_renders_the_cached_stories(self):
        recent = (datetime.now(timezone.utc) - timedelta(hours=3)).strftime(
            '%a, %d %b %Y %H:%M:%S +0000')
        cache = {'date': '2026-09-24', 'fetched_at': datetime.now(timezone.utc).isoformat(),
                 'articles': [{'title': 'Global insurance regulator issues new solvency rules',
                               'url': 'https://www.insurancejournal.com/news/solvency',
                               'summary': 'Regulators set out new solvency rules for insurers '
                                          'writing global business. More follows.',
                               'date': recent}]}
        with mock.patch.object(store, '_load', return_value=cache):
            resp = self.client.get('/brief')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('<h2>Insurance &amp; Financial Services</h2>', body)
        self.assertIn('href="https://www.insurancejournal.com/news/solvency"', body)
        self.assertIn('Regulators set out new solvency rules for insurers writing global business.', body)
        self.assertIn('Thursday, 24 September 2026', body)

    def test_without_a_cache_it_says_so_instead_of_scraping(self):
        with mock.patch.object(store, '_load', return_value=None):
            resp = self.client.get('/brief')
        self.assertEqual(resp.status_code, 200)
        self.assertIn('No stories yet', resp.get_data(as_text=True))


class TestCloserButton(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        body = create_app().test_client().get('/').get_data(as_text=True)
        start = body.index('<section class="closer">')
        cls.closer = body[start:body.index('</section>', start)]

    def test_one_button_opens_the_brief(self):
        self.assertEqual(self.closer.count('class="btn'), 1)
        self.assertIn('<a class="btn pri" href="/brief">Read Today\'s Brief</a>', self.closer)

    def test_dead_links_are_gone(self):
        self.assertNotIn('href="#brief"', self.closer)
        self.assertNotIn('Explore Signals', self.closer)


class TestStaticDeploy(unittest.TestCase):

    def test_workflow_publishes_the_brief_where_the_button_points(self):
        """/brief on the live site is brief.html, via cleanUrls."""
        workflow = WORKFLOW.read_text(encoding='utf-8')
        self.assertIn('cp public/brief.html /tmp/signals-deploy/brief.html', workflow)
        add = next(line for line in workflow.splitlines()
                   if 'git add' in line and 'signals.html' in line)
        self.assertIn(' brief.html ', add)
        self.assertIn('"cleanUrls": true', workflow)


if __name__ == '__main__':
    unittest.main()
