# scripts/build_command_center_data.py
"""Build the live data snapshot for the Command Center static deploy.

Run twice a day, at 09:00 and 19:00 IST (alongside
scripts/build_static_signals.py, by .github/workflows/build-signals.yml), to
write:
    public/command_center_data.json
    public/brief.html   Today's Brief, the tiles' stories as a text-only page

Maps the scored Signal categories from app/analysis/signals.py onto the
Command Center's engine ids: the Signals page's six plus the tile-only
domains in signals.TILE_SIGNALS (Banking first, added one at a time). Tiles
with no scoring engine behind them yet are listed in
command_center.COMING_SOON_ENGINES and written out honestly as comingSoon
rather than fabricated content.

Signal Executive is intentionally left out of this mapping — the Command
Center layout has no tile for it.
"""
import json
import logging
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv
load_dotenv()

from app.scraper.store import get_articles, get_cache_date
from app.scraper.article_images import fill_missing_images, fill_signal_images
from app.analysis.signals import synthesize_signals
from app.analysis.brief import build_brief, render_brief
from app.analysis.command_center import (build_engine_data, build_features, build_people_rows,
                                         load_pinned_moves, load_pinned_stories)
from app.scraper.leader_quotes import get_leader_quotes

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

PUBLIC_DIR = ROOT / 'public'

# When the scheduled build runs, in IST: every 3 hours. The cron in
# .github/workflows/build-signals.yml is what actually runs it;
# tests/test_refresh_schedule.py keeps the two in step.
#
# On Vercel's free plan a cron can only fire once a day, so the deploy
# branch's vercel.json covers just two of these slots (09:00 and 21:00 IST).
# The hourly watchdog (site-watchdog.yml) drives the other six: it notices
# the data is past a slot and starts the build itself.
REFRESH_SLOTS_IST = ('00:00', '03:00', '06:00', '09:00',
                     '12:00', '15:00', '18:00', '21:00')

# Injected into the Flask template only: tells the page to read the live API
# instead of the static snapshot.
LIVE_URL_INJECT = '''<script>
    // Served by Flask: read live classified articles from the API, not the
    // prebuilt static snapshot. Must precede the main script block.
    window.CC_DATA_URL = "/api/command-center";
</script>
</head>'''

# A build does not re-scrape by default. store.py already scrapes at most
# once a day; forcing a refresh here meant every build replaced the single
# cache slot, so two surfaces built minutes apart could rest on two different
# article sets. Pass --refresh when you actually want new articles.
def main(refresh=False):
    articles = get_articles(force_refresh=refresh)
    data_date = get_cache_date()
    logger.info("%d articles from the %s cache%s.",
                len(articles), data_date or "undated",
                " (re-scraped)" if refresh else "")

    # The Command Center also scores its tile-only domains (Banking, ...);
    # the Signals page build does not, and keeps its six.
    signals_data = synthesize_signals(articles, data_date=data_date, include_tile_signals=True)
    by_name = {s['name']: s for s in signals_data['signals']}

    by_title = {a.get('title'): a for a in articles}
    fill_signal_images(signals_data, by_title)
    # Pins from config/pinned_stories.yaml lead their tile (and Featured,
    # when marked) until they expire.
    engine_data = build_engine_data(signals_data, by_title, pinned=load_pinned_stories())
    # The Featured Analysis rotation; _featured is its first card.
    features = build_features(engine_data, by_title)
    engine_data['_features'] = features
    engine_data['_featured'] = features[0] if features else None
    # The static build waits for a fresh quote set; the live API never does.
    # Pins from config/pinned_moves.yaml lead People Movers until they expire.
    engine_data.update(build_people_rows(articles, get_leader_quotes(block=True),
                                         pinned=load_pinned_moves()))
    # Each move card leads with its story's own picture.
    fill_missing_images(engine_data['_movers'])
    # The static page shows the same freshness stamp as the live one; without
    # _meta it would render blank on the Vercel deploy.
    # built_at and refresh_slots_ist let the page say when it was last
    # refreshed and when the next refresh is due. fetched_minutes_ago stays 0
    # for the current "Sources checked" stamp, which is only true at build time.
    built_at = datetime.now(timezone.utc)
    engine_data['_meta'] = {
        'data_date': data_date,
        'fetched_minutes_ago': 0,
        'stale_excluded': signals_data.get('stale_excluded', 0),
        'built_at': built_at.isoformat(timespec='seconds'),
        'refresh_slots_ist': list(REFRESH_SLOTS_IST),
    }

    PUBLIC_DIR.mkdir(exist_ok=True)
    out_path = PUBLIC_DIR / 'command_center_data.json'
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(engine_data, f, ensure_ascii=False)
    logger.info("Wrote %s", out_path)

    # Today's Brief, from the same engine data, so it lists exactly the
    # stories the tiles cycle through. "Updated" matches the page's line.
    brief_out = PUBLIC_DIR / 'brief.html'
    brief_out.write_text(
        render_brief(build_brief(engine_data, by_title), data_date, built_at),
        encoding='utf-8')
    logger.info("Wrote %s", brief_out)

    html_src = ROOT / 'app' / 'static' / 'command_center_source.html'
    source_html = html_src.read_text(encoding='utf-8')

    # Static deploy copy: no CC_DATA_URL, so the page falls back to the
    # committed command_center_data.json sitting beside it.
    html_out = PUBLIC_DIR / 'command_center.html'
    shutil.copy(html_src, html_out)
    logger.info("Wrote %s", html_out)

    # Flask template: same markup, plus the live API URL. Generated from the
    # same source so the served page and the static deploy cannot drift.
    template_out = ROOT / 'app' / 'templates' / 'command_center.html'
    template_html = source_html.replace('</head>', LIVE_URL_INJECT, 1)
    # Relative asset paths work for the static deploy (files sit beside the
    # page); Flask serves the same files from /static/, so rewrite them.
    template_html = template_html.replace('src="./img/', 'src="/static/img/')
    template_out.write_text(template_html, encoding='utf-8')
    logger.info("Wrote %s", template_out)


if __name__ == '__main__':
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument('--refresh', action='store_true',
                   help='re-scrape before building (overwrites the day cache)')
    main(**vars(p.parse_args()))