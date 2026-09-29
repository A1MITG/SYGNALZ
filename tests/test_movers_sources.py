"""BP-57: more leadership moves for People Movers, from sources we may read.

LinkedIn pages are not read (its terms forbid scraping). HRKatha's feed and
Analytics India Magazine's news sitemap carry the same moves, and the move
detector learned how the Indian press words them. Real headlines of
29 Sep 2026. No network: fetches are stubbed.
"""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

from app.analysis.command_center import build_movers
from app.intelligence.normalize import publisher_for
from app.intelligence.trust import resolve
from app.scraper import news_sitemaps
from app.scraper.news_sitemaps import is_a_move
from app.scraper.sources import NEWS_SITEMAPS, TIER_2_SOURCES

HRKATHA = 'https://www.hrkatha.com/feed/'
AIM = 'https://analyticsindiamag.com/news-sitemap.xml'


class TestTheDetectorKnowsIndianPhrasing(unittest.TestCase):

    MOVES = (
        'Radhika Arora joins DCM Shriram Chemicals as CHRO',
        'Koninika Mitra joins SBS India as head-HR',
        'Deepa Subbaiah joins Lumilens as global head-HR',
        'Siddharth Sawant joins Tata Consumer Products to lead HR for Ready-to-Drink business',
        'MongoDB CEO Joins Meta to Lead Enterprise AI Platform',
        'IndiGo brings in Deloitte’s Sudeep Anurag to lead HR analytics',
        'MiPhi brings in Intel’s Nupur Shrivastava as CHRO',
        'Guy Goldstein joins Munich Re’s primary subsidiary, ERGO as Chief AI Officer',
        'Air India’s new CEO takes charge; focuses on safety and empowering work culture',
        'From Mattel to BASF: Pooja Venkatram takes charge of HR in India',
        'Chetan Garg Takes Charge As RateGain Travel Technologies CFO',
        'Acme CFO quits after two years',
        'Jane Doe quits as chief executive of Acme',
    )

    NOT_MOVES = (
        'Acme quits Russian market',
        'Infosys joins AI alliance as founding member',
        'Manufacturing, transport firms overtake banks to lead India’s GCC boom',
        'Acme hires 500 engineers for its Pune GCC',
        'NVIDIA Approves Record $150 Bn Share Buyback as AI Boom Fuels Cash Generation',
        'Paras Health is trying to build one HR system for a workforce that has nothing in common',
        # A market roundup that mentions a resignation among other stocks.
        'Price Action: Allcargo Logistics sheds 2% on MD, CEO resignation; Ola Electric gains',
    )

    def test_real_moves_are_found(self):
        for title in self.MOVES:
            self.assertTrue(is_a_move(title), title)

    def test_lookalikes_are_not_moves(self):
        for title in self.NOT_MOVES:
            self.assertFalse(is_a_move(title), title)

    def test_a_party_leadership_race_is_politics(self):
        """The Guardian, 29 Sep 2026: an exit, but not a business leader's."""
        from app.analysis.command_center import _is_political
        self.assertTrue(_is_political('Sarah Hanson-Young and Mehreen Faruqi announce Greens '
                                      'leadership tilt after Larissa Waters steps down'))
        self.assertTrue(_is_political("CJP's movement should now be for PM Modi's resignation: Arvind Kejriwal"))
        self.assertFalse(_is_political('LIC Mutual Fund appoints Ashis Kumar as Managing Director and CEO'))


def _sitemap(*titles):
    now = datetime.now(timezone(timedelta(hours=5, minutes=30))).isoformat(timespec='seconds')
    urls = ''.join(
        f'<url><loc>https://analyticsindiamag.com/story-{i}/</loc><news:news><news:publication>'
        f'<news:name>AIM</news:name><news:language>en</news:language></news:publication>'
        f'<news:publication_date>{now}</news:publication_date><news:title>{t}</news:title>'
        f'</news:news></url>' for i, t in enumerate(titles))
    return (f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
            f'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">{urls}</urlset>').encode('utf-8')


async def _no_page(session, story):
    story['summary'] = ''


def _scrape(xml):
    async def fetch(session, url):
        return xml
    with mock.patch.object(news_sitemaps, '_describe', _no_page):
        return asyncio.run(news_sitemaps.scrape_news_sitemap(None, AIM, fetch))


class TestSitemapsKeepMoves(unittest.TestCase):

    def test_gcc_stories_and_moves_are_kept_and_the_rest_left(self):
        stories = _scrape(_sitemap('Syneos Health opens GCC in Hyderabad',
                                   'MiPhi brings in Intel’s Nupur Shrivastava as CHRO',
                                   'NVIDIA Approves Record $150 Bn Share Buyback'))
        self.assertEqual([s['title'][:12] for s in stories], ['Syneos Healt', 'MiPhi brings'])

    def test_moves_are_capped(self):
        stories = _scrape(_sitemap(*[f'Person {i} joins Acme{i} as CFO' for i in range(20)]))
        self.assertEqual(len(stories), news_sitemaps.MAX_MOVES)

    def test_a_sitemap_move_reaches_people_movers(self):
        stories = _scrape(_sitemap('MongoDB CEO Joins Meta to Lead Enterprise AI Platform'))
        moves = build_movers(stories)
        self.assertEqual([(m['source'], m['kind']) for m in moves], [('Analytics India Magazine', 'appointment')])


class TestTheNewSources(unittest.TestCase):

    def test_hrkatha_is_read(self):
        self.assertIn(HRKATHA, [url for feeds in TIER_2_SOURCES.values() for url in feeds])

    def test_aim_is_read_from_its_sitemap(self):
        self.assertIn(AIM, NEWS_SITEMAPS)

    def test_both_are_trusted_trade_press_and_named(self):
        for host, name in (('www.hrkatha.com', 'HRKatha'), ('analyticsindiamag.com', 'Analytics India Magazine')):
            self.assertEqual(resolve(host)['tier'], 'tier_2', host)
            self.assertEqual(publisher_for(host), name)


if __name__ == '__main__':
    unittest.main()
