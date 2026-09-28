"""BP-55: scraped text is escaped on every page, and a bad or late build
raises an alarm (scripts/site_health.py, .github/workflows/*.yml)."""
import importlib.util
import json
import re
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PAGES = (ROOT / 'app' / 'static' / 'command_center_source.html',
         ROOT / 'app' / 'templates' / 'signals.html',
         ROOT / 'public' / 'command_center.html',
         ROOT / 'public' / 'index.html')

_spec = importlib.util.spec_from_file_location('site_health', ROOT / 'scripts' / 'site_health.py')
health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health)

IST = timezone(timedelta(hours=5, minutes=30))


class TestEscaping(unittest.TestCase):
    """A feed headline or link must never become markup or script."""

    def test_every_link_from_data_goes_through_safe_url(self):
        for page in PAGES:
            html = page.read_text(encoding='utf-8')
            for href in re.findall(r'href="\$\{([^}]*)\}', html):
                self.assertTrue(href.startswith('safeUrl('), f'{page.name}: href="${{{href}}}"')

    def test_no_headline_is_inserted_raw(self):
        for page in PAGES:
            html = page.read_text(encoding='utf-8')
            self.assertNotRegex(html, r'>\$\{\w+\.title\}<', page.name)

    def test_safe_url_takes_web_addresses_only(self):
        for page in PAGES:
            html = page.read_text(encoding='utf-8')
            self.assertIn('function safeUrl(v)', html, page.name)
            self.assertIn("/^https?:\\/\\//i.test(url) ? esc(url) : '#'", html, page.name)


def _build(tmp, built_at, tiles=14, url='https://example.com/a'):
    data = {'_meta': {'built_at': built_at}, '_movers': [], '_pulse': [], '_features': [],
            '_record': {'quotes': []}}
    for i in range(14):
        data[f'tile{i}'] = {'name': f'T{i}', 'articles': [{'title': 'x', 'url': url}] if i < tiles else []}
    for name in health.REQUIRED:
        (tmp / name).write_text('x', encoding='utf-8')
    (tmp / 'command_center_data.json').write_text(json.dumps(data), encoding='utf-8')
    return tmp


class TestGate(unittest.TestCase):
    NOW = datetime(2026, 9, 28, 4, 0, tzinfo=timezone.utc)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)

    def test_a_good_build_passes(self):
        errors, warnings = health.gate_problems(_build(self.tmp, '2026-09-28T03:50:00+00:00'), self.NOW)
        self.assertEqual((errors, warnings), ([], []))

    def test_a_missing_page_stops_the_publish(self):
        _build(self.tmp, '2026-09-28T03:50:00+00:00')
        (self.tmp / 'brief.html').unlink()
        self.assertTrue(any('brief.html' in e for e in health.gate_problems(self.tmp, self.NOW)[0]))

    def test_old_data_stops_the_publish(self):
        errors, _ = health.gate_problems(_build(self.tmp, '2026-09-27T13:30:00+00:00'), self.NOW)
        self.assertTrue(any('not this build' in e for e in errors))

    def test_a_broken_scrape_stops_the_publish(self):
        errors, _ = health.gate_problems(_build(self.tmp, '2026-09-28T03:50:00+00:00', tiles=2), self.NOW)
        self.assertTrue(any('scrape looks broken' in e for e in errors))

    def test_a_thin_hour_publishes_with_a_warning(self):
        errors, warnings = health.gate_problems(_build(self.tmp, '2026-09-28T03:50:00+00:00', tiles=8), self.NOW)
        self.assertEqual(errors, [])
        self.assertTrue(warnings)

    def test_a_script_link_stops_the_publish(self):
        errors, _ = health.gate_problems(
            _build(self.tmp, '2026-09-28T03:50:00+00:00', url='javascript:alert(1)'), self.NOW)
        self.assertTrue(any('not web addresses' in e for e in errors))


class TestWatchdog(unittest.TestCase):
    SLOTS = ['09:00', '19:00']

    def _ist(self, day, hour, minute=0):
        return datetime(2026, 9, day, hour, minute, tzinfo=IST)

    def test_on_time(self):
        self.assertIsNone(health.missed_slot(self._ist(28, 9, 5), self._ist(28, 12), self.SLOTS))

    def test_a_late_morning_build_is_caught(self):
        """27 Sep's 19:00 data at 10:20 on the 28th: the 09:00 refresh is late."""
        self.assertEqual(health.missed_slot(self._ist(27, 19, 8), self._ist(28, 10, 20), self.SLOTS),
                         self._ist(28, 9))

    def test_the_scheduler_gets_an_hours_grace(self):
        self.assertIsNone(health.missed_slot(self._ist(27, 19, 8), self._ist(28, 9, 50), self.SLOTS))

    def test_a_missed_evening_is_caught_next_morning_too(self):
        self.assertEqual(health.missed_slot(self._ist(27, 9, 5), self._ist(28, 8, 0), self.SLOTS),
                         self._ist(27, 19))


class TestWorkflows(unittest.TestCase):
    BUILD = ROOT / '.github' / 'workflows' / 'build-signals.yml'
    WATCHDOG = ROOT / '.github' / 'workflows' / 'site-watchdog.yml'

    def _steps(self, path):
        wf = yaml.safe_load(path.read_text(encoding='utf-8'))
        return [step.get('run', '') for job in wf['jobs'].values() for step in job['steps']]

    def test_the_build_is_checked_before_it_is_published(self):
        runs = self._steps(self.BUILD)
        gate = next(i for i, r in enumerate(runs) if 'site_health.py gate' in r)
        publish = next(i for i, r in enumerate(runs) if 'signals-deploy' in r)
        commit = next(i for i, r in enumerate(runs) if 'git commit' in r and 'rebuild' in r)
        self.assertLess(gate, commit)
        self.assertLess(gate, publish)

    def test_the_live_site_is_checked_after_publishing(self):
        runs = self._steps(self.BUILD)
        self.assertIn('site_health.py deployed', runs[-1])

    def test_one_build_at_a_time(self):
        wf = yaml.safe_load(self.BUILD.read_text(encoding='utf-8'))
        self.assertEqual(wf['concurrency']['group'], 'build-signals')
        self.assertFalse(wf['concurrency']['cancel-in-progress'])

    def test_the_watchdog_runs_hourly_and_can_start_a_build(self):
        wf = yaml.safe_load(self.WATCHDOG.read_text(encoding='utf-8'))
        on = wf.get('on', wf.get(True))
        self.assertEqual(on['schedule'][0]['cron'], '20 * * * *')
        self.assertEqual(wf['permissions']['actions'], 'write')
        runs = ' '.join(self._steps(self.WATCHDOG))
        self.assertIn('site_health.py watchdog', runs)
        self.assertIn('gh workflow run build-signals.yml', runs)


if __name__ == '__main__':
    unittest.main()
