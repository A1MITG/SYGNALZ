# scripts/build_bloom.py
"""Build Bloom, the good-news page, for the static deploy.

Run by .github/workflows/build-signals.yml after the Command Center build.
Scrapes the good-news and CSR sources (app/scraper/good_news.py), keeps the
positive stories of the last week (app/analysis/bloom.py), and writes:
    public/bloom.json   the stories the page fetches
    public/bloom.html   the page, copied from app/static/bloom.html

Bloom has its own sources and never touches the Command Center data, so a
failure here leaves the rest of the site's build as it was.
"""
import json
import logging
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.analysis.bloom import build_bloom, load_pinned
from app.scraper.article_images import fill_missing_images
from app.scraper.good_news import get_good_news

logging.basicConfig(level=logging.INFO, format='%(message)s')
logger = logging.getLogger(__name__)

PUBLIC_DIR = ROOT / 'public'


def main():
    articles = get_good_news()
    data = build_bloom(articles, pinned=load_pinned())
    shown = ([data['pick']] if data['pick'] else []) + data['stories']
    fill_missing_images(shown)
    logger.info("Bloom: %d good-news stories from %d scraped.", data['_meta']['count'], len(articles))

    PUBLIC_DIR.mkdir(exist_ok=True)
    shutil.copy(ROOT / 'app' / 'static' / 'bloom.html', PUBLIC_DIR / 'bloom.html')
    # Every source down: keep the last build's stories rather than an empty page.
    if not data['_meta']['count'] and (PUBLIC_DIR / 'bloom.json').exists():
        logger.warning("Bloom: no stories this run; keeping the previous bloom.json.")
        return
    (PUBLIC_DIR / 'bloom.json').write_text(json.dumps(data), encoding='utf-8')
    logger.info("Wrote %s and %s", PUBLIC_DIR / 'bloom.json', PUBLIC_DIR / 'bloom.html')


if __name__ == '__main__':
    main()
