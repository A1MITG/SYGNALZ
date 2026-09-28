"""Is the live site healthy? Three checks for the build workflows (BP-55).

    python scripts/site_health.py gate       before publishing: is public/ fit to go live?
    python scripts/site_health.py deployed   after publishing: is Vercel serving this build?
    python scripts/site_health.py watchdog   hourly: is the live data late for a refresh slot?

Each prints what it found (and, on GitHub, writes it to the run's summary)
and exits non-zero on a problem. That fails the workflow run, and GitHub
emails the repo owner: the email is the alert. Scheduled builds have started
3 h 50 min and 5.5 h late, and once not at all, with nobody told.

Standard library only, so the hourly watchdog needs no install step.
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / 'public'
LIVE_URL = os.environ.get('LIVE_URL', 'https://news-letter-cxo.vercel.app').rstrip('/')
IST = timezone(timedelta(hours=5, minutes=30))

# What the deploy step publishes: a missing or empty one would put a broken
# page live.
REQUIRED = ('command_center.html', 'command_center_data.json', 'index.html',
            'brief.html', 'signals.json', 'signal.css')

MIN_LIVE_TILES = 3     # fewer tiles with a story: the scrape broke. Do not publish.
WARN_LIVE_TILES = 10   # fewer: a quiet hour or a source down. Publish, and say so.
MAX_BUILD_AGE = timedelta(hours=2)    # older data is not this build's
LATE_GRACE = timedelta(minutes=60)    # GitHub's scheduler often starts a run this late
DEPLOY_WAIT = timedelta(minutes=8)    # Vercel redeploys within a minute or two
LATE = 3                              # the watchdog's exit code for late data

# Every section whose items carry a link, as (key, list of items).
_LINKED = ('_movers', '_pulse', '_features')


def _built_at(data):
    try:
        return datetime.fromisoformat(data['_meta']['built_at'].replace('Z', '+00:00'))
    except (KeyError, TypeError, AttributeError, ValueError):
        return None


def _hours(delta):
    return f'{delta.total_seconds() / 3600:.1f} h'


def _links(data):
    """Every link the page will render from this data."""
    for value in data.values():
        if isinstance(value, dict) and isinstance(value.get('articles'), list):
            yield from (a.get('url') for a in value['articles'])
    for key in _LINKED:
        yield from (item.get('url') for item in data.get(key) or [])
    yield from (q.get('url') for q in (data.get('_record') or {}).get('quotes') or [])


def gate_problems(public=PUBLIC, now=None):
    """(errors, warnings) for the build in public/. Any error: do not publish."""
    now = now or datetime.now(timezone.utc)
    errors, warnings = [], []
    for name in REQUIRED:
        path = Path(public) / name
        if not path.is_file() or path.stat().st_size == 0:
            errors.append(f'{name} is missing or empty')
    try:
        data = json.loads((Path(public) / 'command_center_data.json').read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        errors.append(f'command_center_data.json cannot be read: {e}')
        return errors, warnings

    built = _built_at(data)
    if built is None:
        errors.append('_meta.built_at is missing: the page could not say how old it is')
    elif now - built > MAX_BUILD_AGE:
        errors.append(f'the data was built {_hours(now - built)} ago, so it is not this build')

    tiles = {key: value for key, value in data.items()
             if isinstance(value, dict) and isinstance(value.get('articles'), list)}
    empty = sorted(key for key, value in tiles.items() if not value['articles'])
    live = len(tiles) - len(empty)
    if live < MIN_LIVE_TILES:
        errors.append(f'only {live} of {len(tiles)} tiles have a story: the scrape looks broken')
    elif live < WARN_LIVE_TILES:
        warnings.append(f'{live} of {len(tiles)} tiles have a story; empty: {", ".join(empty)}')

    bad = [url for url in _links(data) if not str(url or '').startswith(('https://', 'http://'))]
    if bad:
        errors.append(f'{len(bad)} links are not web addresses, e.g. {bad[0]!r}')
    return errors, warnings


def missed_slot(built, now, slots):
    """The latest refresh slot the live data is late for, or None.

    ``slots`` are IST times ("09:00"). A slot is missed when it passed more
    than LATE_GRACE ago and the data was built before it.
    """
    local = now.astimezone(IST)
    passed = []
    for day in (local.date(), local.date() - timedelta(days=1)):
        for slot in slots:
            hour, minute = map(int, slot.split(':'))
            at = datetime(day.year, day.month, day.day, hour, minute, tzinfo=IST)
            if at + LATE_GRACE <= now:
                passed.append(at)
    if not passed:
        return None
    last = max(passed)
    return last if built < last else None


def _fetch_json(url):
    request = urllib.request.Request(f'{url}?t={int(time.time())}',
                                     headers={'User-Agent': 'signal-site-health', 'Cache-Control': 'no-cache'})
    with urllib.request.urlopen(request, timeout=30) as response:
        return json.loads(response.read().decode('utf-8'))


def _status(url):
    request = urllib.request.Request(url, headers={'User-Agent': 'signal-site-health'})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status
    except urllib.error.HTTPError as e:
        return e.code


def _summary(lines):
    """Print, and add to the GitHub run's summary page when there is one."""
    for line in lines:
        print(line)
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a', encoding='utf-8') as f:
            f.write('\n'.join(lines) + '\n')


def gate():
    errors, warnings = gate_problems()
    _summary(['### Build check'] + [f'- Error: {e}' for e in errors]
             + [f'- Warning: {w}' for w in warnings] + ([] if errors or warnings else ['- All checks passed.']))
    for w in warnings:
        print(f'::warning::{w}')
    for e in errors:
        print(f'::error::{e}')
    return 1 if errors else 0


def deployed():
    data = json.loads((PUBLIC / 'command_center_data.json').read_text(encoding='utf-8'))
    want = data['_meta']['built_at']
    deadline = datetime.now(timezone.utc) + DEPLOY_WAIT
    seen = None
    while datetime.now(timezone.utc) < deadline:
        try:
            seen = (_fetch_json(f'{LIVE_URL}/command_center_data.json').get('_meta') or {}).get('built_at')
        except (OSError, ValueError):
            seen = None
        if seen == want:
            break
        time.sleep(20)
    if seen != want:
        print(f'::error::{LIVE_URL} still serves the build of {seen}, not {want}: the deploy did not go live')
        return 1
    down = [path for path in ('/', '/brief', '/signals') if _status(LIVE_URL + path) != 200]
    if down:
        print(f'::error::{LIVE_URL} does not answer on {", ".join(down)}')
        return 1
    _summary([f'### Live site', f'- {LIVE_URL} serves this build ({want}); /, /brief and /signals answer.'])
    return 0


def watchdog():
    meta = _fetch_json(f'{LIVE_URL}/command_center_data.json').get('_meta') or {}
    built = _built_at({'_meta': meta})
    if built is None:
        print(f'::error::{LIVE_URL} serves no build time')
        return 1
    now = datetime.now(timezone.utc)
    slot = missed_slot(built, now, meta.get('refresh_slots_ist') or ['09:00', '19:00'])
    if slot is None:
        print(f'On time: built {built.astimezone(IST):%d %b %H:%M} IST, {_hours(now - built)} ago.')
        return 0
    _summary([f'### Live data is late',
              f'- The {slot:%H:%M} IST refresh of {slot:%d %b} has not reached {LIVE_URL}.',
              f'- The live data was built {built.astimezone(IST):%d %b %H:%M} IST, {_hours(now - built)} ago.'])
    return LATE


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('check', choices=('gate', 'deployed', 'watchdog'))
    return {'gate': gate, 'deployed': deployed, 'watchdog': watchdog}[parser.parse_args(argv).check]()


if __name__ == '__main__':
    sys.exit(main())
