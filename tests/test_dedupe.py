"""BP-51: one story, shown once, from the most trusted source; no politicians
in People Movers. The headlines are the real duplicates of 28 Sep 2026."""
import unittest
from datetime import datetime, timezone

from app.analysis.command_center import (_is_political, build_engine_data, build_movers,
                                         build_people_rows, build_record)
from app.analysis.dedupe import keep_most_trusted, same_story


def _now():
    return datetime.now(timezone.utc).strftime('%a, %d %b %Y %H:%M:%S +0000')


def _article(title, url, image='https://example.com/p.jpg'):
    return {'title': title, 'summary': '', 'url': url, 'image': image, 'date': _now()}


MISTRY = [
    _article('Mehli Mistry steps down from Tata Medical Center trust',
             'https://www.thehindubusinessline.com/companies/mistry/article1.ece'),
    _article('Who is Mehli Mistry? Why is the Ratan Tata confidant stepping down from Tata Medical Centre Trust board?',
             'https://www.livemint.com/companies/people/who-is-mehli-mistry-1.html'),
    _article('Mehli Mistry steps down as Tata Medical Centre Trust trustee in sixth such exit: Who are the remaining trustees?',
             'https://www.livemint.com/companies/news/mehli-mistry-steps-down-2.html'),
]

VUCIC = [
    _article('Serbian President Aleksandar Vucic resigns amid prolonged protests',
             'https://www.aljazeera.com/news/2026/9/27/vucic-resigns'),
    _article('Embattled Serbian president resigns, paving way for early elections',
             'https://www.bbc.com/news/articles/serbia-president'),
    _article('Serbia’s populist president Aleksandar Vučić resigns to run for prime minister',
             'https://www.theguardian.com/world/2026/sep/27/vucic'),
]


def _move(title, url, kind='exit', **extra):
    return dict({'title': title, 'url': url, 'kind': kind}, **extra)


class TestSameStory(unittest.TestCase):
    def test_mistry_retellings_are_one_story(self):
        a, b, c = MISTRY
        self.assertTrue(same_story(a, b))
        self.assertTrue(same_story(a, c))

    def test_accents_do_not_split_a_person(self):
        a = _move(VUCIC[0]['title'], VUCIC[0]['url'])
        c = _move(VUCIC[2]['title'], VUCIC[2]['url'])
        self.assertTrue(same_story(a, c))

    def test_same_link_is_one_story(self):
        self.assertTrue(same_story({'title': 'A', 'url': 'https://x.com/1'}, {'title': 'B', 'url': 'https://x.com/1'}))

    def test_two_executives_resigning_the_same_day_are_two_stories(self):
        a = _move('Alkem Laboratories CFO Nitin Agrawal resigns', 'https://a.com/1')
        b = _move('Tata Steel CFO Koushik Chatterjee resigns', 'https://b.com/2')
        self.assertFalse(same_story(a, b))

    def test_two_companies_of_one_group_are_two_stories(self):
        a = _move('Tata Motors CEO steps down', 'https://a.com/1')
        b = _move('Tata Steel CEO steps down', 'https://b.com/2')
        self.assertFalse(same_story(a, b))

    def test_the_same_person_arriving_and_leaving_is_two_stories(self):
        a = _move('Jane Doe steps down as Acme CEO', 'https://a.com/1', kind='exit')
        b = _move('Beta names Jane Doe as CEO', 'https://b.com/2', kind='appointment')
        self.assertFalse(same_story(a, b))

    def test_different_stories_on_one_topic_stay(self):
        a = {'title': "'MDR to boost UPI use in cross border payments'", 'url': 'https://a.com/1'}
        b = {'title': 'Government may examine UPI MDR applicability on petrol pump payments: Sources', 'url': 'https://b.com/2'}
        self.assertFalse(same_story(a, b))

    def test_two_companies_opening_in_one_city_are_two_stories(self):
        a = {'title': 'Syneos Health opens GCC in Hyderabad', 'url': 'https://a.com/1'}
        b = {'title': 'Fuel Cycle opens GCC in Hyderabad to drive AI', 'url': 'https://b.com/2'}
        self.assertFalse(same_story(a, b))

    def test_a_joint_venture_told_twice_is_one_story(self):
        a = {'title': 'NTPC, EDF Power form joint venture to develop low-carbon energy projects | Details here', 'url': 'https://a.com/1'}
        b = {'title': 'NTPC, EDF form JV firm as India & France explore deepening engagement in clean energy', 'url': 'https://b.com/2'}
        self.assertTrue(same_story(a, b))


class TestKeepMostTrusted(unittest.TestCase):
    def test_keeps_the_more_trusted_publisher_in_place(self):
        items = [_move('Mehli Mistry steps down from trust', 'https://www.example-blog.com/1'),
                 _move('Mehli Mistry steps down from Tata trust', 'https://www.reuters.com/2')]
        self.assertEqual([i['url'] for i in keep_most_trusted(items)], ['https://www.reuters.com/2'])

    def test_a_pin_always_wins(self):
        items = [_move('Mehli Mistry steps down', 'https://www.reuters.com/1'),
                 _move('Mehli Mistry steps down from trust', 'https://www.example-blog.com/2', pinned=True)]
        self.assertTrue(keep_most_trusted(items)[0]['pinned'])

    def test_ties_go_to_the_copy_with_a_picture_then_the_newer(self):
        a = _move('Mehli Mistry steps down', 'https://www.livemint.com/1', image=None, ts=2)
        b = _move('Mehli Mistry steps down from trust', 'https://www.livemint.com/2', image='x.jpg', ts=1)
        c = _move('Mehli Mistry quits trust, steps down', 'https://www.livemint.com/3', image='y.jpg', ts=3)
        self.assertEqual(keep_most_trusted([a, b, c]), [c])

    def test_order_is_kept(self):
        items = [{'title': t, 'url': f'https://x.com/{t}'} for t in ('one alpha', 'two beta', 'three gamma')]
        self.assertEqual(keep_most_trusted(items), items)


class TestPeopleMovers(unittest.TestCase):
    def test_mistry_appears_once(self):
        rows = build_people_rows(MISTRY, {'quotes': []})
        mistry = [m for m in rows['_movers'] if 'Mistry' in m['title']]
        self.assertEqual(len(mistry), 1)

    def test_heads_of_state_are_not_movers(self):
        self.assertEqual(build_movers(VUCIC), [])

    def test_political_headlines(self):
        for title in ('Prime Minister resigns after losing confidence vote',
                      'Opposition leader steps down after poll defeat',
                      'Senator appointed ambassador to Japan',
                      'Embattled Serbian president resigns, paving way for early elections'):
            self.assertTrue(_is_political(title), title)

    def test_business_headlines_stay(self):
        for title in ('Helmsman names Emily Drew president, CEO',
                      'Acme names Jane Doe president of its Asia unit',
                      'Minister names new Air India CEO',
                      'WOI India acquires SwiftSeed Ventures; appoints founder Amar Dixit as CEO'):
            self.assertFalse(_is_political(title), title)

    def test_a_business_move_still_shows(self):
        moves = build_movers([_article('Alkem Laboratories CFO Nitin Agrawal resigns',
                                       'https://www.thehindubusinessline.com/a.ece')])
        self.assertEqual(len(moves), 1)


class TestTilesAndRecord(unittest.TestCase):
    def test_a_story_in_two_tiles_is_shown_once(self):
        ntpc = 'NTPC, EDF Power form joint venture to develop low-carbon energy projects | Details here'
        jv = 'NTPC, EDF form JV firm as India & France explore deepening engagement in clean energy'
        data = {'signals': [
            {'name': 'Signal Energy', 'articles': [{'title': jv, 'url': 'https://unknown-site.com/jv'}]},
            {'name': 'Signal Business', 'articles': [{'title': ntpc, 'url': 'https://www.reuters.com/ntpc'}]},
        ]}
        engines = build_engine_data(data)
        titles = [a['title'] for e in engines.values() for a in e.get('articles', [])]
        self.assertEqual(titles, [ntpc])

    def test_the_same_quote_from_two_outlets_is_shown_once(self):
        quote = {'leader': 'Satya Nadella', 'quote': 'Copilot is the new UI for AI.', 'company': 'Microsoft', 'date': '', 'ts': 1}
        record = build_record({'quotes': [dict(quote, source='Blog', url='https://unknown-site.com/q'),
                                          dict(quote, source='Reuters', url='https://www.reuters.com/q')]}, [])
        self.assertEqual([q['source'] for q in record['quotes']], ['Reuters'])

    def test_different_quotes_by_one_leader_stay(self):
        a = {'leader': 'Satya Nadella', 'quote': 'One thing.', 'url': 'https://www.cnbc.com/1'}
        b = {'leader': 'Satya Nadella', 'quote': 'Another thing entirely.', 'url': 'https://www.theverge.com/2'}
        self.assertEqual(len(build_record({'quotes': [a, b]}, [])['quotes']), 2)


if __name__ == '__main__':
    unittest.main()
