# app/api/routes.py
import logging
from datetime import datetime, timedelta, timezone
from flask import Blueprint, jsonify, request
from ..scraper.store import get_articles, get_cache_date, cache_age_minutes
from ..scraper.article_images import fill_missing_images, fill_signal_images
from ..analysis.synthesis import synthesize_articles
from ..analysis.signals import synthesize_signals
from ..analysis.command_center import build_engine_data, build_features, build_people_rows
from ..scraper.leader_quotes import get_leader_quotes

logger = logging.getLogger(__name__)
api_blueprint = Blueprint('api', __name__)


@api_blueprint.route('/newsletter', methods=['GET'])
def get_newsletter():
    """
    This endpoint triggers the scraper, analysis, and returns the
    synthesized newsletter.
    """
    try:
        scraped_articles = get_articles(force_refresh='refresh' in request.args)
        logger.info("Serving %d articles for newsletter (daily cache).", len(scraped_articles))

        newsletter_data = synthesize_articles(scraped_articles,
                                              data_date=get_cache_date())
        return jsonify(newsletter_data)
    except Exception:
        logger.exception("Error generating newsletter")
        return jsonify({"error": "Failed to generate newsletter"}), 500


@api_blueprint.route('/signals', methods=['GET'])
def get_signals():
    """Scrape and group articles under the six launch Signals."""
    try:
        scraped_articles = get_articles(force_refresh='refresh' in request.args)
        logger.info("Serving %d articles for signals (daily cache).", len(scraped_articles))
        return jsonify(synthesize_signals(scraped_articles,
                                          data_date=get_cache_date()))
    except Exception:
        logger.exception("Error generating signals")
        return jsonify({"error": "Failed to generate signals"}), 500


@api_blueprint.route('/command-center', methods=['GET'])
def get_command_center():
    """Live Command Center tiles: the same scoring engine, mapped to tile ids.

    Shares app/analysis/command_center.py with the static build script, so the
    live page and a static deploy cannot drift apart.
    """
    try:
        scraped_articles = get_articles(force_refresh='refresh' in request.args)
        logger.info("Serving %d articles for command center (daily cache).",
                    len(scraped_articles))
        signals_data = synthesize_signals(scraped_articles,
                                          data_date=get_cache_date(),
                                          include_tile_signals=True)
        by_title = {a.get('title'): a for a in scraped_articles}
        fill_signal_images(signals_data, by_title)
        # The people rows first: a story People Movers shows is left out of
        # the tiles, so one move is not also a tech or GCC headline.
        # Never blocks: a stale quote set refreshes in the background.
        people = build_people_rows(scraped_articles, get_leader_quotes())
        payload = build_engine_data(signals_data, by_title, exclude=people['_movers'])
        # The Featured Analysis rotation; _featured is its first card.
        features = build_features(payload, by_title)
        payload['_features'] = features
        payload['_featured'] = features[0] if features else None
        payload.update(people)
        # A move card leads with its story's picture; fill the ones the feed
        # left without from the article's own page (FETCH_ARTICLE_IMAGES).
        fill_missing_images(payload['_movers'])
        # Let the page state how fresh it is rather than leaving the reader to
        # trust an undated grid.
        age = cache_age_minutes()
        payload['_meta'] = {
            'data_date': get_cache_date(),
            'fetched_minutes_ago': None if age is None else round(age),
            'stale_excluded': signals_data.get('stale_excluded', 0),
        }
        # When the articles were fetched: the world-time line's "Updated". No
        # refresh_slots_ist, since the app re-scrapes on its own timer rather
        # than the static site's schedule.
        if age is not None:
            fetched = datetime.now(timezone.utc) - timedelta(minutes=age)
            payload['_meta']['built_at'] = fetched.isoformat(timespec='seconds')
        return jsonify(payload)
    except Exception:
        logger.exception("Error generating command center data")
        return jsonify({"error": "Failed to generate command center data"}), 500
