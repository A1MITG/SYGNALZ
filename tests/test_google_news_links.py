"""Google News links resolved to the publisher's own (2026-10-26).

People Movers and GCC cards from news searches showed no picture, because the
link was news.google.com's, whose page has no story photo. No network: the
resolver and the page fetch are stubbed.
"""
import base64
import json
import unittest

from app.scraper import article_images, news_search

SEARCH = 'https://news.google.com/rss/articles/CBMiNEW?oc=5'
REAL = 'https://www.bwpeople.in/article/joshi-alaparthi-joins-assetmark-123'


class ResolveTest(unittest.TestCase):
    def setUp(self):
        news_search._resolved.clear()
        article_images._seen.clear()

    def test_card_gets_publisher_link_and_its_photo(self):
        move = {'title': 'Joshi Alaparthi Joins AssetMark', 'url': SEARCH, 'image': None}
        filled = article_images.fill_missing_images(
            [move],
            fetch=lambda urls: [(u, 'https://img.bwpeople.in/joshi.jpg') for u in urls],
            resolve=lambda urls: {u: REAL for u in urls})
        self.assertEqual(filled, 1)
        self.assertEqual(move['url'], REAL)
        self.assertEqual(move['image'], 'https://img.bwpeople.in/joshi.jpg')

    def test_unresolved_link_is_kept_and_not_fetched(self):
        move = {'title': 'x', 'url': SEARCH, 'image': None}
        fetched = []
        article_images.fill_missing_images(
            [move], fetch=lambda urls: fetched.extend(urls) or [], resolve=lambda urls: {})
        self.assertEqual(move['url'], SEARCH)
        self.assertEqual(fetched, [])

    def test_resolve_links_drops_failures_and_caches(self):
        calls = []

        def resolver(urls):
            calls.append(list(urls))
            return [(u, REAL if 'NEW' in u else None) for u in urls]

        other = 'https://news.google.com/rss/articles/CBMiBAD'
        self.assertEqual(news_search.resolve_links([SEARCH, other, REAL], resolver), {SEARCH: REAL})
        news_search.resolve_links([SEARCH, other], resolver)
        self.assertEqual(len(calls), 1)

    def test_legacy_id_decodes_offline(self):
        aid = base64.urlsafe_b64encode(b'\x08\x13"\x2a' + REAL.encode() + b'\xd2\x01\x00').decode().rstrip('=')
        self.assertEqual(news_search.legacy_url(aid), REAL)
        new = base64.urlsafe_b64encode(b'\x08\x13"\x2aAU_yqLabc').decode().rstrip('=')
        self.assertIsNone(news_search.legacy_url(new))

    def test_batch_reply_parsed(self):
        inner = json.dumps(['garturlres', REAL, 1])
        reply = ")]}'\n\n" + json.dumps([['wrb.fr', 'Fbv4je', inner, None, None, None, 'generic']])
        self.assertEqual(news_search.parse_batch_reply(reply), REAL)
        self.assertIsNone(news_search.parse_batch_reply(")]}'\n\n[]"))

    def test_article_id(self):
        self.assertEqual(news_search.article_id(SEARCH), 'CBMiNEW')
        self.assertIsNone(news_search.article_id(REAL))


if __name__ == '__main__':
    unittest.main()
