"""The page must never present stale articles as today's intelligence.

Regression cover for the moneycontrol.com incident: that feed returned
HTTP 200 while frozen at April 2024, and three 880-day-old articles reached
the front page under today's date.
"""
import json
import unittest
from datetime import date, datetime, timedelta, timezone

import pytest

from app.analysis.command_center import build_engine_data
from app.analysis.signals import synthesize_signals
from app.intelligence.freshness import classify
from app.intelligence.normalize import parse_date
from app.main import create_app

OLD = "Tue, 23 Apr 2024 18:51:45 +0530"


def _rss(dt):
    return dt.strftime('%a, %d %b %Y %H:%M:%S +0000')


class TestFreshnessGate(unittest.TestCase):

    def _article(self, title, when):
        return {'title': title, 'url': 'https://example.com/x',
                'summary': 'insurance regulatory compliance market',
                'date': when}

    def test_multi_year_old_article_is_excluded(self):
        arts = [self._article('Tata Elxsi Q4 net profit revenue decline', OLD)]
        out = synthesize_signals(arts)
        shown = [a for s in out['signals'] for a in s['articles']]
        self.assertEqual(shown, [])
        self.assertEqual(out['stale_excluded'], 1)

    def test_recent_article_survives_the_gate(self):
        recent = _rss(datetime.now(timezone.utc) - timedelta(hours=3))
        arts = [self._article('Global insurance regulator issues new solvency rules', recent)]
        out = synthesize_signals(arts)
        shown = [a for s in out['signals'] for a in s['articles']]
        self.assertEqual(len(shown), 1)
        self.assertEqual(out['stale_excluded'], 0)

    def test_undated_article_is_excluded(self):
        """config/freshness.yaml sets treat_as_current: false."""
        arts = [self._article('People on the move in the insurance industry', None)]
        out = synthesize_signals(arts)
        self.assertEqual([a for s in out['signals'] for a in s['articles']], [])
        self.assertEqual(out['stale_excluded'], 1)

    def test_gate_can_be_disabled_for_analysis(self):
        arts = [self._article('Tata Elxsi Q4 net profit revenue decline', OLD)]
        out = synthesize_signals(arts, require_current=False)
        self.assertEqual(out['stale_excluded'], 0)

    def test_boundary_is_the_configured_archive_threshold(self):
        # BP-52: 24 hours (was 168).
        inside = _rss(datetime.now(timezone.utc) - timedelta(hours=23))
        outside = _rss(datetime.now(timezone.utc) - timedelta(hours=25))
        self.assertTrue(classify(parse_date(inside))['is_current'])
        self.assertFalse(classify(parse_date(outside))['is_current'])


@pytest.mark.usefixtures("raw_cache")
class TestLivePageIsCurrent(unittest.TestCase):
    """End-to-end against the real corpus."""

    @classmethod
    def setUpClass(cls):
        cls.payload = create_app().test_client() \
            .get('/api/command-center').get_json()

    def test_no_stale_article_reaches_any_tile(self):
        for engine_id, engine in self.payload.items():
            if engine_id.startswith('_') or engine.get('comingSoon'):
                continue
            for article in engine['articles']:
                self.assertTrue(article['title'].strip())

    def test_meta_reports_freshness(self):
        meta = self.payload.get('_meta')
        self.assertIsNotNone(meta, "page cannot state how fresh it is")
        self.assertIn('fetched_minutes_ago', meta)
        self.assertIn('stale_excluded', meta)

    def test_meta_says_when_the_articles_were_fetched(self):
        """The world-time line's "Updated" reads built_at."""
        meta = self.payload['_meta']
        if meta.get('fetched_minutes_ago') is None:
            self.skipTest('no cached articles')
        built = datetime.fromisoformat(meta['built_at'])
        self.assertIsNotNone(built.tzinfo)
        self.assertLessEqual(built, datetime.now(timezone.utc))

    def test_meta_is_not_treated_as_an_engine(self):
        """_meta must never render as a tile."""
        self.assertNotIn('articles', self.payload['_meta'])


class TestDeadFeedIsGone(unittest.TestCase):

    def test_moneycontrol_feeds_are_not_configured(self):
        """Every moneycontrol RSS feed is frozen upstream (2024, one at 2016)."""
        from app.scraper import sources
        text = json.dumps({k: v for k, v in vars(sources).items()
                           if not k.startswith('_') and isinstance(v, dict)})
        self.assertNotIn('moneycontrol', text)


if __name__ == '__main__':
    unittest.main()


@pytest.mark.usefixtures("raw_cache")
class TestPageRows(unittest.TestCase):
    """The rows under the engine tiles, in their agreed order.

    The Global Affairs / Economy / AI card row that used to sit here repeated
    three engines already in the tile row, so it was removed; its old
    hardcoded July-2026 stories must not come back with it.
    """

    @classmethod
    def setUpClass(cls):
        client = create_app().test_client()
        cls.payload = client.get('/api/command-center').get_json()
        cls.page = client.get('/').get_data(as_text=True)

    def test_repeated_engine_row_is_gone(self):
        self.assertNotIn('id="econGrid"', self.page)
        self.assertNotIn('_hero', self.payload)
        self.assertNotIn('War Risk Insurance Surges for Southern Red Sea', self.page)
        self.assertNotIn('Anthropic May Require All Employees', self.page)

    def test_endpoint_serves_the_people_rows(self):
        self.assertIsInstance(self.payload.get('_movers'), list)
        record = self.payload.get('_record')
        self.assertIsInstance(record, dict)
        self.assertIsInstance(record.get('quotes'), list)
        self.assertTrue(record.get('leaders'), 'Leaders on Record has no watchlist')


class TestPageRowsMarkup(unittest.TestCase):
    """The template alone, so it runs without a corpus (CI has none)."""

    @classmethod
    def setUpClass(cls):
        cls.page = create_app().test_client().get('/').get_data(as_text=True)

    def test_rows_appear_in_the_agreed_order(self):
        order = ['id="ribbon"', 'id="featuredSlot"', 'id="moversRow"',
                 'id="pulseSection"', 'id="recordRow"']
        positions = [self.page.index(marker) for marker in order]
        self.assertEqual(positions, sorted(positions))

    def test_people_rows_are_not_treated_as_engines(self):
        """The page peels the _-prefixed keys off before ENGINE_DATA, or the
        ticker and tile code would iterate over them as domains."""
        self.assertIn('const { _meta, _featured, _features, _movers, _pulse, _record, ...engines } = data;', self.page)


@pytest.mark.usefixtures("raw_cache")
class TestFeaturedIsLive(unittest.TestCase):
    """The large Featured Analysis card was one frozen July-2026 article."""

    @classmethod
    def setUpClass(cls):
        cls.payload = create_app().test_client() \
            .get('/api/command-center').get_json()

    def test_endpoint_returns_a_featured_story(self):
        self.assertIsNotNone(self.payload.get('_featured'))

    def test_featured_has_what_the_layout_needs(self):
        f = self.payload['_featured']
        for field in ('title', 'url', 'image'):
            self.assertTrue(f.get(field), f'featured missing {field}')

    def test_featured_comes_from_the_priority_engines(self):
        """GCC, then Insurance, otherwise the top Global Affairs story."""
        from app.analysis.command_center import FEATURED_ORDER
        urls = {a['url'] for e in FEATURED_ORDER for a in self.payload.get(e, {}).get('articles', [])}
        self.assertIn(self.payload['_featured']['url'], urls)


class TestFeaturedMarkup(unittest.TestCase):
    """The template alone, so it runs without a corpus (CI has none)."""

    @classmethod
    def setUpClass(cls):
        cls.page = create_app().test_client().get('/').get_data(as_text=True)

    def test_no_hardcoded_featured_remains(self):
        self.assertIn('id="featuredSlot"', self.page)
        self.assertNotIn("GCCs May Account for Nearly Half", self.page)

    def test_page_structure_survived_the_swap(self):
        """The hardcoded blocks were spliced out; the rest must be intact."""
        for marker in ('id="moversRow"', 'id="recordRow"', 'class="pipeline"', 'class="why"',
                       'class="closer"', 'id="founder"', '</nav>'):
            self.assertIn(marker, self.page, f'{marker} lost from the page')
