# app/main.py
import logging
from datetime import datetime, timedelta, timezone

from flask import Flask, jsonify, render_template
from dotenv import load_dotenv

from .analysis.brief import build_brief, render_brief
from .analysis.command_center import build_engine_data, build_mover_cards
from .analysis.signals import synthesize_signals
from .api.routes import api_blueprint
from .scraper.store import (cache_age_minutes, get_cache_date, get_cached_articles,
                            start_background_refresh)
from config.env_check import is_production, validate_environment

load_dotenv()
logging.basicConfig(level=logging.INFO)


def create_app():
    """Create and configure an instance of the Flask application."""
    validate_environment()

    app = Flask(__name__)
    app.config.from_object('config.config.Config')

    app.register_blueprint(api_blueprint, url_prefix='/api')

    # Keep the article cache warm on a timer so no reader ever waits on a
    # scrape, and the news moves on even when nobody is looking. Started on
    # the first request, not at import: the dev server's reloader imports this
    # module in two processes, and starting here would orphan a thread in the
    # parent that scrapes on the same timer as the child's.
    @app.before_request
    def _ensure_refresher():
        start_background_refresh()

    @app.route('/')
    def command_center():
        """The Command Center is the front door: full executive intelligence layout."""
        return render_template('command_center.html')

    @app.route('/brief')
    def brief():
        """Today's Brief: the tiles' stories as a text-only newsletter.

        Reads the cache as it stands rather than scraping, so the page never
        keeps a reader waiting; it says when its articles were fetched.
        """
        articles = get_cached_articles()
        data_date = get_cache_date()
        signals_data = synthesize_signals(articles, data_date=data_date,
                                          include_tile_signals=True)
        by_title = {a.get('title'): a for a in articles}
        # Same tiles as the page: People Movers' stories are left out.
        movers = build_mover_cards(articles)
        sections = build_brief(build_engine_data(signals_data, by_title, exclude=movers), by_title)
        age = cache_age_minutes()
        fetched = None if age is None else datetime.now(timezone.utc) - timedelta(minutes=age)
        return render_brief(sections, data_date, fetched)

    @app.route('/signals')
    def signals():
        """Compact terminal-style Signals view — the other low-bandwidth reading view."""
        return render_template('signals.html')

    @app.route('/health')
    def health():
        return jsonify({"status": "ok"})

    return app

# Create app instance for Flask CLI / WSGI servers
app = create_app()

if __name__ == '__main__':
    app.run(debug=not is_production())
