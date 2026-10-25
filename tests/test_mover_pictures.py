"""People Movers cards lead with their story's own picture.

Each card shows the image the publisher chose for that story: the feed's
image, or the article page's share image when the feed has none (the same
route the tiles use). With no usable picture the card shows a navy panel
with the SIGNAL mark, never someone else's photo. The row's wording also
changed to "Recent appointments and exits" (2026-09-25): there is not a
move every day, and the row covers the week, not today.
"""
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

from app.analysis.command_center import build_movers
from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]


def _article(title, url='https://example.com/move', image=''):
    return {'title': title, 'summary': '', 'url': url, 'image': image,
            'date': datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')}


class TestMoveImages(unittest.TestCase):

    def test_a_move_carries_its_storys_picture(self):
        moves = build_movers([_article('Acme appoints Jane Rivera as chief executive officer',
                                       image='https://cdn.example.com/rivera.jpg')])
        self.assertEqual(moves[0]['image'], 'https://cdn.example.com/rivera.jpg')

    def test_no_usable_picture_is_none(self):
        for image in ('', '/static/img/thumb.jpg', 'data:image/gif;base64,R0lGOD'):
            moves = build_movers([_article('Acme appoints Jane Rivera as chief executive officer', image=image)])
            self.assertIsNone(moves[0]['image'], image)

    def test_the_live_endpoint_fills_missing_pictures(self):
        story = _article('Acme appoints Jane Rivera as chief executive officer')
        with mock.patch('app.api.routes.get_articles', return_value=[story]), \
             mock.patch('app.api.routes.get_leader_quotes', return_value={'quotes': [], 'leaders': []}), \
             mock.patch('app.api.routes.fill_missing_images') as fill:
            payload = create_app().test_client().get('/api/command-center').get_json()
        fill.assert_called_once()
        self.assertEqual([m['url'] for m in fill.call_args.args[0]], [m['url'] for m in payload['_movers']])

    def test_the_static_build_fills_missing_pictures(self):
        build = (ROOT / 'scripts' / 'build_command_center_data.py').read_text(encoding='utf-8')
        self.assertIn("fill_missing_images(engine_data['_movers'])", build)


class TestMoverCards(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.page = create_app().test_client().get('/').get_data(as_text=True)

    def test_a_card_leads_with_the_picture(self):
        self.assertIn('<div class="move-photo"><img src="${esc(m.image)}" alt="" loading="lazy"', self.page)
        self.assertIn('<div class="move-body">', self.page)

    def test_without_a_picture_the_card_shows_the_signal_mark(self):
        self.assertIn('<div class="move-photo none"><span class="mark">', self.page)
        # a picture that fails to load falls back to the same panel
        self.assertIn("onerror=\"this.parentNode.className='move-photo none';this.remove()\"", self.page)

    def test_the_row_says_recent_not_today(self):
        self.assertIn('<p class="row-lede">Recent appointments and exits.</p>', self.page)
        self.assertIn('No recent appointments or exits.', self.page)
        renderer = self.page[self.page.index('function renderMovers'):self.page.index('function renderRecord')]
        self.assertNotIn("today's scan", renderer)


if __name__ == '__main__':
    unittest.main()
