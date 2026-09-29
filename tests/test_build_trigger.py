"""BP-56: Vercel Cron starts the build at 09:00 and 19:00 IST.

GitHub's own scheduler started every scheduled build 4-7 hours late (25-28
Sep 2026). Vercel Cron calls deploy/api/trigger-build.js, which starts the
workflow through GitHub's API. The workflow publishes the function and
writes the crons into the deploy branch's vercel.json.
"""
import json
import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

import yaml

from tests.test_refresh_schedule import IST_OFFSET, _build_slots_ist

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'build-signals.yml'
FUNCTION = ROOT / 'deploy' / 'api' / 'trigger-build.js'


def _publish_step():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    return next(step['run'] for job in wf['jobs'].values() for step in job['steps']
                if 'signals-deploy' in step.get('run', '') and 'git init' in step.get('run', ''))


def _vercel_json():
    """The vercel.json the publish step writes, parsed."""
    match = re.search(r"<<'JSON'\n(.*?)\nJSON\n", _publish_step(), re.S)
    assert match, 'the publish step no longer writes vercel.json from a heredoc'
    return json.loads(match.group(1))


class TestDeployConfig(unittest.TestCase):

    def test_clean_urls_stay(self):
        self.assertIs(_vercel_json()['cleanUrls'], True)

    def test_the_crons_fire_at_the_refresh_slots(self):
        """The same slots the page promises (REFRESH_SLOTS_IST)."""
        slots = []
        for cron in _vercel_json()['crons']:
            self.assertEqual(cron['path'], '/api/trigger-build')
            minute, hour, dom, month, dow = cron['schedule'].split()
            # Vercel's free plan runs a cron at most once a day.
            self.assertEqual((dom, month, dow), ('*', '*', '*'))
            self.assertNotIn(',', minute + hour)
            t = (int(hour) * 60 + int(minute) + IST_OFFSET) % (24 * 60)
            slots.append(f'{t // 60:02d}:{t % 60:02d}')
        self.assertEqual(sorted(slots), _build_slots_ist())

    def test_the_function_is_published(self):
        run = _publish_step()
        self.assertIn('cp deploy/api/trigger-build.js /tmp/signals-deploy/api/', run)
        add = next(line for line in run.splitlines() if line.strip().startswith('git add'))
        self.assertTrue(add.rstrip().endswith(' api'), add)


# Runs the function in Node with a stand-in for fetch: no network, no secrets.
_HARNESS = r"""
const handler = require(process.argv[1]);
const calls = [];
globalThis.fetch = async (url, init = {}) => {
    calls.push({ url, method: init.method || 'GET', body: init.body || null });
    const busy = process.env.TEST_BUSY === '1';
    return { ok: true, status: url.endsWith('/dispatches') ? 204 : 200,
             json: async () => ({ workflow_runs: busy ? [{ status: 'in_progress' }] : [{ status: 'completed' }] }) };
};
const res = { code: 0, body: null,
              status(c) { this.code = c; return this; }, json(b) { this.body = b; return this; } };
handler({ headers: { authorization: process.env.TEST_AUTH || '' } }, res)
    .then(() => console.log(JSON.stringify({ code: res.code, body: res.body, calls })));
"""


@unittest.skipUnless(shutil.which('node'), 'Node is not installed')
class TestFunction(unittest.TestCase):

    OWN = ('CRON_SECRET', 'GITHUB_DISPATCH_TOKEN', 'TEST_AUTH', 'TEST_BUSY')

    def _call(self, env):
        # The machine's environment (Node on Windows needs SYSTEMROOT), minus
        # anything this test sets itself.
        base = {k: v for k, v in os.environ.items() if k not in self.OWN}
        out = subprocess.run(['node', '-e', _HARNESS, str(FUNCTION)], capture_output=True, text=True,
                             env={**base, **env}, timeout=30)
        self.assertEqual(out.returncode, 0, out.stderr)
        return json.loads(out.stdout)

    def test_does_nothing_until_configured(self):
        result = self._call({})
        self.assertEqual(result['code'], 503)
        self.assertEqual(result['calls'], [])

    def test_refuses_a_caller_without_the_secret(self):
        result = self._call({'CRON_SECRET': 's3cret', 'GITHUB_DISPATCH_TOKEN': 't',
                             'TEST_AUTH': 'Bearer wrong'})
        self.assertEqual(result['code'], 401)
        self.assertEqual(result['calls'], [])

    def test_starts_the_build_on_final(self):
        result = self._call({'CRON_SECRET': 's3cret', 'GITHUB_DISPATCH_TOKEN': 't',
                             'TEST_AUTH': 'Bearer s3cret'})
        self.assertEqual(result['code'], 200)
        self.assertTrue(result['body']['started'])
        dispatch = result['calls'][-1]
        self.assertTrue(dispatch['url'].endswith('/repos/A1MITG/SYGNALZ/actions/workflows/build-signals.yml/dispatches'))
        self.assertEqual(dispatch['method'], 'POST')
        self.assertEqual(json.loads(dispatch['body']), {'ref': 'final'})

    def test_one_build_at_a_time(self):
        result = self._call({'CRON_SECRET': 's3cret', 'GITHUB_DISPATCH_TOKEN': 't',
                             'TEST_AUTH': 'Bearer s3cret', 'TEST_BUSY': '1'})
        self.assertEqual(result['code'], 200)
        self.assertFalse(result['body']['started'])
        self.assertFalse(any(c['url'].endswith('/dispatches') for c in result['calls']))


if __name__ == '__main__':
    unittest.main()
