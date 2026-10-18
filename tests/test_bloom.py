"""Bloom: the good-news page (/bloom).

Headlines are real ones from the 2026-10-18 feeds of Good News Network, The
Better India and The CSR Journal; the last runs general news beside its CSR
stories, which is why its stories need a good-news cue.
"""
import unittest
from datetime import date, datetime, timezone
from pathlib import Path

from app.analysis.bloom import (LANE_IDS, WINDOW_DAYS, build_bloom, is_good_news, is_india,
                                load_pinned, pick_lane)
from app.scraper.good_news import CSR_FEEDS, DEDICATED_FEEDS
from app.scraper.sources import TIER_1_SOURCES, TIER_2_SOURCES

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'build-signals.yml'
PAGE = ROOT / 'app' / 'static' / 'bloom.html'
NOW = datetime(2026, 10, 18, 14, 30, tzinfo=timezone.utc)


def story(title, dedicated=True, date='Sat, 17 Oct 2026 19:56:32 +0000', url=None, **kw):
    slug = title.lower().replace(' ', '-')[:40]
    return {'title': title, 'url': url or f'https://www.goodnewsnetwork.org/{slug}/',
            'date': date, 'summary': kw.pop('summary', ''), 'image': kw.pop('image', ''),
            'dedicated': dedicated, **kw}


class TestGoodNews(unittest.TestCase):
    def test_dedicated_outlet_story_needs_no_cue(self):
        self.assertTrue(is_good_news(story("Massive 70lb Dog the Size of Baby Hippo Sheds Pounds and Gets New Home")))

    def test_general_news_needs_a_cue(self):
        self.assertFalse(is_good_news(story("New 5G Smartphone Launched Under ₹16,000 with 6000mAh Battery",
                                            dedicated=False)))
        self.assertTrue(is_good_news(story("Nikhilan JM from LPU Achieves Global Recognition at WorldSkills Shanghai 2026",
                                           dedicated=False)))

    def test_never_words_block_even_from_good_outlets(self):
        for title in ("Fatal Attack in St Petersburg Targets Oil Facilities, Injures Five",
                      "West Bengal CM Orders Investigation into Kolkata Stampede",
                      "Inches Below the Forest Floor, Archeologists in Germany Unearthed a Roman Temple to the War God"):
            self.assertFalse(is_good_news(story(title)), title)

    def test_never_words_in_the_summary_block_too(self):
        self.assertFalse(is_good_news(story("Survivor rescued from rubble days later",
                                            summary="The building collapse killed 12 people.")))

    def test_outlet_features_that_are_not_stories(self):
        for title in ("Good News in History, October 18",
                      "Your Positive Weekly Horoscope: ‘Free Will Astrology’ by Rob Brezsny",
                      "What went right this week: the good news that matters"):
            self.assertFalse(is_good_news(story(title)), title)


class TestLanes(unittest.TestCase):
    def test_lanes(self):
        cases = {
            "Teen Triathlete Raises More Than $30,000 to Donate Bikes and Riding Lessons to Low-Income Families": 'heroes',
            "Coral Bleaching Reversed at Record Speed Using New Stanford Method": 'planet',
            "Giant Tortoise Population is Thriving–Survey Finds 180,000 on One Island Chain": 'planet',
            "Drape Some ‘Plant Armor’ Fabric Over a Strawberry Patch to Triple Yields with Less Pesticides": 'breakthroughs',
            "Tata Steel Foundation CSR programme gives scholarships to 5,000 tribal students": 'csr',
            "Pittsburgh Water Launches Debt Forgiveness Program to Help Families with Rising Costs": 'community',
        }
        for title, lane in cases.items():
            self.assertEqual(pick_lane(story(title)), lane, title)

    def test_india(self):
        self.assertTrue(is_india(story("Kerala Is Placing Trained Green Marshals at Tourist Spots",
                                       url='https://thebetterindia.com/travel/x')))
        self.assertTrue(is_india(story("Villagers in Odisha restore their pond", dedicated=False)))
        self.assertFalse(is_india(story("Giant Tortoise Population is Thriving on One Island Chain")))


class TestBuild(unittest.TestCase):
    def test_window_newest_first_and_dedupe(self):
        articles = [
            story("Giant Tortoise Population is Thriving on One Island Chain",
                  date='Sun, 11 Oct 2026 16:11:41 +0000'),
            story("Prince William launches kit to help communities prepare",
                  date='Thu, 08 Oct 2026 11:27:24 +0000'),          # older than a week
            story("Undated story about a kind neighbour", date=None),
            story("Massive 70lb Dog the Size of Baby Hippo Sheds Pounds and Gets New Home"),
            story("Massive 70lb Dog the Size of Baby Hippo Sheds Pounds and Gets New Home",
                  url='https://example.com/copy'),
        ]
        data = build_bloom(articles, now=NOW)
        shown = ([data['pick']] if data['pick'] else []) + data['stories']
        self.assertEqual([s['title'][:12] for s in shown], ['Massive 70lb', 'Giant Tortoi'])
        self.assertEqual(data['_meta']['window_days'], WINDOW_DAYS)
        self.assertEqual(data['_meta']['count'], 2)
        self.assertEqual([l['id'] for l in data['lanes']], list(LANE_IDS))

    def test_pick_is_newest_hero_with_a_picture(self):
        articles = [
            story("Coral Bleaching Reversed at Record Speed Using New Stanford Method",
                  image='https://example.com/coral.jpg', date='Sat, 17 Oct 2026 20:00:00 +0000'),
            story("Teen Triathlete Raises More Than $30,000 to Donate Bikes to Families",
                  image='https://example.com/bike.jpg', date='Wed, 14 Oct 2026 18:30:16 +0000'),
        ]
        data = build_bloom(articles, now=NOW)
        self.assertTrue(data['pick']['title'].startswith('Teen Triathlete'))
        self.assertNotIn(data['pick'], data['stories'])

    def test_pinned_story_leads(self, tmp=None):
        import tempfile
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'pins.yaml'
            path.write_text("pins:\n  - title: Old pin\n    url: https://a.example/1\n    until: 2026-10-01\n"
                            "  - title: Live pin\n    url: https://a.example/2\n    lane: csr\n    until: 2026-10-20\n")
            pin = load_pinned(path, today=date(2026, 10, 18))
        self.assertEqual(pin['title'], 'Live pin')
        data = build_bloom([], now=NOW, pinned=pin)
        self.assertTrue(data['pick']['pinned'])
        self.assertEqual(data['pick']['lane'], 'csr')
        self.assertEqual(data['_meta']['count'], 1)


class TestWiring(unittest.TestCase):
    def test_bloom_sources_stay_off_the_rest_of_the_site(self):
        site = {u for group in (TIER_1_SOURCES, TIER_2_SOURCES) for urls in group.values() for u in urls}
        self.assertFalse(site & set(DEDICATED_FEEDS + CSR_FEEDS))

    def test_workflow_builds_and_publishes_bloom_without_blocking_the_site(self):
        text = WORKFLOW.read_text(encoding='utf-8')
        step = text.split('name: Build Bloom good-news page', 1)[1].split('- name:', 1)[0]
        self.assertIn('continue-on-error: true', step)
        self.assertIn('python scripts/build_bloom.py', step)
        self.assertIn('cp public/bloom.html public/bloom.json /tmp/signals-deploy/', text)
        self.assertIn('bloom.html bloom.json vercel.json', text)

    def test_page_escapes_feed_text(self):
        html = PAGE.read_text(encoding='utf-8')
        self.assertIn("./bloom.json", html)
        self.assertIn('esc(s.title)', html)
        self.assertIn('safeUrl(s.url)', html)
        self.assertNotIn('innerHTML = s.', html)


if __name__ == '__main__':
    unittest.main()
