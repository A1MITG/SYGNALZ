"""People Movers and Executive Pulse on a one-story day.

With a single story, the lone card stretched across the row. The Mover's
small share image was blown up into a 160px-tall strip that showed only a
forehead, and the Pulse photo box took a ~500px width cap from its
max-height through its aspect ratio, leaving the rest of the card empty
(2026-10-25). A lone card now lies on its side (picture left, story right)
and every Mover and Pulse picture is shown whole on a blurred copy of itself.
"""
import unittest

from app.main import create_app


class TestSingleStoryPhotos(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.page = create_app().test_client().get('/').get_data(as_text=True)

    def test_pictures_are_never_cropped(self):
        self.assertIn('.move-photo img { position: relative; z-index: 1; width: 100%; height: 100%; object-fit: contain;', self.page)
        self.assertIn('.pulse-photo img { position: relative; z-index: 1; display: block; width: 100%; height: 100%; object-fit: contain; }', self.page)
        self.assertNotIn('object-position: center 25%', self.page)
        self.assertNotIn('object-position: center 30%', self.page)

    def test_blurred_backdrop_fills_the_panel(self):
        self.assertIn('<img class="fill" src="${esc(m.image)}" alt="" aria-hidden="true"', self.page)
        self.assertIn('<img class="fill" src="${esc(c.image)}" alt="" aria-hidden="true"', self.page)
        self.assertIn('.pulse-photo img.fill, .move-photo img.fill { position: absolute;', self.page)
        # A Mover whose picture fails falls back to the navy panel, with no
        # leftover backdrop.
        self.assertIn('.move-photo.none img.fill { display: none; }', self.page)

    def test_pulse_photo_spans_its_card(self):
        self.assertIn('.pulse-photo { width: 100%; aspect-ratio: 16 / 7;', self.page)

    def test_lone_card_lies_on_its_side_above_phone_width(self):
        self.assertIn('.move-card:only-child { flex-direction: row; }', self.page)
        self.assertIn('.move-card:only-child .move-photo { flex: 0 0 360px; aspect-ratio: 4 / 3;', self.page)
        self.assertIn('.pulse-card:only-child { display: flex; }', self.page)
        self.assertIn('.pulse-card:only-child .pulse-photo { flex: 0 0 360px; width: auto; aspect-ratio: 4 / 3;', self.page)
        self.assertEqual(self.page.count('@media (min-width: 641px) {\n    .move-card:only-child'), 1)
        self.assertEqual(self.page.count('@media (min-width: 641px) {\n    .pulse-card:only-child'), 1)


if __name__ == '__main__':
    unittest.main()
