"""The link preview LinkedIn, X, Slack and WhatsApp build for the SIGNAL URL.

Crawlers read the first HTML response and run no JavaScript, so the Open
Graph and Twitter tags live in the page's static <head>, the same file the
workflow publishes as the site's index.html. The card is
public/img/signal-og.png (2400 x 1254: LinkedIn's 1.91:1 at twice its
1200 x 627, so LinkedIn only ever scales it down and it stays sharp), published to /img/
with the rest of public/img.
"""
import re
import struct
import unittest
from html.parser import HTMLParser
from pathlib import Path

from app.main import create_app

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'app' / 'static' / 'command_center_source.html'
CARD = ROOT / 'public' / 'img' / 'signal-og.png'
PRODUCTION = 'https://news-letter-cxo.vercel.app/'
TITLE = 'SIGNAL — Know What Matters'
DESCRIPTION = 'Signals from everywhere. Multiple lenses. One intelligent brief.'
# ?v=N makes LinkedIn and X fetch a changed card instead of their cached copy.
IMAGE = PRODUCTION + 'img/signal-og.png?v=3'


class _Head(HTMLParser):
    """Meta tags and the canonical link, from the HTML before </head>."""

    def __init__(self):
        super().__init__()
        self.meta, self.canonical = {}, None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == 'meta' and (a.get('property') or a.get('name')):
            self.meta[a.get('property') or a.get('name')] = a.get('content')
        if tag == 'link' and a.get('rel') == 'canonical':
            self.canonical = a.get('href')


def _head(html):
    parser = _Head()
    parser.feed(html[:html.index('</head>')])
    return parser


class TestSocialPreview(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.published = (ROOT / 'public' / 'command_center.html').read_text(encoding='utf-8')
        cls.head = _head(cls.published)

    def test_open_graph_tags_in_the_static_head(self):
        m = self.head.meta
        self.assertEqual(m['og:title'], TITLE)
        self.assertEqual(m['og:description'], DESCRIPTION)
        self.assertEqual(m['og:type'], 'website')
        self.assertEqual(m['og:url'], PRODUCTION)
        self.assertEqual(m['og:image'], IMAGE)
        self.assertEqual((m['og:image:width'], m['og:image:height'], m['og:image:type']),
                         ('2400', '1254', 'image/png'))

    def test_twitter_tags(self):
        m = self.head.meta
        self.assertEqual(m['twitter:card'], 'summary_large_image')
        self.assertEqual(m['twitter:title'], TITLE)
        self.assertEqual(m['twitter:description'], DESCRIPTION)
        self.assertEqual(m['twitter:image'], IMAGE)

    def test_canonical_and_description(self):
        self.assertEqual(self.head.canonical, PRODUCTION)
        self.assertEqual(self.head.meta['description'], DESCRIPTION)

    def test_no_script_needed_and_no_local_urls(self):
        """The tags come before any script, and point only at production over HTTPS."""
        head = self.published[:self.published.index('</head>')]
        first_script = self.published.find('<script')
        self.assertLess(self.published.index('property="og:title"'), first_script)
        for value in list(self.head.meta.values()) + [self.head.canonical]:
            self.assertNotRegex(value or '', r'localhost|127\.0\.0\.1|http://')
        self.assertEqual(len(re.findall(r'property="og:', head)), 12)

    def test_author_and_publication_date(self):
        """LinkedIn's Post Inspector reads name="author" and og:publish_date."""
        m = self.head.meta
        self.assertEqual((m['author'], m['article:author']), ('Amit Gupta', 'Amit Gupta'))
        self.assertEqual(m['og:publish_date'], '2026-09-27T09:00:00+05:30')   # 9:00 IST
        self.assertEqual(m['article:published_time'], '2026-09-27T09:00:00+05:30')

    def test_the_card_is_a_1_91_png_at_twice_linkedins_size(self):
        data = CARD.read_bytes()
        self.assertEqual(data[:8], b'\x89PNG\r\n\x1a\n')
        width, height = struct.unpack('>II', data[16:24])
        self.assertEqual((width, height), (2400, 1254))
        self.assertAlmostEqual(width / height, 1.91, places=2)
        self.assertLess(len(data), 5 * 1024 * 1024)          # LinkedIn's limit is 5 MB

    def test_the_workflow_publishes_the_card(self):
        workflow = (ROOT / '.github' / 'workflows' / 'build-signals.yml').read_text(encoding='utf-8')
        self.assertIn('cp public/img/* /tmp/signals-deploy/img/', workflow)
        self.assertIn('cp public/command_center.html /tmp/signals-deploy/index.html', workflow)

    def test_the_flask_page_carries_the_same_tags(self):
        page = create_app().test_client().get('/').get_data(as_text=True)
        self.assertEqual(_head(page).meta['og:image'], IMAGE)
        self.assertEqual(self.published, SOURCE.read_text(encoding='utf-8'))


if __name__ == '__main__':
    unittest.main()
