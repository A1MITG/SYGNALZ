"""Shared Command Center engine mapping.

Single source of truth for turning scored Signals into the Command Center's
{engineId: {name, articles, comingSoon}} contract.

Both surfaces read from here so they cannot drift apart:
  - app/api/routes.py          -> GET /api/command-center   (live)
  - scripts/build_command_center_data.py -> public/command_center_data.json (static build)

Signal name -> (Command Center engine id, display name). Signal Executive is
intentionally unmapped: the Command Center layout has no tile for it.
"""
import logging
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SIGNAL_TO_ENGINE = {
    'Signal Global': ('global', 'Global Affairs'),
    'Signal Business': ('economy', 'Economy, Business & Markets'),
    'Signal AI': ('ai', 'AI, Technology & Innovation'),
    'Signal GCC': ('gcc', 'GCC & Enterprise Technology'),
    'Signal Insurance': ('insurance', 'Insurance & Financial Services'),
    # Tile-only signals (signals.TILE_SIGNALS): scored only for the Command
    # Center, which calls synthesize_signals(include_tile_signals=True).
    'Signal Banking': ('banking', 'Banking'),
    'Signal Energy': ('energy', 'Energy'),
    'Signal Defence': ('defence', 'Defence'),
    'Signal Healthcare': ('healthcare', 'Healthcare'),
    'Signal Cyber': ('cyber', 'Cyber Intelligence'),
    'Signal Climate': ('climate', 'Climate & Sustainability'),
    'Signal Telecom': ('telecom', 'Telecom & Digital Infrastructure'),
    'Signal Supply Chain': ('supplychain', 'Supply Chain'),
    'Signal Manufacturing': ('manufacturing', 'Manufacturing'),
}

# Engines with no scoring logic yet — written out honestly, not fabricated.
COMING_SOON_ENGINES = {
}

COMING_SOON_NOTE = (
    "No scoring engine built for this domain yet — it isn't classifying "
    "real articles today. Planned as a future configurable Intelligence Domain."
)


# Featured Analysis priority: the audience's own sectors first (GCC, then
# Insurance, which covers insurtech), otherwise the biggest global story.
FEATURED_ORDER = ('gcc', 'insurance', 'global')


# Words that are never part of a person's name. The Sprint 5 extractor keys
# off a role term followed by capitalised words ("CEO Jane Doe"), so a
# headline like "Chairman Stirs Controversy" yields "Stirs Controversy" and
# "CEO Designate Tewolde Gebremariam" keeps the qualifier. Those are fine as a
# ranking signal, but Executive Pulse prints the name under a photograph of a
# real person, so a wrong one is the exact defect this pipeline exists to
# remove. Anything here disqualifies the extraction outright.
_NOT_NAME_WORDS = frozenset("""
stirs opposed backs weighs says said lauds announces launches urges warns
calls plans seeks sees expects steps names appoints elects joins quits
resigns designate elect nominee interim acting incoming outgoing deputy
former ex chief executive officer chairman chairwoman president director
""".split())


def _looks_like_a_person(name):
    """True when every token could plausibly belong to a person's name."""
    tokens = name.split()
    if not (2 <= len(tokens) <= 3):
        return False
    for token in tokens:
        bare = token.strip('.').lower()
        if bare in _NOT_NAME_WORDS:
            return False
        if not token[:1].isupper():
            return False
    return True


# --- Supplementary headline patterns for Executive Pulse -------------------
#
# app/intelligence/entities.py matches three shapes: "CEO Jane Doe",
# "Jane Doe, chief executive" and "names Jane Doe as CFO". Real leadership
# headlines carry others, and each miss costs this row a card:
#
#   "Helmsman names Emily Drew president, CEO"   -> no "as" before the role
#   "Who is Karandeep Anand? The Character.AI CEO ..."
#   "Warren Buffett steps down as Berkshire chair" -> role follows the verb
#
# These run only for this row, so the shared Sprint 5 extractor (and the 18
# tests that pin its behaviour) stays untouched.

_PULSE_NAME = r"[A-Z][a-z]+(?:\s+[A-Z]\.?)?(?:\s+[A-Z][a-z]+){1,2}"

_PULSE_ROLE = (
    r"chief\s+executive\s+officer|chief\s+executive|chief\s+financial\s+officer|"
    r"chief\s+technology\s+officer|chief\s+operating\s+officer|managing\s+director|"
    r"chairman|chairwoman|chairperson|chair|president|ceo|cfo|cto|coo|cio|"
    r"co-founder|founder|deputy\s+governor|governor|minister|secretary"
)

# A role word must appear somewhere in the headline for any of these to fire:
# "Who is <Name>?" alone matches athletes and celebrities just as happily.
_PULSE_HAS_ROLE = re.compile(_PULSE_ROLE, re.IGNORECASE)

# Case-sensitivity is load-bearing, exactly as it is in entities.py: a global
# re.IGNORECASE turns _PULSE_NAME's [A-Z][a-z]+ into "any word", so the greedy
# {1,2} swallows the role that follows ("Emily Drew president"). The verbs and
# roles are therefore made case-insensitive with scoped (?i:...) groups while
# the name pattern stays case-sensitive.
_PULSE_PATTERNS = (
    # "names/appoints/promotes <Name> president" (with or without "as")
    re.compile(
        r"(?i:(?:names?|named|appoints?|appointed|elects?|elected|promotes?|promoted))\s+"
        r"(?P<name>" + _PULSE_NAME + r")\s*,?\s*(?i:(?:as\s+)?(?:the\s+)?)"
        r"(?i:" + _PULSE_ROLE + r")"
    ),
    # "Who is <Name>?" — profile pieces, gated by _PULSE_HAS_ROLE above.
    re.compile(r"(?i:who\s+is\s+)(?P<name>" + _PULSE_NAME + r")\b"),
    # "<Name> steps down / resigns / to succeed ..."
    re.compile(
        r"\b(?P<name>" + _PULSE_NAME + r")\s+"
        r"(?i:steps?\s+down|stepping\s+down|resigns?|resigned|quits?|retires?|"
        r"to\s+succeed|succeeds)\b"
    ),
)


def _names_from_headline(title):
    """Leader names this headline states outright, for shapes entities.py misses."""
    if not _PULSE_HAS_ROLE.search(title):
        return []
    out = []
    for rx in _PULSE_PATTERNS:
        for m in rx.finditer(title):
            name = (m.group("name") or "").strip()
            if name and name not in out:
                out.append(name)
    return out


def _is_the_subject(name, title):
    """True when the headline is about this person, not merely near them.

    Matching the full name is too strict: "Buffett Steps Down as Berkshire
    Chair" is entirely about Warren Buffett but never spells out his first
    name. Matching the surname alone is enough, because the name was already
    extracted from this headline next to a role term — the surname check only
    decides whether the headline, rather than the summary, is where it sits.
    """
    surname = name.split()[-1].lower()
    pattern = r"\b" + re.escape(surname) + r"\b"
    return re.search(pattern, title.lower()) is not None


# Executive Pulse is for business leaders, so the role that qualifies a card
# must be a corporate one. A bare "president" is not enough on its own — it
# is how "President Donald Trump" in a summary reached the row — and the
# political titles _PULSE_ROLE also matches (governor, minister, secretary)
# never qualify here.
_CORPORATE_ROLE = re.compile(
    r"\b(?:chief\s+\w+(?:\s+\w+)?\s+officer|chief\s+executive|managing\s+director|"
    r"chairman|chairwoman|chairperson|chair|ceo|cfo|cto|coo|cio|cro|co-founder|founder)\b",
    re.IGNORECASE,
)


def _mentions_surname(name, texts):
    surname = r"\b" + re.escape(name.split()[-1].lower()) + r"\b"
    return any(re.search(surname, t.lower()) for t in texts)


def build_pulse_cards(current_articles, limit=8, exclude_urls=(), exclude_names=(), exclude_text=()):
    """Executive Pulse: business leaders who are the subject of today's news.

    Was four hardcoded cards — name, photo and hand-written commentary baked
    into the HTML, each stamped "today" while pointing at July 2026 stories.

    Scans the whole corpus rather than the ~40 articles already chosen for
    tiles: a leader story worth showing here often is not the top-scoring
    article in its Signal.

    A card is only built when the story names an executive in a corporate role
    AND the headline is about them AND the article carries a real photograph,
    because the layout puts the person's name over the image as the subject of
    the story. No qualifying leader today means an empty row, which is the
    honest outcome the section's own note already promises.

    Each person appears once on the page, in the most specific row: the
    exclude_* arguments carry what People Movers (its stories and headlines)
    and Leaders on Record (its leaders) already show. See build_people_rows.
    """
    from ..intelligence.entities import extract
    from ..intelligence.freshness import classify
    from ..intelligence.normalize import parse_date

    seen_names = {n.lower() for n in exclude_names}
    skip_urls = set(exclude_urls)
    cards = []
    for raw in current_articles:
        # The caller passes the raw corpus, which still holds whatever a feed
        # served; gate here so a dead feed cannot put a years-old leader story
        # under a "today" stamp.
        if not classify(parse_date(raw.get('date'))).get('is_current'):
            continue
        if raw.get('url', '') in skip_urls:
            continue
        image = (raw.get('image') or '').strip()
        if not image.startswith('http'):
            continue
        title = (raw.get('title') or '').strip()
        if not title:
            continue
        # Read the summary too: a headline often carries the surname alone
        # ("Buffett Steps Down as Berkshire Chair") with no role term beside
        # it, so a title-only pass misses the person entirely. _is_the_subject
        # below is what keeps an incidental mention out.
        found = extract(title, raw.get('summary', ''))
        candidates = [(p.get('value', '').strip(), p.get('role', ''))
                      for p in found.get('executives', [])
                      if _CORPORATE_ROLE.search(p.get('role', '') or '')]
        if _CORPORATE_ROLE.search(title):
            candidates += [(n, '') for n in _names_from_headline(title)]
        for name, role in candidates:
            if not name or not _looks_like_a_person(name):
                continue
            if not _is_the_subject(name, title):
                continue
            if name.lower() in seen_names or _mentions_surname(name, exclude_text):
                continue
            seen_names.add(name.lower())
            cards.append({
                'name': name,
                'role': role,
                'title': title,
                'url': raw.get('url', ''),
                'image': image,
                'source': _publisher(raw.get('url', '')),
            })
            break
        if len(cards) >= limit:
            break
    return cards


def _publisher(url):
    """The publisher's name for the card's meta line ("Mint", not "livemint")."""
    from ..intelligence.normalize import publisher_for
    try:
        host = url.split('/')[2]
    except IndexError:
        return ''
    return publisher_for(host)


def _publisher_of(raw):
    """_publisher, except that a news-search result names its own publisher:
    its link is the search engine's (app/scraper/news_search.py)."""
    from ..scraper.news_search import is_search_link
    if is_search_link(raw.get('url')) and raw.get('source'):
        return raw['source']
    return _publisher(raw.get('url', ''))


# People Movers is for business leaders. A headline about a head of state,
# a minister, a lawmaker or a party figure is left out unless it also names a
# corporate role ("Minister names new Air India CEO" stays). "President"
# alone is ambiguous (a company president), so it counts as political only
# beside the context that says so: elections, protests, parliament, a party.
_POLITICAL_OFFICE = re.compile(
    r"\b(?:prime\s+minister|chief\s+minister|deputy\s+minister|minister|ministers|"
    r"head\s+of\s+(?:state|government)|opposition\s+leader|leader\s+of\s+(?:the\s+)?opposition|"
    r"lawmakers?|senators?|congress(?:man|woman)|mps?|mlas?|mayor|speaker\s+of|"
    r"cabinet|parliament\w*|party\s+(?:leader|chief|president)|premier|monarch|dictator|junta)\b",
    re.IGNORECASE,
)
_POLITICAL_PRESIDENT = re.compile(r"\bpresident\b", re.IGNORECASE)
_POLITICAL_CONTEXT = re.compile(
    r"\b(?:elections?|electoral|polls?|protests?|protesters|populist|parliament\w*|opposition|coup|"
    r"impeach\w*|referendum|ruling\s+party|voters|regime)\b",
    re.IGNORECASE,
)


def _is_political(title):
    """True when a move headline is about a politician rather than a business leader."""
    if _CORPORATE_ROLE.search(title):
        return False
    if _POLITICAL_OFFICE.search(title):
        return True
    return bool(_POLITICAL_PRESIDENT.search(title) and _POLITICAL_CONTEXT.search(title))


_MOVE_LABELS = {'EXECUTIVE_APPOINTMENT': 'Appointed', 'EXECUTIVE_EXIT': 'Steps down'}
_APPOINT_WORDS = re.compile(r"\b(?:named|names|appoint\w*|promot\w*|joins|succeed\w*|elected)\b", re.IGNORECASE)


def build_movers(current_articles, limit=8):
    """People Movers: appointments and exits that today's headlines state.

    The Sprint 6 event detector finds the candidates; this row additionally
    requires the detector's evidence to sit in the headline. Summary evidence
    is often background rather than a move (a lawsuit that "names OpenAI and
    its CEO" fired the detector until BP-25 vetoed legal contexts), and a row
    called People Movers has to be right every time it shows a card.

    Politicians are left out (_is_political): the row is for the business
    and CXO audience. `limit=None` returns every move, for build_people_rows
    to de-duplicate before it cuts the row.
    """
    from ..intelligence import events
    from ..intelligence.freshness import classify
    from ..intelligence.normalize import parse_date

    moves, seen = [], set()
    for raw in current_articles:
        when = parse_date(raw.get('date'))
        if not classify(when).get('is_current'):
            continue
        title = (raw.get('title') or '').strip()
        url = raw.get('url', '')
        if not title or not url or url in seen or _is_political(title):
            continue
        found = [e for e in events.detect(title, raw.get('summary', ''))
                 if e['type'] in _MOVE_LABELS and any(x['where'] == 'title' for x in e['evidence'])]
        if not found:
            continue
        types = {e['type'] for e in found}
        # "X named president as Y steps down" is one change, not only an exit.
        if 'EXECUTIVE_EXIT' in types and _APPOINT_WORDS.search(title):
            types.add('EXECUTIVE_APPOINTMENT')
        label = 'Leadership change' if len(types) > 1 else _MOVE_LABELS[next(iter(types))]
        seen.add(url)
        moves.append({
            'title': title,
            'url': url,
            'type': label,
            'kind': 'exit' if label == 'Steps down' else 'appointment',
            'source': _publisher_of(raw),
            'date': when.strftime('%d %b') if when else '',
            'ts': when.timestamp() if when else 0,
            'image': _article_image(raw),
        })
    moves.sort(key=lambda m: m['ts'], reverse=True)
    return moves if limit is None else moves[:limit]


PINNED_MOVES = Path(__file__).resolve().parents[2] / 'config' / 'pinned_moves.yaml'
_IST = timezone(timedelta(hours=5, minutes=30))


def load_pinned_moves(path=PINNED_MOVES, today=None):
    """People Movers pins from config/pinned_moves.yaml that are still in date.

    A pin keeps a reported move on the row past the 7-day window until its
    `until` date (IST, inclusive), then it drops off by itself. The card keeps
    the report's real date and carries pinned=True, which the page shows as
    "Pinned", so an older story never passes as today's. An unreadable file or
    an incomplete entry is skipped rather than failing the build.
    """
    from ..intelligence.normalize import publisher_for
    try:
        entries = (yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}).get('pins') or []
    except (OSError, yaml.YAMLError):
        logger.warning("Pinned moves unreadable: %s", path, exc_info=True)
        return []
    today = today or datetime.now(_IST).date()
    labels = set(_MOVE_LABELS.values()) | {'Leadership change'}
    pins = []
    for entry in entries:
        title = (entry.get('title') or '').strip()
        url = (entry.get('url') or '').strip()
        until, published = entry.get('until'), entry.get('published')
        if not title or not url.startswith('http') or not isinstance(until, date) or until < today:
            continue
        label = entry.get('type') if entry.get('type') in labels else 'Appointed'
        when = (datetime(published.year, published.month, published.day, tzinfo=timezone.utc)
                if isinstance(published, date) else None)
        pins.append({
            'title': title,
            'url': url,
            'type': label,
            'kind': 'exit' if label == 'Steps down' else 'appointment',
            'source': entry.get('source') or publisher_for(url.split('/')[2]),
            'date': when.strftime('%d %b') if when else '',
            'ts': when.timestamp() if when else 0,
            # Optional; without one, the surfaces fill it from the report's page.
            'image': _article_image(entry),
            'pinned': True,
        })
    return pins


PINNED_STORIES = Path(__file__).resolve().parents[2] / 'config' / 'pinned_stories.yaml'


def load_pinned_stories(path=PINNED_STORIES, today=None):
    """Engine tile pins from config/pinned_stories.yaml that are still in date.

    A pin keeps a published story at the front of its tile after the feeds
    stop listing it, until its `until` date (IST, inclusive), then it drops
    off by itself. It keeps the story's real date and carries pinned=True;
    one marked featured is also the Featured Analysis card (build_featured).
    An unreadable file or an incomplete entry is skipped rather than failing
    the build.
    """
    from ..intelligence.normalize import publisher_for
    try:
        entries = (yaml.safe_load(Path(path).read_text(encoding='utf-8')) or {}).get('pins') or []
    except (OSError, yaml.YAMLError):
        logger.warning("Pinned stories unreadable: %s", path, exc_info=True)
        return []
    today = today or datetime.now(_IST).date()
    engines = {engine_id for engine_id, _ in SIGNAL_TO_ENGINE.values()}
    pins = []
    for entry in entries:
        title = (entry.get('title') or '').strip()
        url = (entry.get('url') or '').strip()
        until, published = entry.get('until'), entry.get('published')
        if (not title or not url.startswith('http') or entry.get('engine') not in engines
                or not isinstance(until, date) or until < today):
            continue
        pins.append({
            'engine': entry['engine'],
            'title': title,
            'url': url,
            'image': _article_image(entry),
            'summary': (entry.get('summary') or '').strip(),
            'source': entry.get('source') or publisher_for(url.split('/')[2]),
            'date': published.strftime('%d %b') if isinstance(published, date) else '',
            'featured': entry.get('featured') is True,
            'pinned': True,
        })
    return pins


def build_record(quote_set, movers):
    """Leaders on Record, minus anyone People Movers already shows.

    ``quote_set`` is app.scraper.leader_quotes.get_leader_quotes(). A move is
    the more specific fact about a person, so it wins.
    The same words reported by several outlets are shown once, from the most
    trusted of them (BP-51).
    """
    from .dedupe import fold, keep_most_trusted

    def same_quote(a, b):
        return (a['leader'] == b['leader']
                and re.sub(r'\W+', ' ', fold(a['quote']))[:80] == re.sub(r'\W+', ' ', fold(b['quote']))[:80])

    titles = [m['title'] for m in movers]
    record = dict(quote_set)
    quotes = [q for q in quote_set.get('quotes', [])
              if not _mentions_surname(q['leader'], titles)]
    record['quotes'] = keep_most_trusted(quotes, same=same_quote)
    return record


def build_people_rows(current_articles, quote_set, pulse_limit=8, pinned=(), movers_limit=8):
    """The three people rows, each person shown once, in the most specific row.

    Precedence is People Movers > Leaders on Record > Executive Pulse: a move
    is a fact about the person, a quote is their own voice, and Pulse is the
    general "in the news" catch-all, so it takes whoever the others did not.

    `pinned` (from load_pinned_moves) leads People Movers; a pinned story the
    scan also found is shown once, as the pin. Any story several outlets
    carry is shown once, from the most trusted of them (dedupe.py).
    """
    from .dedupe import keep_most_trusted
    movers = keep_most_trusted(list(pinned) + build_movers(current_articles, limit=None))[:movers_limit]
    record = build_record(quote_set, movers)
    pulse = build_pulse_cards(
        current_articles, limit=pulse_limit,
        exclude_urls={m['url'] for m in movers},
        exclude_names={q['leader'] for q in record['quotes']},
        exclude_text=[m['title'] for m in movers],
    )
    pulse = keep_most_trusted(pulse)
    return {'_movers': movers, '_record': record, '_pulse': pulse}


FEATURED_SUMMARY = 260  # characters of summary on the Featured card


def _clip(text, limit):
    """Text cut to at most `limit` characters at a word boundary, with "…"."""
    text = ' '.join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit - 1].rsplit(' ', 1)[0].rstrip(',;:') + '…'


FEATURED_LIMIT = 6  # cards in the Featured Analysis rotation


def build_features(engine_data, articles_by_title, limit=FEATURED_LIMIT):
    """The Featured Analysis rotation, in order.

    Was one hardcoded article (a July 2026 GCC office-leasing piece), then one
    card. Stories pinned with `featured: true` (config/pinned_stories.yaml)
    come first and say so. Then the current stories with a full-size picture
    (a feed thumbnail does not count) in FEATURED_ORDER: GCC's first, then
    Insurance's, then the top Global Affairs stories, so a thin GCC day still
    fills the rotation. Every card names its publisher and date.
    """
    from ..intelligence.normalize import parse_date
    from ..scraper.article_images import is_thumbnail

    features = []
    # 1. Pins: they carry their own picture, summary, publisher and date.
    for engine_id, engine in engine_data.items():
        if engine_id.startswith('_'):
            continue
        for pin in engine.get('articles') or []:
            if pin.get('pinned') and pin.get('featured') and pin.get('image'):
                features.append({
                    'title': pin['title'], 'url': pin['url'], 'image': pin['image'],
                    'summary': _clip(pin.get('summary') or '', FEATURED_SUMMARY),
                    'label': engine.get('name', ''),
                    'source': pin.get('source', ''), 'date': pin.get('date', ''),
                    'pinned': True,
                })
    # 2. The day's stories with a full-size picture, GCC's first.
    seen = {f['url'] for f in features} | {f['title'] for f in features}
    for engine_id in FEATURED_ORDER:
        engine = engine_data.get(engine_id) or {}
        for article in engine.get('articles', []):
            if len(features) >= limit:
                return features
            if article['url'] in seen or article['title'] in seen:
                continue
            raw = articles_by_title.get(article['title']) or {}
            image = (raw.get('image') or '').strip()
            if not image.startswith('http') or is_thumbnail(image):
                continue
            when = parse_date(raw.get('date'))
            features.append({
                'title': article['title'], 'url': article['url'], 'image': image,
                'summary': _clip(raw.get('summary') or '', FEATURED_SUMMARY),
                'label': engine.get('name', ''),
                'source': _publisher(article['url']),
                'date': when.astimezone(_IST).strftime('%d %b') if when else '',
            })
            seen.add(article['url'])
    return features[:limit]


def build_featured(engine_data, articles_by_title):
    """The first Featured Analysis card (build_features), or None."""
    features = build_features(engine_data, articles_by_title, limit=1)
    return features[0] if features else None


def _article_image(raw):
    """An article's own image, or None.

    Only absolute http(s) urls qualify — the same test the featured and
    Pulse builders apply. A relative path or a scraper placeholder would
    render as a broken frame, and the tile's vector is the better answer.
    """
    image = ((raw or {}).get('image') or '').strip()
    return image if image.startswith('http') else None


def build_engine_data(signals_data, articles_by_title=None, pinned=()):
    """Map a synthesize_signals() result onto the Command Center tile contract.

    ``articles_by_title`` is the raw corpus keyed by title, the same map the
    featured builder already takes. Passing it attaches each article's
    own image so a tile can show the picture belonging to the headline it is
    currently cycling, instead of one baked-in photo sitting under all five.
    It stays optional: callers that only need titles and urls (and the tests
    that pin this contract) may omit it, and every article then reports no
    image, which the page renders as that domain's vector.

    ``pinned`` (from load_pinned_stories) leads its engine's tile; a pinned
    story the scan also found is shown once, as the pin. Pins count toward
    the tile's MAX_PER_SIGNAL.

    A story several outlets carry appears once on the page, in one tile, as
    its most trusted copy (dedupe.py); Featured and the Brief read these
    tiles, so they inherit it.
    """
    from .dedupe import keep_most_trusted
    from .signals import MAX_PER_SIGNAL
    by_name = {s['name']: s for s in signals_data.get('signals', [])}
    by_title = articles_by_title or {}

    engine_data = {}
    for signal_name, (engine_id, display_name) in SIGNAL_TO_ENGINE.items():
        signal = by_name.get(signal_name, {})
        articles = [
            {'title': a['title'], 'url': a['url'],
             'image': _article_image(by_title.get(a['title']))}
            for a in signal.get('articles', [])
        ]
        pins = [{k: v for k, v in p.items() if k != 'engine'}
                for p in pinned if p['engine'] == engine_id]
        if pins:
            seen = {p['url'] for p in pins} | {p['title'] for p in pins}
            articles = (pins + [a for a in articles
                                if a['url'] not in seen and a['title'] not in seen])[:MAX_PER_SIGNAL]
        engine_data[engine_id] = {'name': display_name, 'articles': articles}

    everything = [(engine_id, a) for engine_id, e in engine_data.items() for a in e['articles']]
    kept = keep_most_trusted([dict(a, _at=i) for i, (_, a) in enumerate(everything)])
    kept_at = {a['_at'] for a in kept}
    for engine in engine_data.values():
        engine['articles'] = []
    for i, (engine_id, article) in enumerate(everything):
        if i in kept_at:
            engine_data[engine_id]['articles'].append(article)

    for engine_id, display_name in COMING_SOON_ENGINES.items():
        engine_data[engine_id] = {
            'name': display_name,
            'comingSoon': True,
            'note': COMING_SOON_NOTE,
        }

    return engine_data
