"""Command Center: the front-door page and its live data endpoint.

Follows the suite convention of testing against the real cached corpus
(see tests/conftest.py) rather than synthetic fixtures.
"""
import json
import unittest
from pathlib import Path

import pytest

from app.analysis.command_center import (
    COMING_SOON_ENGINES,
    SIGNAL_TO_ENGINE,
    build_engine_data,
)
from app.main import create_app

# The tile ids the Command Center page iterates over (ENGINE_ORDER in the HTML).
ENGINE_ORDER = ['global', 'economy', 'ai', 'gcc', 'insurance', 'banking',
                'manufacturing', 'energy', 'defence', 'cyber', 'supplychain',
                'healthcare', 'telecom', 'climate']


class TestEngineMapping(unittest.TestCase):
    """build_engine_data() must satisfy the contract the page renders against."""

    def test_covers_every_tile_the_page_renders(self):
        data = build_engine_data({'signals': []})
        self.assertEqual(set(data), set(ENGINE_ORDER))

    def test_live_and_coming_soon_split(self):
        data = build_engine_data({'signals': []})
        live = {k for k, v in data.items() if not v.get('comingSoon')}
        soon = {k for k, v in data.items() if v.get('comingSoon')}
        self.assertEqual(live, {e for e, _ in SIGNAL_TO_ENGINE.values()})
        self.assertEqual(soon, set(COMING_SOON_ENGINES))

    def test_every_engine_has_a_display_name(self):
        for engine_id, engine in build_engine_data({'signals': []}).items():
            self.assertTrue(engine.get('name'), f"{engine_id} has no name")

    def test_coming_soon_engines_carry_no_fabricated_articles(self):
        """The 9 unbuilt domains must never render invented content."""
        for engine_id in COMING_SOON_ENGINES:
            engine = build_engine_data({'signals': []})[engine_id]
            self.assertTrue(engine['comingSoon'])
            self.assertNotIn('articles', engine)
            self.assertTrue(engine.get('note'))

    def test_maps_real_signal_articles_onto_tiles(self):
        signals_data = {'signals': [
            {'name': 'Signal Global', 'articles': [
                {'title': 'A headline', 'url': 'https://example.com/a',
                 'extra_field': 'should be dropped'},
            ]},
        ]}
        data = build_engine_data(signals_data)
        self.assertEqual(data['global']['articles'],
                         [{'title': 'A headline', 'url': 'https://example.com/a',
                           'image': None}])

    def test_article_image_comes_from_the_raw_corpus(self):
        """A tile cycles its headlines; each needs its own picture.

        The image is not on the scored article, so build_engine_data takes
        the raw corpus keyed by title. Without it every slide reported no
        image and one baked-in photo sat under all five headlines.
        """
        signals_data = {'signals': [
            {'name': 'Signal Global', 'articles': [
                {'title': 'Has a photo', 'url': 'https://example.com/a'},
                {'title': 'Has none', 'url': 'https://example.com/b'},
            ]},
        ]}
        by_title = {
            'Has a photo': {'image': 'https://cdn.example.com/photo.jpg'},
            'Has none': {'image': ''},
        }
        articles = build_engine_data(signals_data, by_title)['global']['articles']
        self.assertEqual(articles[0]['image'], 'https://cdn.example.com/photo.jpg')
        self.assertIsNone(articles[1]['image'])

    def test_unusable_image_urls_are_reported_as_absent(self):
        """Only absolute http(s) urls qualify, as elsewhere on this page.

        A relative path or a scraper placeholder would render as a broken
        frame; reporting None lets the tile show its domain vector instead.
        """
        signals_data = {'signals': [
            {'name': 'Signal Global', 'articles': [
                {'title': 'Relative', 'url': 'https://example.com/a'},
                {'title': 'Placeholder', 'url': 'https://example.com/b'},
                {'title': 'Missing key', 'url': 'https://example.com/c'},
            ]},
        ]}
        by_title = {
            'Relative': {'image': '/static/img/thumb.jpg'},
            'Placeholder': {'image': 'data:image/gif;base64,R0lGOD'},
            'Missing key': {},
        }
        articles = build_engine_data(signals_data, by_title)['global']['articles']
        self.assertEqual([a['image'] for a in articles], [None, None, None])

    def test_missing_signal_yields_empty_not_error(self):
        """A signal that classified nothing gives an empty tile, not a crash."""
        data = build_engine_data({'signals': []})
        self.assertEqual(data['global']['articles'], [])


class TestRoutes(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()

    def test_root_serves_the_command_center(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn('Executive Intelligence', body)
        self.assertIn('/api/command-center', body)

    def test_compact_views_still_reachable(self):
        """The low-bandwidth reading views must survive the reshuffle."""
        self.assertEqual(self.client.get('/brief').status_code, 200)
        self.assertEqual(self.client.get('/signals').status_code, 200)

    def test_command_center_links_to_both_compact_views(self):
        body = self.client.get('/').get_data(as_text=True)
        self.assertIn('href="/brief"', body)
        self.assertIn('href="/signals"', body)

    def test_engine_count_is_not_hardcoded(self):
        """The live/coming-soon counts must derive from the data, not a literal."""
        body = self.client.get('/').get_data(as_text=True)
        self.assertIn('id="engineCount"', body)
        self.assertNotIn('12 live today', body)

    def test_health_still_ok(self):
        resp = self.client.get('/health')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.get_json(), {'status': 'ok'})


class TestFounderSection(unittest.TestCase):
    """The About the Builder section and its portrait (ids and classes keep
    the older "founder" name, so #founder links still land)."""

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()
        cls.body = cls.client.get('/').get_data(as_text=True)

    def test_section_exists_for_the_nav_anchor(self):
        """The nav has always linked to #founder; the target must exist."""
        self.assertIn('href="#founder"', self.body)
        self.assertIn('id="founder"', self.body)

    def test_labelled_about_the_builder(self):
        """Renamed from "About the Founder" on 2026-09-24: the nav link and
        the section's eyebrow."""
        self.assertIn('href="#founder">About the Builder</a>', self.body)
        self.assertIn('<p class="founder-eyebrow">About the Builder</p>', self.body)
        self.assertNotIn('About the Founder', self.body)

    def test_shows_name_roles_and_statement(self):
        self.assertIn('Amit Gupta', self.body)
        self.assertIn('Corporate Survivor', self.body)
        self.assertIn('a&nbsp;million tokens at a time!', self.body)

    def test_portrait_is_served_by_flask(self):
        """The template must use the Flask static path, not the relative one."""
        self.assertIn('/static/img/founder.jpg', self.body)
        resp = self.client.get('/static/img/founder.jpg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'image/jpeg')

    def test_portrait_has_accessible_alt_text(self):
        self.assertIn('alt="Portrait of the founder of SIGNAL"', self.body)


class TestListeningStripParked(unittest.TestCase):
    """The "Listening to" watchlist under Leaders on Record is hidden
    (2026-09-24). Parked, not removed: the data and the renderer stay, so
    removing the class brings it back."""

    @classmethod
    def setUpClass(cls):
        cls.body = create_app().test_client().get('/').get_data(as_text=True)

    def test_strip_is_parked(self):
        self.assertIn('<div class="listening parked" id="recordListening"></div>', self.body)
        self.assertIn('.parked { display: none !important; }', self.body)


class TestManifestoSection(unittest.TestCase):
    """The Builder's Manifesto (2026-09-24): the nav's "About" link, which
    pointed nowhere, now reads "Manifesto" and lands on it, just before
    About the Builder."""

    ROOT = Path(__file__).resolve().parents[1]

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()
        cls.body = cls.client.get('/').get_data(as_text=True)

    def test_nav_link_lands_on_the_section(self):
        self.assertIn('<a class="link" href="#manifesto">Manifesto</a>', self.body)
        self.assertIn('id="manifesto"', self.body)
        self.assertNotIn('href="#">About</a>', self.body)

    def test_sits_just_before_about_the_builder(self):
        start = self.body.index('<section class="manifesto" id="manifesto">')
        between = self.body[start + 1:self.body.index('<section class="founder" id="founder">')]
        self.assertNotIn('<section', between)
        self.assertEqual(between.count('class="mf-chapter"'), 4)

    def test_background_is_served_by_flask(self):
        self.assertIn('src="/static/img/manifesto-bg.jpg"', self.body)
        resp = self.client.get('/static/img/manifesto-bg.jpg')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'image/jpeg')

    def test_static_deploy_carries_the_background(self):
        """The static page reads ./img/, and the workflow must publish it."""
        self.assertIn('src="./img/manifesto-bg.jpg"',
                      (self.ROOT / 'public' / 'command_center.html').read_text(encoding='utf-8'))
        self.assertTrue((self.ROOT / 'public' / 'img' / 'manifesto-bg.jpg').is_file())
        workflow = (self.ROOT / '.github' / 'workflows' / 'build-signals.yml').read_text(encoding='utf-8')
        self.assertEqual(workflow.count('cp public/img/* '), 2, 'both deploy branches copy every image')


@pytest.mark.usefixtures("raw_cache")
class TestLiveEndpoint(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.client = create_app().test_client()
        cls.payload = cls.client.get('/api/command-center').get_json()

    def test_returns_every_tile(self):
        engines = {k: v for k, v in self.payload.items()
                   if not k.startswith('_')}
        self.assertEqual(set(engines), set(ENGINE_ORDER))

    def test_live_tiles_carry_real_articles_from_the_corpus(self):
        """The whole point: real scored articles reach the page."""
        live = [v for k, v in self.payload.items()
                if not k.startswith('_') and not v.get('comingSoon')]
        self.assertTrue(any(e['articles'] for e in live),
                        "no live engine returned any article")
        for engine in live:
            for article in engine['articles']:
                self.assertTrue(article['title'].strip())
                self.assertTrue(article['url'].startswith('http'))

    def test_is_json_serialisable_for_the_browser(self):
        json.dumps(self.payload)


if __name__ == '__main__':
    unittest.main()
