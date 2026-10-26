"""Global feeds, native-language screening, the tighter Insurance rubric and
the per-country cap (2026-10-26)."""
from urllib.parse import unquote

import pytest

from app.analysis import geo_cap
from app.analysis.command_center import build_engine_data
from app.analysis.screen import screened_out
from app.analysis.signals import classify_article, score_signals, synthesize_signals
from app.intelligence import geo
from app.intelligence.language import native_verdict, prepare_languages
from app.scraper import registry


class TestRegistry:
    def test_every_feed_has_an_id_a_language_and_a_source(self):
        ids = set()
        for feed in registry.feeds():
            assert feed['id'] not in ids
            ids.add(feed['id'])
            assert feed.get('language')
            assert feed.get('url') or feed.get('search')
            assert feed.get('country') or feed.get('region'), feed['id']

    def test_amits_sources_and_the_global_feeds_are_in(self):
        ids = {f['id'] for f in registry.feeds()}
        for wanted in ('revistaapolice_seguros', 'segs_seguros', 'assinews', 'meinsurancereview',
                       'prtimes', 'vneconomy', 'insnews', 'chinadaily_cn', 'nikkei_asia',
                       'scmp_business', 'allafrica_business', 'bnamericas'):
            assert wanted in ids

    def test_portals_are_left_out(self):
        urls = ' '.join((f.get('url') or '') + (f.get('search') or '') for f in registry.feeds())
        assert 'nate.com' not in urls and 'sohu.com' not in urls

    def test_search_uses_the_languages_edition_and_the_last_day(self):
        url = registry.search_url('site:news1.kr 보험', language='ko')
        assert 'gl=KR' in url and 'hl=ko' in url
        assert unquote(url).endswith('site:news1.kr 보험 when:1d')

    def test_stories_carry_their_feeds_tags(self):
        feed = {'id': 'x', 'name': 'X', 'country': 'BR', 'language': 'pt',
                'sectors': ['insurance'], 'trade': True}
        story = registry.tag({'title': 't', 'url': 'u'}, feed)
        assert story['source_country'] == 'BR' and story['language'] == 'pt'
        assert story['trade'] is True and story['press_release'] is False


class TestCountry:
    @pytest.mark.parametrize('title, country', [
        ('SUSEP aprova novas regras para seguro de vida', 'BR'),
        ('Tokio Marine to buy US specialty insurer for $2bn', 'JP'),
        ('South Africa insurers face flood claims', 'ZA'),
        ('US stocks rally as Fed holds', 'US'),
        ('Bajaj Allianz names new CEO', 'IN'),
    ])
    def test_the_headline_names_the_country(self, title, country):
        assert geo.subject_country(title) == country

    def test_lower_case_us_is_not_the_country(self):
        assert geo.subject_country('Tell us what you think') is None

    def test_no_place_falls_back_to_the_publisher(self):
        story = {'title': 'RLI reports $58.8m underwriting income',
                 'url': 'https://www.insurancejournal.com/news/1'}
        assert geo.story_country(story) == 'US'
        assert geo.story_region(story) == 'americas_n'

    def test_indian_hosts(self):
        assert geo.host_country('https://hr.economictimes.indiatimes.com/x') == 'IN'


class TestNativeLanguage:
    def _story(self, title, language='pt', trade=False, sectors=('insurance',)):
        return {'title': title, 'url': f'https://x/{hash(title)}', 'language': language,
                'trade': trade, 'sectors': list(sectors)}

    def test_insurance_word_in_the_headline_passes(self):
        assert native_verdict(self._story('Seguradora lucra R$ 2 bilhões'))[0]
        assert native_verdict(self._story('損保大手3社が増益', 'ja'))[0]
        assert native_verdict(self._story('손해보험사 3분기 실적', 'ko'))[0]

    def test_exclude_words_drop(self):
        assert native_verdict(self._story('Como escolher um seguro auto'))[1] == 'native_exclude'

    def test_general_outlet_needs_the_word_in_the_headline(self):
        story = self._story('Governo anuncia pacote', trade=False)
        story['summary'] = 'Inclui mudanças no seguro-desemprego'
        assert not native_verdict(story)[0]
        story['trade'] = True
        assert native_verdict(story)[0]

    def test_translated_stories_keep_their_original_headline(self):
        story = self._story('Seguradora lucra R$ 2 bilhões')
        out = prepare_languages(
            [{'title': 'English story', 'url': 'e'}, story],
            translate=lambda xs: {xs[0]['url']: ('Insurer earns R$2 billion', '')})
        assert [s['title'] for s in out] == ['English story', 'Insurer earns R$2 billion']
        assert out[1]['title_original'] == 'Seguradora lucra R$ 2 bilhões'

    def test_untranslated_stories_are_left_out(self):
        out = prepare_languages([self._story('Seguradora lucra R$ 2 bilhões')],
                                translate=lambda xs: {})
        assert out == []


class TestScreen:
    @pytest.mark.parametrize('title', [
        'Best car insurance quotes for October',
        '5 things to know before buying term insurance',
        'Stocks to buy today: LIC, Star Health',
        'US stock market today: Wall Street rallies',
        'How to choose a health insurance plan',
    ])
    def test_dropped(self, title):
        assert screened_out({'title': title, 'url': 'https://x/a'})

    @pytest.mark.parametrize('title', [
        'Swiss Re cuts 2027 property cat capacity in Asia',
        'Tokio Marine to buy US specialty insurer for $2bn',
        'Infosys promoted to leader in Gartner quadrant',
        'How India became the GCC capital of the world',
    ])
    def test_kept(self, title):
        assert screened_out({'title': title, 'url': 'https://x/a'}) is None

    def test_sponsored_paths(self):
        assert screened_out({'title': 'Insurer news', 'url': 'https://x/sponsored/a'}) == 'blocked_path'


class TestInsuranceRubric:
    def test_weak_words_alone_score_nothing(self):
        assert score_signals('Premium smartphones see record coverage of launch')['Signal Insurance'] == 0
        assert score_signals('Government policy on rice exports carries risk')['Signal Insurance'] == 0

    def test_an_insurance_word_lets_them_count(self):
        assert score_signals('Insurers raise premiums after flood claims')['Signal Insurance'] > 6

    def test_general_outlets_need_more(self):
        # insurance (2) + claims (1) in the summary: 3, a trade outlet's floor.
        title, summary = 'Flood losses mount across Valencia', 'Insurance claims are rising'
        assert classify_article(title, summary, trade=True)[0] == 'Signal Insurance'
        result = classify_article(title, summary, trade=False)
        assert result is None or result[0] != 'Signal Insurance'


def _story(title, country, score=10, region=None, **kw):
    return {'title': title, 'url': f'https://x/{title}', 'score': score, 'country': country,
            'region': region or geo.region_of(country), **kw}


class TestCountryCap:
    def test_one_country_holds_at_most_half_when_others_qualify(self):
        ranked = [_story(f'in{i}', 'IN', 20 - i) for i in range(10)] + \
                 [_story(f'us{i}', 'US', 5 - i) for i in range(3)] + [_story('br', 'BR', 1)]
        kept = geo_cap.select_capped('Signal Banking', ranked, 8)
        assert len(kept) == 8
        assert sum(s['country'] == 'IN' for s in kept) == 4
        assert any(s['country'] == 'BR' for s in kept)

    def test_slots_refill_when_no_one_else_has_news(self):
        ranked = [_story(f'in{i}', 'IN', 20 - i) for i in range(10)]
        assert len(geo_cap.select_capped('Signal Banking', ranked, 8)) == 8

    def test_gcc_is_exempt(self):
        ranked = [_story(f'in{i}', 'IN', 20 - i) for i in range(6)] + [_story('us', 'US', 1)]
        kept = geo_cap.select_capped('Signal GCC', ranked, 5)
        assert all(s['country'] == 'IN' for s in kept)

    def test_order_stays_best_first(self):
        ranked = [_story(f'in{i}', 'IN', 20 - i) for i in range(6)] + [_story('us', 'US', 1)]
        kept = geo_cap.select_capped('Signal Banking', ranked, 4)
        assert [s['score'] for s in kept] == sorted((s['score'] for s in kept), reverse=True)


class TestInsuranceSelection:
    def test_two_per_country_three_regions_one_press_release(self):
        ranked = ([_story(f'Insurer us{i} earnings', 'US', 30 - i) for i in range(5)]
                  + [_story(f'Insurer gb{i} news', 'GB', 20 - i) for i in range(3)]
                  + [_story('Seguradora br', 'BR', 5),
                     _story('Insurer jp release', 'JP', 4, press_release=True),
                     _story('Insurer jp release 2', 'JP', 3, press_release=True)])
        # Six slots: two US, two UK, Brazil and one Japanese release fill them
        # without refilling past the per-country limit.
        kept = geo_cap.select_insurance(ranked, 6)
        countries = [s['country'] for s in kept]
        assert max(countries.count(c) for c in set(countries)) <= 2
        assert len({s['region'] for s in kept}) >= 3
        assert sum(1 for s in kept if s.get('press_release')) <= 1

    def test_events_lift_a_story(self):
        deal = _story('Tokio Marine to acquire US specialty insurer', 'JP', 10)
        talk = _story('Insurers weigh outlook for next year', 'GB', 11)
        assert geo_cap.interest(deal) > geo_cap.interest(talk)


class TestEndToEnd:
    def test_tiles_carry_country_and_translation(self):
        articles = [
            {'title': 'Seguradora Porto Seguro compra corretora', 'url': 'https://a/1',
             'summary': '', 'language': 'pt', 'trade': True, 'sectors': ['insurance'],
             'source_country': 'BR', 'region': 'latam'},
        ]
        articles = prepare_languages(
            articles, translate=lambda xs: {'https://a/1': ('Porto Seguro insurer buys a broker', '')})
        data = synthesize_signals(articles, require_current=False, include_tile_signals=True)
        insurance = next(s for s in data['signals'] if s['name'] == 'Signal Insurance')
        assert insurance['articles'][0]['country'] == 'BR'
        engine = build_engine_data(data)
        story = engine['insurance']['articles'][0]
        assert story['country'] == 'BR' and story['language'] == 'pt'
        assert story['title_original'] == 'Seguradora Porto Seguro compra corretora'

    def test_screened_stories_are_counted(self):
        data = synthesize_signals([{'title': 'Best car insurance quotes for October 2026',
                                    'url': 'https://a/2'}], require_current=False)
        assert data['screened_out'] == {'consumer_advice': 1}
