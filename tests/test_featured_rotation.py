"""Featured Analysis rotates through the day's GCC stories, then others.

2026-09-25: the section showed one card, the pinned Dell-Zinnov report, while
the new GCC feeds brought in a day's worth of GCC stories. It now cycles
through the pin and those stories like the tiles' headlines, each card naming
its publisher and date, and only stories with a full-size picture qualify
(ET's B2B feeds send 100x100 thumbnails). 2026-10-25: pins were removed;
the rotation is only the day's identified stories.
"""
import unittest
from datetime import datetime, timezone
from unittest import mock

from app.analysis.command_center import FEATURED_LIMIT, build_features
from app.main import create_app

THUMB = 'https://etimg.etb2bimg.com/thumb/img-size-939740/134435247.cms'


def _now():
    return datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')


def _engines(**tiles):
    return {eid: {'name': eid.upper(), 'articles': [{'title': t, 'url': f'https://{eid}.example/{i}'}
                                                    for i, t in enumerate(titles)]}
            for eid, titles in tiles.items()}


def _raw(*titles, image='https://example.com/p.jpg'):
    return {t: {'title': t, 'image': image, 'summary': f'About {t}.', 'date': _now()} for t in titles}


class TestTheRotation(unittest.TestCase):

    def test_gcc_stories_lead_in_tile_order(self):
        data = _engines(gcc=['g0', 'g1', 'g2'], insurance=['i0'], global_=['x0'])
        features = build_features(data, _raw('g0', 'g1', 'g2', 'i0'))
        self.assertEqual([f['title'] for f in features], ['g0', 'g1', 'g2', 'i0'])
        self.assertNotIn('pinned', features[0])

    def test_at_most_six_cards(self):
        data = _engines(gcc=[f'g{i}' for i in range(9)])
        self.assertEqual(len(build_features(data, _raw(*[f'g{i}' for i in range(9)]))), FEATURED_LIMIT)
        self.assertEqual(FEATURED_LIMIT, 6)

    def test_a_thin_gcc_day_is_filled_from_insurance_then_global(self):
        data = _engines(gcc=['g0'], insurance=['i0'], economy=['e0'])
        data['global'] = _engines(x=['x0'])['x']
        titles = [f['title'] for f in build_features(data, _raw('g0', 'i0', 'e0', 'x0'))]
        self.assertEqual(titles, ['g0', 'i0', 'x0'])

    def test_only_stories_with_a_full_size_picture(self):
        data = _engines(gcc=['thumb', 'none', 'full'])
        raw = {**_raw('thumb', image=THUMB), **_raw('none', image=''), **_raw('full')}
        self.assertEqual([f['title'] for f in build_features(data, raw)], ['full'])

    def test_every_card_names_its_publisher_and_date(self):
        data = {'gcc': {'name': 'GCC', 'articles': [
            {'title': 'Syneos Health opens GCC in Hyderabad',
             'url': 'https://gcc.economictimes.indiatimes.com/news/syneos/134435247'}]}}
        raw = {'Syneos Health opens GCC in Hyderabad': {
            'image': 'https://example.com/p.jpg', 'summary': 'x',
            'date': 'Wed, 23 Sep 2026 17:07:06 +0530'}}
        card = build_features(data, raw)[0]
        self.assertEqual((card['source'], card['date'], card['label']), ('The Economic Times', '23 Sep', 'GCC'))

    def test_a_story_in_two_tiles_is_one_card(self):
        data = _engines(gcc=['g0'], insurance=['i0'])
        data['insurance']['articles'][0]['url'] = data['gcc']['articles'][0]['url']
        self.assertEqual([f['title'] for f in build_features(data, _raw('g0', 'i0'))], ['g0'])

    def test_the_live_endpoint_sends_the_rotation_and_its_first_card(self):
        data = _engines(gcc=['g0', 'g1'])
        with mock.patch('app.api.routes.get_articles', return_value=[]), \
             mock.patch('app.api.routes.get_leader_quotes', return_value={'quotes': [], 'leaders': []}), \
             mock.patch('app.api.routes.build_engine_data', return_value=data), \
             mock.patch('app.api.routes.build_features', wraps=lambda d, b: build_features(d, _raw('g0', 'g1'))):
            payload = create_app().test_client().get('/api/command-center').get_json()
        self.assertEqual([f['title'] for f in payload['_features']], ['g0', 'g1'])
        self.assertEqual(payload['_featured']['title'], 'g0')


class TestTheDeck(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.page = create_app().test_client().get('/').get_data(as_text=True)

    def test_the_page_reads_the_rotation(self):
        self.assertIn('const { _meta, _featured, _features, _movers, _pulse, _record, ...engines } = data;',
                      self.page)
        self.assertIn('renderFeatured(_features || (_featured ? [_featured] : []));', self.page)

    def test_cards_stack_in_one_cell_so_nothing_below_moves(self):
        self.assertIn('.featured-stack > .featured { grid-area: 1 / 1;', self.page)

    def test_it_moves_on_every_seven_seconds_and_holds_for_the_reader(self):
        self.assertIn('show(at + 1); }, 7000);', self.page)
        for event in ("'mouseenter', () => { held = true; }", "'focusin', () => { held = true; }",
                      "addEventListener('touchstart'"):
            self.assertIn(event, self.page)
        self.assertIn("matchMedia('(prefers-reduced-motion: reduce)')", self.page)

    def test_arrows_and_dots_move_it_by_hand(self):
        self.assertIn('aria-label="${dir === \'prev\' ? \'Previous\' : \'Next\'} featured story"', self.page)
        self.assertIn('aria-label="Featured story ${i + 1} of ${list.length}"', self.page)

    def test_every_card_says_who_published_it_and_when(self):
        self.assertIn("<div class=\"featured-pin\">${f.pinned ? 'Pinned &middot; ' : ''}${esc(f.source || '')}"
                      "${f.date ? ` &middot; ${esc(f.date)}` : ''}</div>", self.page)


if __name__ == '__main__':
    unittest.main()
