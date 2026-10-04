"""The feed refreshes every 3 hours, on the IST grid (00:00 ... 21:00).

The schedule lives in two places that must agree: the cron in
.github/workflows/build-signals.yml (UTC), which runs the build, and
REFRESH_SLOTS_IST in scripts/build_command_center_data.py, which the build
writes into the page's data so it can say when the next refresh is due.
"""
import ast
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / '.github' / 'workflows' / 'build-signals.yml'
BUILD = ROOT / 'scripts' / 'build_command_center_data.py'
IST_OFFSET = 5 * 60 + 30


def _cron_slots_ist():
    """IST 'HH:MM' for every time the workflow's schedule fires."""
    wf = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    triggers = wf.get('on', wf.get(True))   # YAML 1.1 reads a bare `on` as True
    slots = []
    for entry in triggers['schedule']:
        minute, hour, dom, month, dow = entry['cron'].split()
        assert (dom, month, dow) == ('*', '*', '*'), 'expected an every-day schedule'
        for h in hour.split(','):
            for m in minute.split(','):
                t = (int(h) * 60 + int(m) + IST_OFFSET) % (24 * 60)
                slots.append(f'{t // 60:02d}:{t % 60:02d}')
    return sorted(slots)


def _build_slots_ist():
    """REFRESH_SLOTS_IST, read without importing the build script."""
    for node in ast.parse(BUILD.read_text(encoding='utf-8')).body:
        if isinstance(node, ast.Assign) and any(
                getattr(t, 'id', None) == 'REFRESH_SLOTS_IST' for t in node.targets):
            return sorted(ast.literal_eval(node.value))
    raise AssertionError('REFRESH_SLOTS_IST not found in the build script')


def test_workflow_runs_every_three_hours_ist():
    assert _cron_slots_ist() == ['00:00', '03:00', '06:00', '09:00',
                                 '12:00', '15:00', '18:00', '21:00']


def test_page_schedule_matches_the_workflow():
    """Otherwise the page would promise a refresh the workflow never runs."""
    assert _build_slots_ist() == _cron_slots_ist()


def test_workflow_can_still_be_run_by_hand():
    wf = yaml.safe_load(WORKFLOW.read_text(encoding='utf-8'))
    assert 'workflow_dispatch' in wf.get('on', wf.get(True))
