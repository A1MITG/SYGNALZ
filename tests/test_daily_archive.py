"""The daily archive keeps the last build of each IST day and draws it at /archive.

scripts/archive_snapshot.py runs at the start of every build, while
public/command_center_data.json still holds the previous build. A build from
an earlier IST day was that day's last, so it is filed once, slimmed, as
public/snapshots/YYYY-MM-DD.json. The workflow keeps those files in the
working branch (signals-deploy is replaced on every build) and publishes them
with archive.html.
"""
import json
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import yaml

from scripts.archive_snapshot import (ARCHIVE_DIR, archived_dates, render_archive_page,
                                      slim, snapshot)

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'build-signals.yml'


def build(built_at):
    return {
        'global': {'name': 'Global Affairs', 'articles': [{'title': 'T', 'url': 'https://x', 'image': ''}]},
        '_features': [{'title': 'F'}],
        '_featured': {'title': 'F'},
        '_record': {'quotes': [], 'leaders': [], 'window_days': 1, 'generated': 'now'},
        '_meta': {'data_date': '2026-10-23', 'fetched_minutes_ago': 0, 'stale_excluded': 3,
                  'built_at': built_at, 'refresh_slots_ist': ['00:00', '21:00']},
    }


class TestSnapshot(unittest.TestCase):

    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())

    def write(self, data):
        (self.dir / 'command_center_data.json').write_text(json.dumps(data), encoding='utf-8')

    def test_a_past_days_last_build_is_filed_under_its_ist_date(self):
        # 17:21 UTC on the 23rd is 22:51 IST on the 23rd.
        self.write(build('2026-10-23T17:21:25+00:00'))
        out = snapshot(now=datetime(2026, 10, 23, 19, 0, tzinfo=timezone.utc), public_dir=self.dir)
        self.assertEqual(out, self.dir / 'snapshots' / '2026-10-23.json')

    def test_ist_midnight_not_utc_midnight_ends_the_day(self):
        # 19:00 UTC on the 23rd is already 00:30 IST on the 24th; 18:00 UTC is not.
        self.write(build('2026-10-23T17:21:25+00:00'))
        self.assertIsNone(snapshot(now=datetime(2026, 10, 23, 18, 0, tzinfo=timezone.utc),
                                   public_dir=self.dir))

    def test_todays_build_is_left_for_a_later_one(self):
        self.write(build('2026-10-24T04:53:06+00:00'))
        self.assertIsNone(snapshot(now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc),
                                   public_dir=self.dir))
        self.assertFalse((self.dir / 'snapshots').exists())

    def test_a_filed_day_is_never_rewritten(self):
        now = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
        self.write(build('2026-10-23T17:21:25+00:00'))
        first = snapshot(now=now, public_dir=self.dir)
        before = first.read_bytes()
        self.write(build('2026-10-23T18:00:00+00:00'))
        self.assertIsNone(snapshot(now=now, public_dir=self.dir))
        self.assertEqual(first.read_bytes(), before)

    def test_the_file_is_minified_and_slimmed(self):
        self.write(build('2026-10-23T17:21:25+00:00'))
        out = snapshot(now=datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc), public_dir=self.dir)
        text = out.read_text(encoding='utf-8')
        self.assertNotIn(', ', text.replace('Global Affairs', ''))
        data = json.loads(text)
        self.assertNotIn('_featured', data)
        self.assertNotIn('generated', data['_record'])
        self.assertEqual(data['_meta'], {'stale_excluded': 3, 'built_at': '2026-10-23T17:21:25+00:00'})
        self.assertEqual(data['global'], build('x')['global'])

    def test_featured_is_kept_when_there_is_no_rotation(self):
        data = build('2026-09-24T17:55:41+00:00')
        del data['_features']
        self.assertIn('_featured', slim(data))

    def test_no_data_or_no_timestamp_files_nothing(self):
        self.assertIsNone(snapshot(public_dir=self.dir))
        self.write({'_meta': {}})
        self.assertIsNone(snapshot(public_dir=self.dir))


class TestArchivePage(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        src = (ROOT / 'app' / 'static' / 'command_center_source.html').read_text(encoding='utf-8')
        cls.page = render_archive_page(src, ['2026-10-22', '2026-10-23'])

    def test_it_reads_the_chosen_days_snapshot_before_the_main_script(self):
        inject = self.page.index("window.CC_DATA_URL = './snapshots/'")
        self.assertLess(inject, self.page.index('const CC_DATA_URL'))
        self.assertIn('var dates = ["2026-10-22", "2026-10-23"];', self.page)

    def test_it_is_kept_out_of_search_and_points_canonical_at_itself(self):
        self.assertIn('<meta name="robots" content="noindex">', self.page)
        self.assertIn('href="https://news-letter-cxo.vercel.app/archive"', self.page)

    def test_todays_brief_link_is_hidden_on_a_past_day(self):
        self.assertIn('a[href="/brief"] { display: none !important; }', self.page)

    def test_the_committed_snapshots_are_well_formed(self):
        for day in archived_dates():
            data = json.loads((ARCHIVE_DIR / f'{day}.json').read_text(encoding='utf-8'))
            self.assertTrue(data['_meta']['built_at'], day)


class TestNavLink(unittest.TestCase):
    """Amit chose the top menu (2026-10-24): Archive sits next to Today's Brief."""

    def test_archive_follows_todays_brief_in_the_nav_on_every_screen(self):
        src = (ROOT / 'app' / 'static' / 'command_center_source.html').read_text(encoding='utf-8')
        self.assertIn('<a class="link" href="/brief">Today\'s Brief</a>\n'
                      '            <a class="link" href="/archive">Archive</a>', src)
        self.assertIn('.nav-right a.link[href="/archive"],', src)


class TestWorkflow(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        steps = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))['jobs']['build']['steps']
        cls.runs = [s.get('run', '') for s in steps]

    def index(self, needle):
        return next(i for i, r in enumerate(self.runs) if needle in r)

    def test_the_snapshot_is_taken_before_the_build_overwrites_the_data(self):
        self.assertLess(self.index('scripts/archive_snapshot.py'),
                        self.index('scripts/build_static_signals.py'))
        self.assertLess(self.index('scripts/archive_snapshot.py'),
                        self.index('scripts/build_command_center_data.py'))

    def test_snapshots_survive_the_reset_and_are_committed(self):
        run = self.runs[self.index('Signals + Command Center rebuild')]
        self.assertIn('archive.html', re.search(r'BUILT="([^"]*)"', run).group(1))
        self.assertLess(run.index('cp -r public/snapshots/. /tmp/built/snapshots/'),
                        run.index('git reset -q --hard FETCH_HEAD'))
        self.assertIn('cp -rn /tmp/built/snapshots/. public/snapshots/', run)

    def test_the_archive_is_published(self):
        run = next(r for r in self.runs if 'signals-deploy' in r and 'git init' in r)
        self.assertIn('cp public/archive.html /tmp/signals-deploy/archive.html', run)
        self.assertIn('cp -r public/snapshots/. /tmp/signals-deploy/snapshots/', run)
        add = next(line for line in run.splitlines() if line.strip().startswith('git add'))
        self.assertIn(' archive.html ', add)
        self.assertIn(' snapshots ', add)


if __name__ == '__main__':
    unittest.main()
