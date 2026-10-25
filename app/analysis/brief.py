# app/analysis/brief.py
"""Today's Brief: the Command Center's stories as a text-only newsletter.

One section per tile, in the page's tile order, each holding the stories
that tile cycles through: a linked headline, its source, and one line from
the feed's own description. No images and no scripts, so it reads the same
in a browser, in print, and pasted into an email.

Built from build_engine_data()'s output, so the brief and the tiles cannot
disagree about which stories lead. Rendered from one template by the static
build (scripts/build_command_center_data.py -> public/brief.html) and by
Flask's /brief.
"""
import html
import re
from datetime import date, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from jinja2 import Environment, FileSystemLoader, select_autoescape

# A tile cycles its first five stories (articles.slice(0, 5) on the page), so
# the brief carries the same five.
PER_SECTION = 5

# The Command Center's tile order: ENGINE_ORDER in
# app/static/command_center_source.html. tests/test_todays_brief.py keeps the
# two in step.
TILE_ORDER = ('global', 'economy', 'ai', 'gcc', 'insurance', 'banking',
              'manufacturing', 'energy', 'defence', 'cyber', 'supplychain',
              'healthcare', 'telecom', 'climate')

SUMMARY_LIMIT = 170
# Shorter than this is a fragment ("St.") or a stub, not a summary.
MIN_SUMMARY = 40

IST = timezone(timedelta(hours=5, minutes=30))

# Trailing feed boilerplate, removed before the first sentence is taken.
_TRAILER = re.compile(r"\s*(The post .*? appeared first on .*|Continue reading.*|Read more.*"
                      r"|\[…\]|\[\.\.\.\])\s*$", re.I)
# Whole sentences that say nothing about the story.
_FILLER = re.compile(r"^(this live blog is (now )?closed|happy \w+day!|follow the day.s news live"
                     r"|from the newsletter\b|get our breaking news email)", re.I)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"“‘'])")
# "Christopher J." ends in a full stop but not a sentence.
_INITIAL = re.compile(r"\b[A-Z]\.$")

_env = Environment(
    loader=FileSystemLoader(Path(__file__).resolve().parents[1] / 'templates'),
    autoescape=select_autoescape(['html']),
)


def one_line(summary, title=''):
    """The first real sentence of a feed description, or '' when there is none.

    Feeds prefix the headline, append "The post ... appeared first on ...",
    and open live blogs with "This live blog is now closed."; none of that is
    a summary. Capped at SUMMARY_LIMIT on a word boundary.
    """
    text = html.unescape(re.sub(r"<[^>]+>", " ", summary or ""))
    text = _TRAILER.sub("", re.sub(r"\s+", " ", text)).strip()
    if title and text.lower().startswith(title.lower()):
        text = text[len(title):].lstrip(" .:-–—")

    line = ""
    for sentence in _SENTENCE_BREAK.split(text):
        if not sentence or _FILLER.match(sentence):
            continue
        line = f"{line} {sentence}".strip()
        if len(line) >= MIN_SUMMARY and not _INITIAL.search(line):
            break
    if len(line) < MIN_SUMMARY:
        return ""
    if len(line) > SUMMARY_LIMIT:
        line = line[:SUMMARY_LIMIT].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    return line


def source_name(url):
    """The publisher's host, without www."""
    host = urlparse(url or '').netloc.lower()
    return host[4:] if host.startswith('www.') else host


def build_brief(engine_data, articles_by_title, per_section=PER_SECTION):
    """Sections in tile order: [{'name', 'items': [{title, url, source, summary}]}].

    Tiles with no stories are left out rather than printed empty.
    """
    sections = []
    for engine_id in TILE_ORDER:
        engine = engine_data.get(engine_id) or {}
        articles = engine.get('articles') or []
        if engine.get('comingSoon') or not articles:
            continue
        items = []
        for article in articles[:per_section]:
            raw = articles_by_title.get(article['title']) or {}
            items.append({
                'title': article['title'],
                'url': article['url'],
                'source': source_name(article['url']),
                'summary': one_line(raw.get('summary'), article['title']),
            })
        sections.append({'name': engine['name'], 'items': items})
    return sections


def render_brief(sections, data_date=None, updated_at=None):
    """The brief as a complete HTML page.

    Dated by the articles' cache date, not by when it was rendered (a Sunday
    build must not stamp Friday's news as Sunday's). updated_at is when the
    articles were fetched, shown in IST; either may be None.
    """
    day = None
    if data_date:
        d = date.fromisoformat(data_date)
        day = f"{d:%A}, {d.day} {d:%B %Y}"
    return _env.get_template('brief.html').render(
        sections=sections,
        day=day,
        updated=updated_at.astimezone(IST).strftime('%H:%M IST') if updated_at else None,
        total=sum(len(s['items']) for s in sections),
    )
